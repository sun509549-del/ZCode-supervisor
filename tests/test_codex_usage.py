import argparse
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.zcode_eval.codex_context_intake import (
    build_context_intake_record,
    infer_image_arg_style,
    parse_tool_context_intake,
    validate_image_arg_order,
)
from tools.zcode_eval.codex_usage import append_jsonl_record, parse_codex_exec_jsonl
from tools.zcode_eval.metrics import metric_invariant_violations, token_metric_metadata
from tools.zcode_eval.token_reduction import build_token_reduction_summary
from tools.zcode_eval.zcode_eval import main


def write_jsonl(path: Path, rows: list[object], *, malformed: bool = False) -> None:
    lines = [json.dumps(row) for row in rows]
    if malformed:
        lines.insert(1, "{malformed")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class CodexUsageLedgerTests(unittest.TestCase):
    def test_parse_normal_codex_jsonl_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            write_jsonl(
                path,
                [
                    {"type": "thread.started"},
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 100,
                            "cached_input_tokens": 40,
                            "output_tokens": 20,
                            "reasoning_output_tokens": 5,
                        },
                    },
                ],
            )

            result = parse_codex_exec_jsonl([path])

            self.assertFalse(result.usage["usage_missing"])
            self.assertEqual(result.usage["uncached_input_tokens"], 60)
            self.assertEqual(result.usage["uncached_plus_reasoning"], 65)
            self.assertEqual(result.usage["effective_codex_work"], 85)
            self.assertEqual(result.events["event_counts"]["turn.completed"], 1)
            self.assertEqual(metric_invariant_violations(result.usage), [])

    def test_metric_metadata_names_effective_formula(self):
        meta = token_metric_metadata()
        self.assertEqual(meta["primary_metric"], "effective_codex_work")
        self.assertEqual(
            meta["primary_metric_formula"],
            "uncached_input_tokens + output_tokens + reasoning_output_tokens",
        )
        self.assertIn("uncached_plus_reasoning", meta["secondary_metrics"])

    def test_parse_sums_multiple_turn_completed_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            write_jsonl(
                path,
                [
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 100,
                            "cached_input_tokens": 10,
                            "output_tokens": 20,
                            "reasoning_output_tokens": 5,
                        },
                    },
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 50,
                            "cached_input_tokens": 20,
                            "output_tokens": 10,
                            "reasoning_output_tokens": 3,
                        },
                    },
                ],
            )

            result = parse_codex_exec_jsonl([path])

            self.assertEqual(result.usage["input_tokens"], 150)
            self.assertEqual(result.usage["cached_input_tokens"], 30)
            self.assertEqual(result.usage["output_tokens"], 30)
            self.assertEqual(result.usage["reasoning_output_tokens"], 8)
            self.assertEqual(result.usage["effective_codex_work"], 158)
            self.assertEqual(result.usage["turn_completed_usage_count"], 2)

    def test_parse_records_missing_malformed_failed_error_and_unknown_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            write_jsonl(
                path,
                [
                    {"type": "turn.completed"},
                    {"type": "turn.failed", "error": "boom"},
                    {"type": "error", "message": "bad"},
                    {"type": "future.event"},
                ],
                malformed=True,
            )

            result = parse_codex_exec_jsonl([path])

            self.assertTrue(result.usage["usage_missing"])
            self.assertIsNone(result.usage["input_tokens"])
            self.assertIsNone(result.usage["effective_codex_work"])
            self.assertEqual(result.events["malformed_jsonl_line_count"], 1)
            self.assertEqual(result.events["turn_failed_count"], 1)
            self.assertEqual(result.events["error_count"], 1)
            self.assertEqual(result.events["unknown_event_count"], 1)

    def test_partial_usage_event_marks_metrics_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            write_jsonl(path, [{"type": "turn.completed", "usage": {"input_tokens": 10}}])

            result = parse_codex_exec_jsonl([path])

            self.assertTrue(result.usage["usage_missing"])
            self.assertTrue(result.usage["usage_anomaly"])
            self.assertEqual(result.usage["turn_completed_usage_count"], 1)
            self.assertIsNone(result.usage["effective_codex_work"])

    def test_cached_tokens_larger_than_input_is_safe_anomaly(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            write_jsonl(
                path,
                [
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 10,
                            "cached_input_tokens": 20,
                            "output_tokens": 3,
                            "reasoning_output_tokens": 7,
                        },
                    }
                ],
            )

            result = parse_codex_exec_jsonl([path])

            self.assertEqual(result.usage["uncached_input_tokens"], 0)
            self.assertEqual(result.usage["effective_codex_work"], 10)
            self.assertTrue(result.usage["usage_anomaly"])

    def test_capture_hashes_prompt_without_persisting_prompt_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "ledger.jsonl"
            raw = root / "events.jsonl"
            prompt = "secret-ish task text that should not persist"
            write_jsonl(
                raw,
                [
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 12,
                            "cached_input_tokens": 2,
                            "output_tokens": 3,
                            "reasoning_output_tokens": 4,
                        },
                    }
                ],
            )

            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = main(
                    [
                        "capture-codex-exec",
                        "--ledger",
                        str(ledger),
                        "--experiment-id",
                        "exp",
                        "--task-id",
                        "task",
                        "--mode",
                        "codex_only",
                        "--run-kind",
                        "measurement",
                        "--phase",
                        "plan",
                        "--attempt-index",
                        "1",
                        "--raw-jsonl",
                        str(raw),
                        "--prompt-text",
                        prompt,
                        "--failed",
                        "false",
                        "--routing-reason-code",
                        "background_safe",
                        "--routing-expected-codex-tokens",
                        "123",
                    ]
                )

            self.assertEqual(exit_code, 0)
            text = ledger.read_text(encoding="utf-8")
            self.assertNotIn(prompt, text)
            record = json.loads(text)
            self.assertEqual(record["primary_metric"], "effective_codex_work")
            self.assertIsNotNone(record["io_visibility"]["prompt_sha256"])
            self.assertEqual(record["io_visibility"]["prompt_bytes"], len(prompt.encode("utf-8")))
            self.assertFalse(record["failure"]["failed"])
            self.assertEqual(record["canonical_mode"], "codex_only")
            self.assertEqual(record["routing_decision"]["selected"], "codex_only")
            self.assertEqual(record["routing_decision"]["reason_codes"], ["background_safe"])
            self.assertEqual(record["routing_decision"]["expected_codex_tokens"], 123)
            self.assertEqual(record["operator_total_usage"]["effective_codex_work"], 17)
            self.assertEqual(record["benchmark_clean_usage"]["effective_codex_work"], 17)

    def test_capture_contract_phase_keeps_missing_usage_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "ledger.jsonl"
            raw = root / "events.jsonl"
            write_jsonl(raw, [{"type": "turn.completed"}])

            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = main(
                    [
                        "capture-codex-exec",
                        "--ledger",
                        str(ledger),
                        "--experiment-id",
                        "strict-v3",
                        "--task-id",
                        "billing-credit-contract",
                        "--mode",
                        "zcode_nested_codex",
                        "--run-kind",
                        "measurement",
                        "--phase",
                        "deterministic_acceptance",
                        "--attempt-index",
                        "1",
                        "--raw-jsonl",
                        str(raw),
                        "--failed",
                        "false",
                    ]
                )

            self.assertEqual(exit_code, 0)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(record["phase"], "deterministic_acceptance")
            self.assertTrue(record["usage"]["usage_missing"])
            self.assertIsNone(record["usage"]["effective_codex_work"])

    def test_capture_records_excluded_attempt_without_zeroing_operator_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "ledger.jsonl"
            raw = root / "events.jsonl"
            write_jsonl(
                raw,
                [
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 20,
                            "cached_input_tokens": 5,
                            "output_tokens": 2,
                            "reasoning_output_tokens": 3,
                        },
                    }
                ],
            )

            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = main(
                    [
                        "capture-codex-exec",
                        "--ledger",
                        str(ledger),
                        "--experiment-id",
                        "exp",
                        "--task-id",
                        "task",
                        "--mode",
                        "zcode_direct_harness",
                        "--run-kind",
                        "measurement",
                        "--phase",
                        "validation_audit",
                        "--attempt-index",
                        "2",
                        "--raw-jsonl",
                        str(raw),
                        "--invalid-measurement",
                        "--exclusion-reason",
                        "harness_bug",
                        "--rerun-reason",
                        "fixed_harness",
                    ]
                )

            self.assertEqual(exit_code, 0)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertFalse(record["attempt_valid_for_benchmark"])
            self.assertTrue(record["invalid_measurement"])
            self.assertIsNone(record["benchmark_clean_usage"])
            self.assertEqual(record["operator_total_usage"]["effective_codex_work"], 20)
            self.assertEqual(record["excluded_attempt_usage"]["effective_codex_work"], 20)

    def test_append_jsonl_record_appends_complete_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.jsonl"

            append_jsonl_record(ledger, {"run_id": "a"})
            append_jsonl_record(ledger, {"run_id": "b"})

            rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["run_id"] for row in rows], ["a", "b"])

    def test_summary_aggregates_two_modes_and_two_phases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "ledger.jsonl"
            summary = root / "summary.md"
            rows = [
                {
                    "schema_version": "codex_usage_ledger.v1",
                    "task_id": "task-a",
                    "mode": "codex_only",
                    "phase": "plan",
                    "attempt_index": 1,
                    "usage": {"usage_missing": False, "effective_codex_work": 10, "input_tokens": 10, "cached_input_tokens": 0, "uncached_input_tokens": 10, "output_tokens": 0, "reasoning_output_tokens": 0, "uncached_plus_reasoning": 10},
                    "quality": {"allowed_files_only": "pass", "scope_safety": "pass", "validation_result": "unknown", "artifact_quality": "unknown", "codex_repair_size": "none"},
                    "safety": {"budget_guard_hit": False},
                    "io_visibility": {},
                    "user_impact": {},
                    "failure": {"failed": False},
                },
                {
                    "schema_version": "codex_usage_ledger.v1",
                    "task_id": "task-a",
                    "mode": "zcode_direct_launcher",
                    "phase": "implementation",
                    "attempt_index": 1,
                    "usage": {"usage_missing": False, "effective_codex_work": 3, "input_tokens": 3, "cached_input_tokens": 0, "uncached_input_tokens": 3, "output_tokens": 0, "reasoning_output_tokens": 0, "uncached_plus_reasoning": 3},
                    "quality": {"allowed_files_only": "pass", "scope_safety": "pass", "validation_result": "pass", "artifact_quality": "pass", "codex_repair_size": "none"},
                    "safety": {"budget_guard_hit": False},
                    "io_visibility": {"codex_visible_launcher_bytes": 100},
                    "user_impact": {"ask_user_count": 0},
                    "failure": {"failed": False},
                },
            ]
            ledger.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = main(["summarize-codex-usage", "--ledger", str(ledger), "--out", str(summary)])

            self.assertEqual(exit_code, 0)
            text = summary.read_text(encoding="utf-8")
            self.assertIn("codex_only", text)
            self.assertIn("zcode_direct_launcher", text)
            self.assertIn("implementation", text)
            self.assertIn("total_until_success", text)
            self.assertIn("Clean Benchmark Vs Operator Total", text)
            self.assertIn("effective_codex_work", text)


class CodexContextIntakeTests(unittest.TestCase):
    def test_tool_context_parses_commands_and_tolerates_bad_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            rows = [
                {"type": "item.command_execution", "item": {"command": "git diff", "stdout": "abc", "stderr": "de"}},
                {"type": "item.plan_update"},
                {"type": "item.file_change"},
                {"type": "item.web_search"},
                {"type": "item.mcp_tool_call"},
                {"type": "future.unknown"},
            ]
            write_jsonl(path, rows, malformed=True)

            intake, events = parse_tool_context_intake([path], green_path=True, large_threshold=4)

            self.assertEqual(intake["command_count"], 1)
            self.assertEqual(intake["command_stdout_bytes_total"], 3)
            self.assertEqual(intake["command_stderr_bytes_total"], 2)
            self.assertEqual(intake["large_command_output_count"], 1)
            self.assertEqual(intake["green_path_forbidden_command_count"], 1)
            self.assertIn("git diff", intake["green_path_forbidden_commands"])
            self.assertEqual(intake["plan_update_event_count"], 1)
            self.assertEqual(intake["file_change_event_count"], 1)
            self.assertEqual(intake["web_search_event_count"], 1)
            self.assertEqual(intake["mcp_tool_event_count"], 1)
            self.assertEqual(events["malformed_jsonl_line_count"], 1)

    def test_green_path_forbidden_command_tolerates_malformed_shell_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            write_jsonl(path, [{"type": "item.command_execution", "item": {"command": "git diff '"}}])

            intake, events = parse_tool_context_intake([path], green_path=True, large_threshold=4096)

            self.assertEqual(events["malformed_jsonl_line_count"], 0)
            self.assertEqual(intake["green_path_forbidden_command_count"], 1)
            self.assertIn("git diff '", intake["green_path_forbidden_commands"])

    def test_image_argv_style_and_order_validation(self):
        self.assertEqual(infer_image_arg_style(["codex", "exec", "--image", "a.png", "--image", "b.png", "prompt"]), "repeated_flags")
        self.assertEqual(infer_image_arg_style(["codex", "exec", "--image", "a.png,b.png", "prompt"]), "comma_list")
        self.assertEqual(infer_image_arg_style(["codex", "exec", "prompt"]), "none")
        self.assertTrue(validate_image_arg_order(["codex", "exec", "--image", "a.png", "prompt"]))
        self.assertFalse(validate_image_arg_order(["codex", "exec", "prompt", "--image", "a.png"]))

    def test_context_intake_hashes_prompt_counts_stdin_and_marks_raw_jsonl_sensitive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "events.jsonl"
            stdin = root / "stdin.txt"
            ledger = root / "context.jsonl"
            prompt = "do not persist this prompt"
            raw.write_text(
                json.dumps({"type": "item.command_execution", "item": {"command": "pytest", "stdout": "ok"}}) + "\n",
                encoding="utf-8",
            )
            raw.chmod(0o600)
            stdin.write_text("stdin payload", encoding="utf-8")

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "capture-codex-context-intake",
                        "--ledger",
                        str(ledger),
                        "--experiment-id",
                        "exp",
                        "--task-id",
                        "vision-card-latest",
                        "--canonical-mode",
                        "zcode_direct_launcher",
                        "--phase",
                        "acceptance",
                        "--attempt-index",
                        "1",
                        "--raw-jsonl",
                        str(raw),
                        "--prompt-text",
                        prompt,
                        "--stdin-file",
                        str(stdin),
                        "--prompt-source",
                        "stdin",
                        "--codex-argv-json",
                        json.dumps(["codex", "exec", "--image", "card.png", "prompt"]),
                        "--green-path",
                    ]
                )

            self.assertEqual(exit_code, 0)
            text = ledger.read_text(encoding="utf-8")
            self.assertNotIn(prompt, text)
            record = json.loads(text)
            self.assertEqual(record["display_mode"], "zcode_delegated")
            self.assertEqual(record["prompt"]["prompt_bytes"], len(prompt.encode("utf-8")))
            self.assertEqual(record["stdin"]["stdin_bytes"], len("stdin payload".encode("utf-8")))
            self.assertEqual(record["argv"]["image_arg_style"], "repeated_flags")
            self.assertTrue(record["argv"]["harness_arg_order_validated"])
            self.assertEqual(record["safety"]["raw_jsonl_sensitivity"], "sensitive")
            self.assertEqual(record["safety"]["raw_jsonl_file_mode"], "0600")
            self.assertFalse(record["safety"]["raw_jsonl_gitignored"])
            self.assertEqual(record["tool_context_intake"]["green_path_forbidden_command_count"], 1)

    def test_context_intake_schema_file_has_required_keys(self):
        schema = json.loads(Path("tools/zcode_eval/schemas/codex_context_intake.schema.json").read_text(encoding="utf-8"))
        self.assertIn("prompt", schema["required"])
        self.assertIn("tool_context_intake", schema["required"])
        self.assertIn("zcode_direct_harness", schema["properties"]["canonical_mode"]["enum"])
        self.assertIn("human_display_mode", schema["properties"])
        self.assertIn("mode_aliases", schema["properties"])

    def test_build_context_intake_record_budget_layers(self):
        namespace = argparse.Namespace(
            workspace=Path("."),
            canonical_mode="codex_only",
            mode_detail=None,
            run_id="run",
            prompt_text="x" * 20,
            prompt_file=None,
            stdin_file=None,
            codex_argv_json=None,
            codex_argv=[],
            image_arg_style=None,
            image=[],
            raw_jsonl=[],
            green_path=False,
            experiment_id="exp",
            task_id="task",
            run_kind="measurement",
            phase="plan",
            attempt_index=1,
            usage_ledger_run_id=None,
            template_id=None,
            template_file=None,
            prompt_source="argument",
            image_resent_to_acceptance=False,
            codex_cli_version=None,
            model_arg=None,
            resolved_model=None,
            reasoning_effort=None,
            sandbox=None,
            approval_mode=None,
            profile=None,
            ignore_user_config=None,
            ignore_rules=None,
            web_search_mode=None,
            output_schema_path=None,
            run_order_index=None,
            run_order_seed=None,
            cache_condition=None,
            previous_run_id=None,
            feature_flag=[],
            agents_bytes_shown=0,
            rules_bytes_shown=0,
            config_bytes_shown=0,
            repo_hint_bytes_shown=0,
            full_repo_context_shown=False,
            zcode_manifest_bytes_shown=0,
            zcode_manifest_path=None,
            zcode_stdout_bytes_captured=0,
            zcode_stdout_log=None,
            zcode_stderr_bytes_captured=0,
            zcode_stderr_log=None,
            zcode_stdout_bytes_model_visible=0,
            zcode_stderr_bytes_model_visible=0,
            task_packet_path=None,
            diff_path=None,
            diff_captured_bytes=5000,
            diff_model_visible_bytes=2048,
            diff_excerpt_max_bytes=2048,
            diff_policy="manifest_only",
            validation_log=None,
            validation_captured_bytes=5000,
            validation_model_visible_bytes=4096,
            validation_excerpt_max_bytes=4096,
            validation_policy="error_blocks",
            prompt_max_bytes=12,
            task_packet_max_bytes=6000,
            zcode_manifest_max_bytes=4000,
            command_output_max_bytes=4096,
            image_resend_allowed=False,
            secret_scan_result="unknown",
            raw_jsonl_secret_scan_result="unknown",
            raw_jsonl_retention_days=14,
            env_policy="unknown",
            ask_user_count=0,
            manual_intervention_required=False,
            manual_repair_required=False,
        )

        record = build_context_intake_record(namespace)

        self.assertIn("prompt_max_bytes", record["visible_context_budget"]["budget_violations"])
        self.assertEqual(record["diff"]["captured_bytes_total"], 5000)
        self.assertEqual(record["diff"]["model_visible_bytes"], 2048)
        self.assertEqual(record["validation"]["captured_bytes_total"], 5000)
        self.assertEqual(record["validation"]["model_visible_bytes"], 4096)

    def test_summary_generator_aggregates_usage_and_context_ledgers(self):
        usage_records = [
            {
                "task_id": "billing",
                "mode": "zcode_direct_launcher",
                "phase": "acceptance",
                "usage": {"usage_missing": False, "effective_codex_work": 10, "input_tokens": 12, "cached_input_tokens": 4, "uncached_input_tokens": 8, "output_tokens": 1, "reasoning_output_tokens": 1, "uncached_plus_reasoning": 9, "total_in_out": 13, "total_plus_reasoning": 14},
                "quality": {"validation_result": "pass", "scope_safety": "pass"},
                "failure": {"failed": False},
                "user_impact": {"ask_user_count": 0},
            }
        ]
        context_records = [
            {
                "task_id": "billing",
                "canonical_mode": "zcode_direct_launcher",
                "phase": "acceptance",
                "prompt": {"prompt_bytes": 5},
                "stdin": {"stdin_bytes": 7},
                "diff": {"model_visible_bytes": 0},
                "validation": {"model_visible_bytes": 20},
                "tool_context_intake": {"command_stdout_bytes_visible_to_model": 0, "command_stderr_bytes_visible_to_model": 0},
            }
        ]

        summary = build_token_reduction_summary(usage_records, context_records, "exp")

        self.assertIn("ZCode Token Reduction Summary", summary)
        self.assertIn("Context-Intake Bytes", summary)
        self.assertIn("effective_codex_work", summary)


class DirectLauncherTests(unittest.TestCase):
    def test_direct_launcher_manifest_keeps_raw_stdout_out_of_visible_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact_dir = root / "artifacts"
            manifest_path = root / "manifest.json"
            noisy = "RAW_STDOUT_SHOULD_ONLY_BE_IN_LOG"

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "run-zcode-direct-launcher",
                        "--workspace",
                        str(root),
                        "--artifact-dir",
                        str(artifact_dir),
                        "--manifest-out",
                        str(manifest_path),
                        "--worktree-mode",
                        "fixture",
                        "--",
                        "python3",
                        "-c",
                        f"print({noisy!r})",
                    ]
                )

            self.assertEqual(exit_code, 0)
            visible = output.getvalue()
            self.assertNotIn(noisy, visible)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            stdout_log = Path(manifest["artifacts"]["launcher_stdout_log"])
            self.assertIn(noisy, stdout_log.read_text(encoding="utf-8"))
            self.assertEqual(manifest["env_policy"], "allowlist")
            self.assertNotIn("OPENAI_API_KEY", manifest["env_names"])

    def test_direct_launcher_rejects_secret_like_env_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "run-zcode-direct-launcher",
                        "--workspace",
                        tmp,
                        "--worktree-mode",
                        "fixture",
                        "--allow-env",
                        "OPENAI_API_KEY",
                        "--",
                        "python3",
                        "-c",
                        "print('ok')",
                    ]
                )

            self.assertEqual(exit_code, 1)
            self.assertIn("refusing to allow secret-like env var", output.getvalue())

    def test_direct_launcher_fails_when_raw_logs_are_not_gitignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            manifest_path = root / "manifest.json"

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "run-zcode-direct-launcher",
                        "--workspace",
                        str(root),
                        "--artifact-dir",
                        str(root / "review-logs"),
                        "--manifest-out",
                        str(manifest_path),
                        "--worktree-mode",
                        "fixture",
                        "--",
                        "python3",
                        "-c",
                        "print('ok')",
                    ]
                )

            self.assertEqual(exit_code, 1)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertFalse(manifest["safety"]["raw_logs_gitignored"])
            self.assertFalse(manifest["ok"])
            self.assertTrue(manifest["artifacts"]["launcher_stdout_log"].endswith(".redacted.log"))

    def test_direct_launcher_includes_untracked_outputs_in_scope_check(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as artifacts:
            root = Path(tmp)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            manifest_path = root / "manifest.json"

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "run-zcode-direct-launcher",
                        "--workspace",
                        str(root),
                        "--artifact-dir",
                        artifacts,
                        "--manifest-out",
                        str(manifest_path),
                        "--worktree-mode",
                        "fixture",
                        "--allowed",
                        "src/app.py",
                        "--",
                        "python3",
                        "-c",
                        "from pathlib import Path; Path('out.txt').write_text('new output')",
                    ]
                )

            self.assertEqual(exit_code, 1)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertIn("out.txt", manifest["changed_files"])
            self.assertEqual(manifest["quality"]["allowed_files_only"], "fail")
            self.assertFalse(manifest["ok"])

    def test_direct_launcher_does_not_embed_untracked_file_contents(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as artifacts:
            root = Path(tmp)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            manifest_path = root / "manifest.json"
            private_content = "private_material_9f7c2d4a_not_for_artifacts"

            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = main(
                    [
                        "run-zcode-direct-launcher",
                        "--workspace",
                        str(root),
                        "--artifact-dir",
                        artifacts,
                        "--manifest-out",
                        str(manifest_path),
                        "--worktree-mode",
                        "fixture",
                        "--allowed",
                        "notes.txt",
                        "--",
                        "python3",
                        "-c",
                        f"from pathlib import Path; Path('notes.txt').write_text({private_content!r})",
                    ]
                )

            self.assertEqual(exit_code, 0)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            diff = Path(manifest["artifacts"]["diff_path"]).read_text(encoding="utf-8")
            self.assertNotIn(private_content, diff)
            self.assertIn("untracked metadata: notes.txt", diff)
            self.assertIn("sha256:", diff)
            self.assertEqual(manifest["safety"]["untracked_file_content_policy"], "metadata_only")

    def test_direct_launcher_scans_staged_diff_content(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as artifacts:
            root = Path(tmp)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            manifest_path = root / "manifest.json"
            command = (
                "from pathlib import Path; import subprocess; "
                "Path('allowed.txt').write_text('token=abcdefghijklmnop'); "
                "subprocess.run(['git', 'add', 'allowed.txt'], check=True)"
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "run-zcode-direct-launcher",
                        "--workspace",
                        str(root),
                        "--artifact-dir",
                        artifacts,
                        "--manifest-out",
                        str(manifest_path),
                        "--worktree-mode",
                        "fixture",
                        "--allowed",
                        "allowed.txt",
                        "--",
                        "python3",
                        "-c",
                        command,
                    ]
                )

            self.assertEqual(exit_code, 1)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["safety"]["secret_scan_result"], "fail")
            self.assertIn("allowed.txt", manifest["changed_files"])
            self.assertFalse(manifest["ok"])

    def test_direct_launcher_detects_spaced_api_key_label(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as artifacts:
            root = Path(tmp)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            manifest_path = root / "manifest.json"
            command = (
                "from pathlib import Path; import subprocess; "
                "Path('allowed.txt').write_text('api key = abcdefghijklmnop'); "
                "subprocess.run(['git', 'add', 'allowed.txt'], check=True)"
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "run-zcode-direct-launcher",
                        "--workspace",
                        str(root),
                        "--artifact-dir",
                        artifacts,
                        "--manifest-out",
                        str(manifest_path),
                        "--worktree-mode",
                        "fixture",
                        "--allowed",
                        "allowed.txt",
                        "--",
                        "python3",
                        "-c",
                        command,
                    ]
                )

            self.assertEqual(exit_code, 1)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["safety"]["secret_scan_result"], "fail")
            self.assertFalse(manifest["ok"])

    def test_direct_launcher_fails_explicit_validation_failure_payload(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as artifacts:
            root = Path(tmp)
            payload = '{"audit":{"validation":{"ok":false}}}'
            manifest_path = root / "manifest.json"

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "run-zcode-direct-launcher",
                        "--workspace",
                        str(root),
                        "--artifact-dir",
                        artifacts,
                        "--manifest-out",
                        str(manifest_path),
                        "--worktree-mode",
                        "fixture",
                        "--",
                        "python3",
                        "-c",
                        f"print({payload!r})",
                    ]
                )

            self.assertEqual(exit_code, 1)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["quality"]["validation_result"], "fail")
            self.assertFalse(manifest["ok"])

    def test_direct_launcher_fails_explicit_nonpassing_quality_payload(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as artifacts:
            root = Path(tmp)
            payload = json.dumps(
                {
                    "run_result_summary": {
                        "scope_safety": "partial",
                        "validation_result": "blocked",
                        "artifact_quality": "partial",
                    }
                }
            )
            manifest_path = root / "manifest.json"

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "run-zcode-direct-launcher",
                        "--workspace",
                        str(root),
                        "--artifact-dir",
                        artifacts,
                        "--manifest-out",
                        str(manifest_path),
                        "--worktree-mode",
                        "fixture",
                        "--",
                        "python3",
                        "-c",
                        f"print({payload!r})",
                    ]
                )

            self.assertEqual(exit_code, 1)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["quality"]["scope_safety"], "partial")
            self.assertEqual(manifest["quality"]["validation_result"], "blocked")
            self.assertEqual(manifest["quality"]["artifact_quality"], "partial")
            self.assertFalse(manifest["ok"])

    def test_direct_launcher_writes_manifest_for_missing_executable(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as artifacts:
            root = Path(tmp)
            manifest_path = root / "manifest.json"

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "run-zcode-direct-launcher",
                        "--workspace",
                        str(root),
                        "--artifact-dir",
                        artifacts,
                        "--manifest-out",
                        str(manifest_path),
                        "--worktree-mode",
                        "fixture",
                        "--",
                        "definitely-not-a-real-zcode-command",
                    ]
                )

            self.assertEqual(exit_code, 1)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["command_exit_code"], 127)
            self.assertEqual(manifest["skipped_reason"], "command_os_error")
            self.assertFalse(manifest["ok"])

    def test_direct_launcher_scaffold_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "run-zcode-direct-launcher",
                        "--workspace",
                        tmp,
                        "--dry-run",
                    ]
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertTrue(payload["dry_run"])
            self.assertEqual(payload["skipped_reason"], "dry_run")


if __name__ == "__main__":
    unittest.main()
