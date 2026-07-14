import json
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from tools.zcode_eval.zcode_eval import build_summary, load_records, main


class ZCodeEvalTests(unittest.TestCase):
    def test_append_and_load_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "eval.jsonl"
            exit_code = main(
                [
                    "append-result",
                    "--path",
                    str(ledger),
                    "--run-id",
                    "run-1",
                    "--tool",
                    "zcode",
                    "--task-id",
                    "task-a",
                    "--task-name",
                    "small fixture",
                    "--status",
                    "pass",
                    "--manual-interventions",
                    "2",
                    "--tokens-total",
                    "1000",
                ]
            )

            self.assertEqual(exit_code, 0)
            records = load_records(ledger)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["tool"], "zcode")
            self.assertEqual(records[0]["manual_interventions"], 2.0)

    def test_append_derives_token_and_quota_usage_from_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "eval.jsonl"
            before = root / "before.json"
            after = root / "after.json"
            before.write_text(
                '{"best":{"tokens_total":1000,"quota_percent":88.5}}\n',
                encoding="utf-8",
            )
            after.write_text(
                '{"best":{"tokens_total":1450,"quota_percent":87.0}}\n',
                encoding="utf-8",
            )

            exit_code = main(
                [
                    "append-result",
                    "--path",
                    str(ledger),
                    "--run-id",
                    "run-usage",
                    "--tool",
                    "zcode",
                    "--task-id",
                    "task-usage",
                    "--task-name",
                    "usage fixture",
                    "--status",
                    "pass",
                    "--usage-before",
                    str(before),
                    "--usage-after",
                    str(after),
                ]
            )

            self.assertEqual(exit_code, 0)
            record = load_records(ledger)[0]
            self.assertEqual(record["tokens_before"], 1000.0)
            self.assertEqual(record["tokens_after"], 1450.0)
            self.assertEqual(record["tokens_used"], 450.0)
            self.assertEqual(record["quota_percent_before"], 88.5)
            self.assertEqual(record["quota_percent_after"], 87.0)
            self.assertEqual(record["quota_percent_used"], 1.5)

    def test_append_derives_used_quota_from_codexbar_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "eval.jsonl"
            before = root / "codexbar-before.json"
            after = root / "codexbar-after.json"
            before.write_text(
                """
                [
                  {
                    "provider": "zai",
                    "source": "api",
                    "usage": {
                      "primary": {
                        "resetDescription": "5 hours window",
                        "resetsAt": "2026-06-17T18:30:44Z",
                        "usedPercent": 1.25
                      },
                      "secondary": {
                        "resetDescription": "Monthly",
                        "resetsAt": "2026-07-04T05:08:05Z",
                        "usedPercent": 0.5
                      }
                    }
                  }
                ]
                """,
                encoding="utf-8",
            )
            after.write_text(
                """
                [
                  {
                    "provider": "zai",
                    "source": "api",
                    "usage": {
                      "primary": {
                        "resetDescription": "5 hours window",
                        "resetsAt": "2026-06-17T18:30:44Z",
                        "usedPercent": 1.75
                      },
                      "secondary": {
                        "resetDescription": "Monthly",
                        "resetsAt": "2026-07-04T05:08:05Z",
                        "usedPercent": 0.6
                      }
                    }
                  }
                ]
                """,
                encoding="utf-8",
            )

            exit_code = main(
                [
                    "append-result",
                    "--path",
                    str(ledger),
                    "--run-id",
                    "run-codexbar",
                    "--tool",
                    "zcode",
                    "--task-id",
                    "task-codexbar",
                    "--task-name",
                    "codexbar fixture",
                    "--status",
                    "pass",
                    "--usage-before",
                    str(before),
                    "--usage-after",
                    str(after),
                    "--quota-percent-direction",
                    "used",
                ]
            )

            self.assertEqual(exit_code, 0)
            record = load_records(ledger)[0]
            self.assertEqual(record["quota_percent_direction"], "used")
            self.assertEqual(record["quota_percent_before"], 1.25)
            self.assertEqual(record["quota_percent_after"], 1.75)
            self.assertEqual(record["quota_percent_used"], 0.5)

    def test_append_derives_used_quota_from_zai_api_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "eval.jsonl"
            before = root / "zai-before.json"
            after = root / "zai-after.json"
            before.write_text(
                """
                {
                  "code": 200,
                  "data": {
                    "level": "pro",
                    "limits": [
                      {
                        "type": "TOKENS_LIMIT",
                        "percentage": 2.0,
                        "usage": 2000,
                        "remaining": 98000,
                        "nextResetTime": 1781680000000
                      },
                      {"type": "TIME_LIMIT", "percentage": 0.5, "usage": 30, "remaining": 70}
                    ]
                  }
                }
                """,
                encoding="utf-8",
            )
            after.write_text(
                """
                {
                  "code": 200,
                  "data": {
                    "level": "pro",
                    "limits": [
                      {
                        "type": "TOKENS_LIMIT",
                        "percentage": 2.75,
                        "usage": 2750,
                        "remaining": 97250,
                        "nextResetTime": 1781680000000
                      },
                      {"type": "TIME_LIMIT", "percentage": 0.5, "usage": 30, "remaining": 70}
                    ]
                  }
                }
                """,
                encoding="utf-8",
            )

            exit_code = main(
                [
                    "append-result",
                    "--path",
                    str(ledger),
                    "--run-id",
                    "run-zai-api",
                    "--tool",
                    "zcode",
                    "--task-id",
                    "task-zai-api",
                    "--task-name",
                    "zai api fixture",
                    "--status",
                    "pass",
                    "--usage-before",
                    str(before),
                    "--usage-after",
                    str(after),
                    "--quota-percent-direction",
                    "used",
                ]
            )

            self.assertEqual(exit_code, 0)
            record = load_records(ledger)[0]
            self.assertEqual(record["quota_percent_direction"], "used")
            self.assertEqual(record["quota_percent_before"], 2.0)
            self.assertEqual(record["quota_percent_after"], 2.75)
            self.assertEqual(record["quota_percent_used"], 0.75)
            self.assertEqual(record["quota_percent_status"], "measured")

    def test_append_derives_zai_api_quota_percent_without_token_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "eval.jsonl"
            before = root / "zai-before.json"
            after = root / "zai-after.json"
            before.write_text(
                """
                {
                  "code": 200,
                  "data": {
                    "level": "pro",
                    "limits": [
                      {"type": "TOKENS_LIMIT", "percentage": 2.0, "usage": null, "remaining": null},
                      {"type": "TIME_LIMIT", "percentage": 0.5, "usage": 30, "remaining": 70}
                    ]
                  }
                }
                """,
                encoding="utf-8",
            )
            after.write_text(
                """
                {
                  "code": 200,
                  "data": {
                    "level": "pro",
                    "limits": [
                      {"type": "TOKENS_LIMIT", "percentage": 2.75, "usage": null, "remaining": null},
                      {"type": "TIME_LIMIT", "percentage": 0.5, "usage": 30, "remaining": 70}
                    ]
                  }
                }
                """,
                encoding="utf-8",
            )

            exit_code = main(
                [
                    "append-result",
                    "--path",
                    str(ledger),
                    "--run-id",
                    "run-zai-api-percent-only",
                    "--tool",
                    "zcode",
                    "--task-id",
                    "task-zai-api-percent-only",
                    "--task-name",
                    "zai api percent-only fixture",
                    "--status",
                    "pass",
                    "--usage-before",
                    str(before),
                    "--usage-after",
                    str(after),
                    "--quota-percent-direction",
                    "used",
                ]
            )

            self.assertEqual(exit_code, 0)
            record = load_records(ledger)[0]
            self.assertEqual(record["quota_percent_before"], 2.0)
            self.assertEqual(record["quota_percent_after"], 2.75)
            self.assertEqual(record["quota_percent_used"], 0.75)
            self.assertEqual(record["quota_percent_status"], "measured")
            self.assertNotIn("quota_percent_unavailable_reason", record)

    def test_append_records_provider_retry_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "eval.jsonl"

            exit_code = main(
                [
                    "append-result",
                    "--path",
                    str(ledger),
                    "--run-id",
                    "run-provider",
                    "--tool",
                    "zcode",
                    "--task-id",
                    "task-provider",
                    "--task-name",
                    "provider fixture",
                    "--status",
                    "partial",
                    "--supervisor-state",
                    "partial_success",
                    "--provider-error",
                    "--provider-code",
                    "1305",
                    "--provider-id",
                    "zai",
                    "--provider-kind",
                    "anthropic",
                    "--attempts",
                    "2",
                    "--attempt-count",
                    "2",
                    "--retry-count",
                    "1",
                    "--retry-delays-ms",
                    "[100,200]",
                    "--partial-artifacts-possible",
                    "--no-safe-to-retry-later",
                    "--no-usage-available",
                    "--no-usage-reason",
                    "provider_error_without_zcode_cli_usage",
                ]
            )

            self.assertEqual(exit_code, 0)
            record = load_records(ledger)[0]
            self.assertTrue(record["provider_error"])
            self.assertEqual(record["provider_code"], "1305")
            self.assertEqual(record["supervisor_state"], "partial_success")
            self.assertEqual(record["attempts"], 2)
            self.assertEqual(record["attempt_count"], 2)
            self.assertEqual(record["retry_count"], 1)
            self.assertEqual(record["retry_delays_ms"], [100, 200])
            self.assertTrue(record["partial_artifacts_possible"])
            self.assertFalse(record["safe_to_retry_later"])
            self.assertFalse(record["usage_available"])
            self.assertEqual(record["no_usage_reason"], "provider_error_without_zcode_cli_usage")

    def test_append_records_artifact_quality_and_repair_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "eval.jsonl"

            exit_code = main(
                [
                    "append-result",
                    "--path",
                    str(ledger),
                    "--run-id",
                    "run-quality",
                    "--tool",
                    "zcode",
                    "--task-id",
                    "task-quality",
                    "--task-name",
                    "bounded artifact quality fixture",
                    "--status",
                    "pass",
                    "--artifact-quality",
                    "pass",
                    "--scope-safety",
                    "pass",
                    "--validation-result",
                    "pass",
                    "--codex-repair-size",
                    "none",
                    "--codex-token-usage-status",
                    "unavailable",
                    "--codex-token-usage-reason",
                    "fixture run without Codex exec events",
                ]
            )

            self.assertEqual(exit_code, 0)
            record = load_records(ledger)[0]
            self.assertEqual(record["artifact_quality"], "pass")
            self.assertEqual(record["scope_safety"], "pass")
            self.assertEqual(record["validation_result"], "pass")
            self.assertEqual(record["codex_repair_size"], "none")
            self.assertEqual(record["codex_token_usage_status"], "unavailable")

    def test_import_duel_results_enriches_zcode_provider_error_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            run_dir = project / "work" / "supervisor_duel_eval" / "runs" / "20260617-153042"
            control = run_dir / "_control" / "zcode" / "site_ops"
            control.mkdir(parents=True)
            result_json = control / "zcode-result.json"
            result_json.write_text(
                json.dumps(
                    {
                        "ok": False,
                        "exit_code": 143,
                        "stdout": "",
                        "stderr": (
                            "ProviderBusinessError: [1305][The service may be temporarily overloaded, "
                            "please try again later][req-1]\n"
                            "  providerCode: '1305',\n"
                            "  providerId: 'zai',\n"
                            "  providerKind: 'anthropic',\n"
                            "  providerRequestId: 'req-1'\n"
                        ),
                    }
                ),
                encoding="utf-8",
            )
            source = run_dir / "results.json"
            source.write_text(
                json.dumps(
                    {
                        "run_dir": "work/supervisor_duel_eval/runs/20260617-153042",
                        "errors": [],
                        "rows": [
                            {
                                "tool": "zcode",
                                "task_id": "site_ops",
                                "kind": "sophisticated website",
                                "run_ok": False,
                                "validation_ok": True,
                                "scope_ok": True,
                                "output_files_ok": True,
                                "quality_score": 8,
                                "wall_ms": 123000,
                                "tokens_total": None,
                                "changed_files": ["index.html"],
                                "lines_added": 10,
                                "lines_deleted": 1,
                                "preview": str(run_dir / "zcode" / "site_ops" / "index.html"),
                                "audit": {"ok": True, "changed_count": 1},
                                "validation": {"ok": True, "returncode": 0},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            ledger = root / "eval.jsonl"

            exit_code = main(
                [
                    "import-duel-results",
                    "--source",
                    str(source),
                    "--path",
                    str(ledger),
                ]
            )

            self.assertEqual(exit_code, 0)
            record = load_records(ledger)[0]
            self.assertEqual(record["tool"], "zcode")
            self.assertEqual(record["status"], "partial")
            self.assertEqual(record["supervisor_state"], "partial_success")
            self.assertTrue(record["provider_error"])
            self.assertEqual(record["provider_code"], "1305")
            self.assertEqual(record["provider_request_id"], "req-1")
            self.assertEqual(record["attempt_count"], 1)
            self.assertEqual(record["retry_delays_ms"], [])
            self.assertFalse(record["usage_available"])
            self.assertEqual(record["no_usage_reason"], "provider_error_without_zcode_cli_usage")
            self.assertEqual(record["quota_percent_status"], "unavailable")
            self.assertEqual(
                record["quota_percent_unavailable_reason"],
                "historical_duel_missing_authoritative_quota_snapshot",
            )

    def test_summary_groups_by_tool(self):
        summary = build_summary(
            [
                {"tool": "zcode", "status": "pass", "tokens_used": 100.0, "quota_percent_used": 1.0},
                {
                    "tool": "zcode",
                    "status": "fail",
                    "tokens_used": 300.0,
                    "quota_percent_used": 3.0,
                    "provider_error": True,
                    "safe_to_retry_later": True,
                },
                {"tool": "zcode", "status": "partial", "supervisor_state": "partial_success"},
                {"tool": "claude-code-glm52", "status": "pass", "tokens_total": 200.0},
            ]
        )

        self.assertEqual(summary["records"], 4)
        self.assertEqual(summary["tools"]["zcode"]["runs"], 3)
        self.assertEqual(summary["tools"]["zcode"]["pass_rate"], 0.333)
        self.assertEqual(summary["tools"]["zcode"]["avg_tokens_used"], 200.0)
        self.assertEqual(summary["tools"]["zcode"]["total_quota_percent_used"], 4.0)
        self.assertEqual(summary["tools"]["zcode"]["provider_errors"], 1)
        self.assertEqual(summary["tools"]["zcode"]["retryable_provider_errors"], 1)
        self.assertEqual(summary["tools"]["zcode"]["partial_successes"], 1)

    def test_summary_counts_artifact_quality_and_repair_size(self):
        summary = build_summary(
            [
                {
                    "tool": "zcode",
                    "status": "pass",
                    "artifact_quality": "pass",
                    "codex_repair_size": "none",
                },
                {
                    "tool": "zcode",
                    "status": "partial",
                    "artifact_quality": "partial",
                    "codex_repair_size": "small polish",
                },
                {
                    "tool": "zcode",
                    "status": "fail",
                    "artifact_quality": "fail",
                    "codex_repair_size": "rewrite needed",
                    "codex_direct_fallback_reason": "scope_violation_repeated",
                },
            ]
        )

        tool = summary["tools"]["zcode"]
        self.assertEqual(tool["artifact_quality"], {"fail": 1, "partial": 1, "pass": 1})
        self.assertEqual(
            tool["codex_repair_size"],
            {"none": 1, "rewrite needed": 1, "small polish": 1},
        )
        self.assertEqual(tool["codex_direct_fallbacks"], 1)

    def test_compare_codex_runs_reports_savings_and_zcode_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            direct = root / "direct.jsonl"
            zcode = root / "zcode.jsonl"
            run_json = root / "run.zcode.json"
            markdown = root / "comparison.md"
            direct.write_text(
                "\n".join(
                    [
                        json.dumps({"type": "thread.started", "thread_id": "direct"}),
                        json.dumps(
                            {
                                "type": "turn.completed",
                                "usage": {
                                    "input_tokens": 150000,
                                    "cached_input_tokens": 100000,
                                    "output_tokens": 600,
                                    "reasoning_output_tokens": 300,
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "turn.completed",
                                "usage": {
                                    "input_tokens": 250000,
                                    "cached_input_tokens": 200000,
                                    "output_tokens": 400,
                                    "reasoning_output_tokens": 200,
                                },
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            zcode.write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 85000,
                            "cached_input_tokens": 10000,
                            "output_tokens": 500,
                            "reasoning_output_tokens": 250,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            run_json.write_text(
                json.dumps(
                    {
                        "supervisor_state": "success",
                        "audit": {
                            "ok": True,
                            "validation": {"ok": True},
                            "changed_count": 1,
                            "artifact_quality": "pass",
                            "scope_safety": "pass",
                            "validation_result": "pass",
                            "codex_repair_size_recommendation": "none",
                            "violations": [],
                        },
                        "attempt_results": [
                            {
                                "audit_ok": True,
                                "validation_ok": True,
                                "changed_count": 1,
                            }
                        ],
                        "usage_accounting": {
                            "usage_available": False,
                            "no_usage_reason": "zcode_cli_usage_missing",
                        },
                    }
                ),
                encoding="utf-8",
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "compare-codex-runs",
                        "--baseline-label",
                        "direct_minimal",
                        "--run",
                        f"direct_minimal={direct}",
                        "--run",
                        f"zcode_minimal={zcode}",
                        "--quality",
                        "direct_minimal=pass",
                        "--quality",
                        "zcode_minimal=pass",
                        "--zcode-run-json",
                        f"zcode_minimal={run_json}",
                        "--duration",
                        "direct_minimal=200",
                        "--duration",
                        "zcode_minimal=150",
                        "--candidate-label",
                        "zcode_minimal",
                        "--reduction-metric",
                        "total_in_out",
                        "--min-reduction-percent",
                        "75",
                        "--require-quality-pass",
                        "--require-zcode-acceptance",
                        "--max-duration-ratio",
                        "1.0",
                        "--markdown-out",
                        str(markdown),
                    ]
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["reduction_metric"], "total_in_out")
            self.assertEqual(payload["baseline_total_in_out"], 401000)
            self.assertEqual(payload["baseline_metric_value"], 401000)
            zcode_row = next(row for row in payload["rows"] if row["label"] == "zcode_minimal")
            self.assertEqual(zcode_row["metrics"]["total_in_out"], 85500)
            self.assertEqual(zcode_row["reduction_vs_baseline_percent"], 78.68)
            self.assertEqual(zcode_row["duration_ratio_vs_baseline"], 0.75)
            self.assertEqual(zcode_row["quality"], "pass")
            self.assertTrue(zcode_row["zcode_acceptance"]["audit_ok"])
            self.assertTrue(zcode_row["zcode_acceptance"]["validation_ok"])
            self.assertEqual(zcode_row["zcode_acceptance"]["changed_count"], 1)
            self.assertEqual(zcode_row["zcode_acceptance"]["artifact_quality"], "pass")
            self.assertEqual(zcode_row["zcode_acceptance"]["scope_safety"], "pass")
            self.assertEqual(zcode_row["zcode_acceptance"]["validation_result"], "pass")
            self.assertEqual(zcode_row["zcode_acceptance"]["codex_repair_size"], "none")
            self.assertTrue(payload["gate"]["ok"])
            self.assertIn("zcode_minimal", markdown.read_text(encoding="utf-8"))

    def test_compare_codex_runs_can_gate_on_uncached_marginal_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            direct = root / "direct.jsonl"
            zcode = root / "zcode.jsonl"
            direct.write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 100000,
                            "cached_input_tokens": 80000,
                            "output_tokens": 1000,
                            "reasoning_output_tokens": 1000,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            zcode.write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 70000,
                            "cached_input_tokens": 65000,
                            "output_tokens": 1000,
                            "reasoning_output_tokens": 0,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "compare-codex-runs",
                        "--baseline-label",
                        "direct",
                        "--run",
                        f"direct={direct}",
                        "--run",
                        f"zcode={zcode}",
                        "--quality",
                        "direct=pass",
                        "--quality",
                        "zcode=pass",
                        "--candidate-label",
                        "zcode",
                        "--reduction-metric",
                        "uncached_plus_reasoning",
                        "--min-reduction-percent",
                        "50",
                        "--require-quality-pass",
                    ]
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["reduction_metric"], "uncached_plus_reasoning")
            self.assertEqual(payload["baseline_metric_value"], 21000)
            zcode_row = next(row for row in payload["rows"] if row["label"] == "zcode")
            self.assertEqual(zcode_row["metrics"]["uncached_plus_reasoning"], 5000)
            self.assertEqual(zcode_row["reduction_vs_baseline_percent"], 76.19)
            self.assertTrue(payload["gate"]["ok"])

    def test_compare_codex_runs_gate_fails_when_reduction_is_too_low(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            direct = root / "direct.jsonl"
            zcode = root / "zcode.jsonl"
            run_json = root / "run.zcode.json"
            direct.write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 100000,
                            "cached_input_tokens": 0,
                            "output_tokens": 1000,
                            "reasoning_output_tokens": 0,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            zcode.write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 40000,
                            "cached_input_tokens": 0,
                            "output_tokens": 1000,
                            "reasoning_output_tokens": 0,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            run_json.write_text(
                json.dumps(
                    {
                        "supervisor_state": "success",
                        "attempt_results": [
                            {
                                "audit_ok": True,
                                "validation_ok": True,
                                "changed_count": 1,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "compare-codex-runs",
                        "--baseline-label",
                        "direct",
                        "--run",
                        f"direct={direct}",
                        "--run",
                        f"zcode={zcode}",
                        "--quality",
                        "direct=pass",
                        "--quality",
                        "zcode=pass",
                        "--zcode-run-json",
                        f"zcode={run_json}",
                        "--min-reduction-percent",
                        "80",
                        "--require-quality-pass",
                        "--require-zcode-acceptance",
                    ]
                )

            self.assertEqual(exit_code, 1)
            payload = json.loads(output.getvalue())
            self.assertFalse(payload["gate"]["ok"])
            self.assertIn("reduction", payload["gate"]["violations"][0])

    def test_compare_codex_runs_fails_closed_when_candidate_usage_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            direct = root / "direct.jsonl"
            zcode = root / "zcode.jsonl"
            run_json = root / "run.zcode.json"
            direct.write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 100000,
                            "cached_input_tokens": 50000,
                            "output_tokens": 1000,
                            "reasoning_output_tokens": 500,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            zcode.write_text(json.dumps({"type": "thread.started", "thread_id": "zcode"}) + "\n", encoding="utf-8")
            run_json.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "status": "success",
                        "supervisor_state": "success",
                        "attempt_results": [
                            {
                                "audit_ok": True,
                                "validation_ok": True,
                                "changed_count": 1,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "compare-codex-runs",
                        "--baseline-label",
                        "direct",
                        "--run",
                        f"direct={direct}",
                        "--run",
                        f"zcode={zcode}",
                        "--quality",
                        "direct=pass",
                        "--quality",
                        "zcode=pass",
                        "--zcode-run-json",
                        f"zcode={run_json}",
                        "--candidate-label",
                        "zcode",
                        "--reduction-metric",
                        "uncached_plus_reasoning",
                        "--min-reduction-percent",
                        "50",
                        "--require-quality-pass",
                        "--require-zcode-acceptance",
                    ]
                )

            self.assertEqual(exit_code, 1)
            payload = json.loads(output.getvalue())
            zcode_row = next(row for row in payload["rows"] if row["label"] == "zcode")
            self.assertEqual(zcode_row["usage_status"], "unavailable")
            self.assertIsNone(zcode_row["metrics"]["uncached_plus_reasoning"])
            self.assertIsNone(zcode_row["reduction_vs_baseline_percent"])
            self.assertFalse(payload["gate"]["ok"])
            self.assertTrue(any("usage_unavailable" in item for item in payload["gate"]["violations"]))

    def test_compare_codex_runs_fails_closed_when_candidate_usage_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            direct = root / "direct.jsonl"
            zcode = root / "zcode.jsonl"
            direct.write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 100000,
                            "cached_input_tokens": 50000,
                            "output_tokens": 1000,
                            "reasoning_output_tokens": 500,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            zcode.write_text(
                json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1000}}) + "\n",
                encoding="utf-8",
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "compare-codex-runs",
                        "--baseline-label",
                        "direct",
                        "--run",
                        f"direct={direct}",
                        "--run",
                        f"zcode={zcode}",
                        "--candidate-label",
                        "zcode",
                        "--min-reduction-percent",
                        "1",
                    ]
                )

            self.assertEqual(exit_code, 1)
            payload = json.loads(output.getvalue())
            zcode_row = next(row for row in payload["rows"] if row["label"] == "zcode")
            self.assertEqual(zcode_row["usage_status"], "unavailable")
            self.assertIn("incomplete turn.completed usage", zcode_row["no_usage_reason"])

    def test_compare_codex_runs_fails_gate_when_candidate_timed_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            direct = root / "direct.jsonl"
            zcode = root / "zcode.jsonl"
            run_json = root / "run.zcode.json"
            direct.write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 100000,
                            "cached_input_tokens": 0,
                            "output_tokens": 1000,
                            "reasoning_output_tokens": 0,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            zcode.write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 10000,
                            "cached_input_tokens": 0,
                            "output_tokens": 100,
                            "reasoning_output_tokens": 0,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            run_json.write_text(
                json.dumps(
                    {
                        "ok": False,
                        "status": "run_timeout",
                        "supervisor_state": "run_timeout",
                        "timed_out": True,
                        "attempt_results": [
                            {
                                "audit_ok": True,
                                "validation_ok": True,
                                "changed_count": 1,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "compare-codex-runs",
                        "--baseline-label",
                        "direct",
                        "--run",
                        f"direct={direct}",
                        "--run",
                        f"zcode={zcode}",
                        "--quality",
                        "direct=pass",
                        "--quality",
                        "zcode=pass",
                        "--zcode-run-json",
                        f"zcode={run_json}",
                        "--candidate-label",
                        "zcode",
                        "--min-reduction-percent",
                        "50",
                        "--require-quality-pass",
                        "--require-zcode-acceptance",
                    ]
                )

            self.assertEqual(exit_code, 1)
            payload = json.loads(output.getvalue())
            self.assertFalse(payload["gate"]["ok"])
            self.assertIn("zcode: timed out", payload["gate"]["violations"])

    def test_compare_codex_runs_requires_full_zcode_acceptance_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            direct = root / "direct.jsonl"
            zcode = root / "zcode.jsonl"
            run_json = root / "run.zcode.json"
            direct.write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 100000,
                            "cached_input_tokens": 0,
                            "output_tokens": 1000,
                            "reasoning_output_tokens": 0,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            zcode.write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 10000,
                            "cached_input_tokens": 0,
                            "output_tokens": 100,
                            "reasoning_output_tokens": 0,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            run_json.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "status": "success",
                        "supervisor_state": "success",
                        "audit": {
                            "ok": True,
                            "validation": {"ok": True},
                            "changed_count": 1,
                            "artifact_quality": "partial",
                            "scope_safety": "pass",
                            "validation_result": "pass",
                            "codex_repair_size_recommendation": "moderate fix",
                            "violations": [],
                        },
                    }
                ),
                encoding="utf-8",
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "compare-codex-runs",
                        "--baseline-label",
                        "direct",
                        "--run",
                        f"direct={direct}",
                        "--run",
                        f"zcode={zcode}",
                        "--quality",
                        "direct=pass",
                        "--quality",
                        "zcode=pass",
                        "--zcode-run-json",
                        f"zcode={run_json}",
                        "--candidate-label",
                        "zcode",
                        "--min-reduction-percent",
                        "50",
                        "--require-quality-pass",
                        "--require-zcode-acceptance",
                    ]
                )

            self.assertEqual(exit_code, 1)
            payload = json.loads(output.getvalue())
            self.assertFalse(payload["gate"]["ok"])
            self.assertIn("zcode: artifact_quality is partial, expected pass", payload["gate"]["violations"])
            self.assertIn(
                "zcode: codex_repair_size is moderate fix, expected none or small polish",
                payload["gate"]["violations"],
            )

    def test_accept_zcode_artifact_accepts_bare_audit_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit_json = root / "audit.json"
            markdown = root / "acceptance.md"
            audit_json.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "validation": {"ok": True},
                        "changed_count": 1,
                        "artifact_quality": "pass",
                        "scope_safety": "pass",
                        "validation_result": "pass",
                        "codex_repair_size_recommendation": "small polish",
                        "violations": [],
                    }
                ),
                encoding="utf-8",
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "accept-zcode-artifact",
                        "--zcode-run-json",
                        str(audit_json),
                        "--label",
                        "fixture",
                        "--markdown-out",
                        str(markdown),
                    ]
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["acceptance"]["artifact_quality"], "pass")
            self.assertEqual(payload["acceptance"]["codex_repair_size"], "small polish")
            self.assertIn("ZCode Artifact Acceptance", markdown.read_text(encoding="utf-8"))

    def test_accept_zcode_artifact_fails_on_scope_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit_json = root / "audit.json"
            audit_json.write_text(
                json.dumps(
                    {
                        "ok": False,
                        "validation": {"ok": True},
                        "changed_count": 2,
                        "artifact_quality": "fail",
                        "scope_safety": "fail",
                        "validation_result": "pass",
                        "codex_repair_size_recommendation": "rewrite needed",
                        "violations": [{"type": "outside_allowed_files", "files": ["README.md"]}],
                    }
                ),
                encoding="utf-8",
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "accept-zcode-artifact",
                        "--zcode-run-json",
                        str(audit_json),
                        "--label",
                        "fixture",
                    ]
                )

            self.assertEqual(exit_code, 1)
            payload = json.loads(output.getvalue())
            self.assertFalse(payload["ok"])
            self.assertIn("fixture: scope_safety is fail, expected pass", payload["violations"])
            self.assertIn("fixture: scope violations present (outside_allowed_files)", payload["violations"])

    def test_compare_zcode_acceptance_reports_estimated_thin_savings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            direct = root / "direct.jsonl"
            run_json = root / "run.zcode.json"
            markdown = root / "comparison.md"
            direct.write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 100000,
                            "cached_input_tokens": 80000,
                            "output_tokens": 1000,
                            "reasoning_output_tokens": 1000,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            run_json.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "status": "success",
                        "supervisor_state": "success",
                        "audit": {
                            "ok": True,
                            "validation": {"ok": True},
                            "changed_count": 1,
                            "artifact_quality": "pass",
                            "scope_safety": "pass",
                            "validation_result": "pass",
                            "codex_repair_size_recommendation": "none",
                            "violations": [],
                        },
                    }
                ),
                encoding="utf-8",
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "compare-zcode-acceptance",
                        "--direct-run-events",
                        str(direct),
                        "--zcode-run-json",
                        str(run_json),
                        "--candidate-codex-token-usage-status",
                        "estimated",
                        "--candidate-codex-token-estimate",
                        "0",
                        "--candidate-codex-token-usage-reason",
                        "deterministic local acceptance command without Codex exec",
                        "--min-estimated-reduction-percent",
                        "50",
                        "--require-token-estimate",
                        "--markdown-out",
                        str(markdown),
                    ]
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["token_comparison"]["baseline_metric_value"], 22000)
            self.assertEqual(payload["token_comparison"]["candidate_metric_value"], 0.0)
            self.assertEqual(payload["token_comparison"]["estimated_reduction_vs_direct_percent"], 100.0)
            self.assertEqual(payload["token_comparison"]["claim_status"], "estimated")
            self.assertTrue(payload["candidate"]["delegated_artifact_accepted"])
            self.assertIn("Direct Codex vs ZCode Thin Acceptance", markdown.read_text(encoding="utf-8"))

    def test_compare_zcode_acceptance_reports_measured_launcher_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            direct = root / "direct.jsonl"
            launcher = root / "launcher.jsonl"
            run_json = root / "run.zcode.json"
            markdown = root / "comparison.md"
            direct.write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 100000,
                            "cached_input_tokens": 80000,
                            "output_tokens": 1000,
                            "reasoning_output_tokens": 1000,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            launcher.write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 12000,
                            "cached_input_tokens": 10000,
                            "output_tokens": 300,
                            "reasoning_output_tokens": 200,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            run_json.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "status": "success",
                        "supervisor_state": "success",
                        "audit": {
                            "ok": True,
                            "validation": {"ok": True},
                            "changed_count": 1,
                            "artifact_quality": "pass",
                            "scope_safety": "pass",
                            "validation_result": "pass",
                            "codex_repair_size_recommendation": "none",
                            "violations": [],
                        },
                    }
                ),
                encoding="utf-8",
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "compare-zcode-acceptance",
                        "--direct-run-events",
                        str(direct),
                        "--zcode-run-json",
                        str(run_json),
                        "--candidate-codex-token-usage-status",
                        "measured",
                        "--candidate-run-events",
                        str(launcher),
                        "--min-reduction-percent",
                        "80",
                        "--markdown-out",
                        str(markdown),
                    ]
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["candidate"]["codex_token_usage_status"], "measured")
            self.assertEqual(payload["candidate"]["codex_token_usage_scope"], "codex_launcher_orchestration")
            self.assertEqual(payload["candidate"]["metrics"]["uncached_plus_reasoning"], 2200)
            self.assertEqual(payload["candidate"]["metrics"]["effective_codex_work"], 2500)
            self.assertEqual(payload["token_comparison"]["candidate_metric_value"], 2500.0)
            self.assertEqual(payload["token_comparison"]["measured_reduction_vs_direct_percent"], 88.64)
            self.assertIsNone(payload["token_comparison"]["estimated_reduction_vs_direct_percent"])
            self.assertEqual(payload["token_comparison"]["min_reduction_percent"], 80.0)
            self.assertEqual(payload["token_comparison"]["claim_status"], "measured")
            self.assertIn("launcher/orchestration", payload["token_comparison"]["caveat"])
            self.assertIn("reduction_vs_direct_percent", markdown.read_text(encoding="utf-8"))

    def test_compare_zcode_acceptance_fails_measured_mode_without_launcher_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            direct = root / "direct.jsonl"
            run_json = root / "run.zcode.json"
            direct.write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 100000,
                            "cached_input_tokens": 80000,
                            "output_tokens": 1000,
                            "reasoning_output_tokens": 1000,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            run_json.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "status": "success",
                        "supervisor_state": "success",
                        "audit": {
                            "ok": True,
                            "validation": {"ok": True},
                            "changed_count": 1,
                            "artifact_quality": "pass",
                            "scope_safety": "pass",
                            "validation_result": "pass",
                            "codex_repair_size_recommendation": "none",
                            "violations": [],
                        },
                    }
                ),
                encoding="utf-8",
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "compare-zcode-acceptance",
                        "--direct-run-events",
                        str(direct),
                        "--zcode-run-json",
                        str(run_json),
                        "--candidate-codex-token-usage-status",
                        "measured",
                    ]
                )

            self.assertEqual(exit_code, 1)
            payload = json.loads(output.getvalue())
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["candidate"]["codex_token_usage_status"], "unavailable")
            self.assertTrue(any("usage_unavailable" in item for item in payload["violations"]))

    def test_compare_zcode_acceptance_fails_when_quality_gate_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            direct = root / "direct.jsonl"
            run_json = root / "run.zcode.json"
            direct.write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 100000,
                            "cached_input_tokens": 80000,
                            "output_tokens": 1000,
                            "reasoning_output_tokens": 1000,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            run_json.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "status": "success",
                        "supervisor_state": "success",
                        "audit": {
                            "ok": True,
                            "validation": {"ok": True},
                            "changed_count": 1,
                            "artifact_quality": "partial",
                            "scope_safety": "pass",
                            "validation_result": "pass",
                            "codex_repair_size_recommendation": "small polish",
                            "violations": [],
                        },
                    }
                ),
                encoding="utf-8",
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "compare-zcode-acceptance",
                        "--direct-run-events",
                        str(direct),
                        "--zcode-run-json",
                        str(run_json),
                        "--candidate-codex-token-usage-status",
                        "estimated",
                        "--candidate-codex-token-estimate",
                        "0",
                        "--require-token-estimate",
                    ]
                )

            self.assertEqual(exit_code, 1)
            payload = json.loads(output.getvalue())
            self.assertFalse(payload["ok"])
            self.assertFalse(payload["candidate"]["delegated_artifact_accepted"])
            self.assertIn(
                "zcode_thin_acceptance: artifact_quality is partial, expected pass",
                payload["violations"],
            )

    def test_compare_zcode_acceptance_defaults_to_effective_codex_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            direct = root / "direct.jsonl"
            run_json = root / "run.zcode.json"
            direct.write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 100,
                            "cached_input_tokens": 80,
                            "output_tokens": 3,
                            "reasoning_output_tokens": 2,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            run_json.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "audit": {
                            "ok": True,
                            "validation": {"ok": True},
                            "changed_count": 1,
                            "artifact_quality": "pass",
                            "scope_safety": "pass",
                            "validation_result": "pass",
                            "codex_repair_size_recommendation": "none",
                        },
                    }
                ),
                encoding="utf-8",
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "compare-zcode-acceptance",
                        "--direct-run-events",
                        str(direct),
                        "--zcode-run-json",
                        str(run_json),
                        "--candidate-codex-token-usage-status",
                        "estimated",
                        "--candidate-codex-token-estimate",
                        "0",
                    ]
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["primary_metric"], "effective_codex_work")
            self.assertEqual(payload["token_comparison"]["metric"], "effective_codex_work")
            self.assertEqual(payload["baseline"]["metrics"]["uncached_plus_reasoning"], 22)
            self.assertEqual(payload["baseline"]["metrics"]["effective_codex_work"], 25)
            self.assertEqual(payload["token_comparison"]["baseline_metric_value"], 25)

    def test_manifest_only_acceptance_hides_raw_stdout_and_keeps_pass_validation_compact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_json = root / "run.json"
            validation_log = root / "validation.log"
            manifest = root / "zcode_result_manifest.json"
            raw_stdout = "RAW_STDOUT_SHOULD_NOT_APPEAR"
            run_json.write_text(
                json.dumps(
                    {
                        "stdout": raw_stdout,
                        "audit": {
                            "validation": {"ok": True},
                            "changed_count": 1,
                            "artifact_quality": "pass",
                            "scope_safety": "pass",
                            "validation_result": "pass",
                            "codex_repair_size_recommendation": "none",
                        },
                    }
                ),
                encoding="utf-8",
            )
            validation_log.write_text("PASS\n" + ("x" * 5000), encoding="utf-8")

            with contextlib.redirect_stdout(io.StringIO()):
                build_exit = main(
                    [
                        "build-zcode-result-manifest",
                        "--task-id",
                        "billing",
                        "--zcode-run-json",
                        str(run_json),
                        "--manifest-out",
                        str(manifest),
                        "--validation-log",
                        str(validation_log),
                        "--changed-file",
                        "src/billing.py",
                        "--allowed-files-only",
                    ]
                )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                accept_exit = main(
                    [
                        "accept-zcode-manifest",
                        "--manifest",
                        str(manifest),
                        "--secret-scan-result",
                        "pass",
                    ]
                )

            self.assertEqual(build_exit, 0)
            self.assertEqual(accept_exit, 0)
            manifest_text = manifest.read_text(encoding="utf-8")
            self.assertNotIn(raw_stdout, manifest_text)
            manifest_payload = json.loads(manifest_text)
            self.assertLessEqual(manifest_payload["validation"]["model_visible_bytes"], 1024)
            accept_payload = json.loads(output.getvalue())
            self.assertEqual(accept_payload["acceptance_mode"], "codex_manifest_only")
            self.assertTrue(accept_payload["accepted"])

    def test_manifest_builder_does_not_infer_allowed_files_from_scope_safety(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_json = root / "run.json"
            manifest = root / "zcode_result_manifest.json"
            run_json.write_text(
                json.dumps(
                    {
                        "audit": {
                            "validation": {"ok": True},
                            "changed_count": 1,
                            "artifact_quality": "pass",
                            "scope_safety": "pass",
                            "validation_result": "pass",
                            "codex_repair_size_recommendation": "none",
                        }
                    }
                ),
                encoding="utf-8",
            )

            with contextlib.redirect_stdout(io.StringIO()):
                build_exit = main(
                    [
                        "build-zcode-result-manifest",
                        "--task-id",
                        "billing",
                        "--zcode-run-json",
                        str(run_json),
                        "--manifest-out",
                        str(manifest),
                        "--changed-file",
                        "src/billing.py",
                    ]
                )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                accept_exit = main(
                    [
                        "accept-zcode-manifest",
                        "--manifest",
                        str(manifest),
                        "--secret-scan-result",
                        "pass",
                    ]
                )

            self.assertEqual(build_exit, 0)
            manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertFalse(manifest_payload["allowed_files_only"])
            self.assertEqual(accept_exit, 1)
            self.assertIn("allowed_files_only", json.loads(output.getvalue())["violations"])

    def test_failure_validation_excerpt_is_capped_and_manifest_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_json = root / "run.json"
            validation_log = root / "validation.log"
            manifest = root / "manifest.json"
            run_json.write_text(
                json.dumps(
                    {
                        "audit": {
                            "validation": {"ok": False},
                            "changed_count": 1,
                            "artifact_quality": "pass",
                            "scope_safety": "pass",
                            "validation_result": "fail",
                            "codex_repair_size_recommendation": "none",
                        }
                    }
                ),
                encoding="utf-8",
            )
            validation_log.write_text("E" * 9000, encoding="utf-8")

            with contextlib.redirect_stdout(io.StringIO()):
                build_exit = main(
                    [
                        "build-zcode-result-manifest",
                        "--task-id",
                        "policy",
                        "--zcode-run-json",
                        str(run_json),
                        "--manifest-out",
                        str(manifest),
                        "--validation-log",
                        str(validation_log),
                    ]
                )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                accept_exit = main(["accept-zcode-manifest", "--manifest", str(manifest)])

            self.assertEqual(build_exit, 0)
            manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(manifest_payload["validation"]["result"], "fail")
            self.assertLessEqual(manifest_payload["validation"]["model_visible_bytes"], 4096)
            self.assertEqual(accept_exit, 1)
            self.assertIn("validation_result", json.loads(output.getvalue())["violations"])

    def test_green_path_non_llm_acceptance_records_shadow_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": "zcode_result_manifest.v1",
                        "task_id": "ledger",
                        "changed_files": ["src/ledger.py"],
                        "allowed_files_only": True,
                        "diffstat": {"insertions": 1, "deletions": 0, "files_changed": 1},
                        "validation": {"exit_code": 0, "result": "pass", "summary": "pass", "model_visible_bytes": 12},
                        "artifact": {
                            "scope_safety": "pass",
                            "artifact_quality": "pass",
                            "codex_repair_size": "none",
                        },
                        "risk_flags": [],
                        "untrusted_data_notice": True,
                    }
                ),
                encoding="utf-8",
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "accept-zcode-manifest",
                        "--manifest",
                        str(manifest),
                        "--green-path-non-llm",
                        "--shadow-codex-audit-enabled",
                        "--shadow-codex-audit-result",
                        "agree",
                        "--secret-scan-result",
                        "pass",
                    ]
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["acceptance_mode"], "non_llm_green_path")
            self.assertTrue(payload["green_path_acceptance"]["codex_acceptance_skipped"])
            self.assertEqual(payload["green_path_acceptance"]["shadow_codex_audit_result"], "agree")

    def test_manifest_acceptance_rejects_unsafe_changed_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            outside = root / "outside"
            outside.mkdir()
            (workspace / "escape").symlink_to(outside, target_is_directory=True)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": "zcode_result_manifest.v1",
                        "task_id": "unsafe",
                        "changed_files": ["../secret.txt", "/tmp/pwn.py", "escape/pwn.py"],
                        "allowed_files_only": True,
                        "diffstat": {"insertions": 1, "deletions": 0, "files_changed": 3},
                        "validation": {"exit_code": 0, "result": "pass", "summary": "pass", "model_visible_bytes": 12},
                        "artifact": {
                            "scope_safety": "pass",
                            "artifact_quality": "pass",
                            "codex_repair_size": "none",
                        },
                        "risk_flags": [],
                        "untrusted_data_notice": True,
                    }
                ),
                encoding="utf-8",
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "accept-zcode-manifest",
                        "--manifest",
                        str(manifest),
                        "--workspace-root",
                        str(workspace),
                        "--secret-scan-result",
                        "pass",
                    ]
                )

            self.assertEqual(exit_code, 1)
            violations = json.loads(output.getvalue())["violations"]
            self.assertIn("path_safety_parent_changed_file", violations)
            self.assertIn("path_safety_absolute_changed_file", violations)
            self.assertIn("path_safety_symlink_escape", violations)

    def test_manifest_acceptance_rejects_noop_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": "zcode_result_manifest.v1",
                        "task_id": "noop",
                        "changed_files": [],
                        "allowed_files_only": True,
                        "diffstat": {"insertions": 0, "deletions": 0, "files_changed": 0},
                        "validation": {"exit_code": 0, "result": "pass", "summary": "pass", "model_visible_bytes": 12},
                        "artifact": {
                            "scope_safety": "pass",
                            "artifact_quality": "pass",
                            "codex_repair_size": "none",
                        },
                        "risk_flags": [],
                        "untrusted_data_notice": True,
                    }
                ),
                encoding="utf-8",
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "accept-zcode-manifest",
                        "--manifest",
                        str(manifest),
                        "--secret-scan-result",
                        "pass",
                    ]
                )

            self.assertEqual(exit_code, 1)
            violations = json.loads(output.getvalue())["violations"]
            self.assertIn("path_safety_no_changed_files", violations)
            self.assertIn("diffstat_no_changed_files", violations)

    def test_manifest_acceptance_checks_changed_files_count_against_diffstat(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.json"
            changed_files = [f"src/file_{index}.py" for index in range(11)]
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": "zcode_result_manifest.v1",
                        "task_id": "count-mismatch",
                        "changed_files": changed_files,
                        "allowed_files_only": True,
                        "diffstat": {"insertions": 11, "deletions": 0, "files_changed": 1},
                        "validation": {"exit_code": 0, "result": "pass", "summary": "pass", "model_visible_bytes": 12},
                        "artifact": {
                            "scope_safety": "pass",
                            "artifact_quality": "pass",
                            "codex_repair_size": "none",
                        },
                        "risk_flags": [],
                        "untrusted_data_notice": True,
                    }
                ),
                encoding="utf-8",
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "accept-zcode-manifest",
                        "--manifest",
                        str(manifest),
                        "--secret-scan-result",
                        "pass",
                    ]
                )

            self.assertEqual(exit_code, 1)
            violations = json.loads(output.getvalue())["violations"]
            self.assertIn("changed_files_budget", violations)
            self.assertIn("diffstat_changed_files_mismatch", violations)

    def test_manifest_acceptance_requires_confirmed_secret_scan_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": "zcode_result_manifest.v1",
                        "task_id": "secret-scan",
                        "changed_files": ["src/app.py"],
                        "allowed_files_only": True,
                        "diffstat": {"insertions": 1, "deletions": 0, "files_changed": 1},
                        "validation": {"exit_code": 0, "result": "pass", "summary": "pass", "model_visible_bytes": 12},
                        "artifact": {
                            "scope_safety": "pass",
                            "artifact_quality": "pass",
                            "codex_repair_size": "none",
                        },
                        "risk_flags": [],
                        "untrusted_data_notice": True,
                    }
                ),
                encoding="utf-8",
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(["accept-zcode-manifest", "--manifest", str(manifest)])

            self.assertEqual(exit_code, 1)
            violations = json.loads(output.getvalue())["violations"]
            self.assertIn("secret_scan_not_passed", violations)

    def test_manifest_acceptance_fails_closed_on_malformed_numeric_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": "zcode_result_manifest.v1",
                        "task_id": "malformed",
                        "changed_files": ["src/app.py"],
                        "allowed_files_only": True,
                        "diffstat": {"insertions": 1, "deletions": 0, "files_changed": "many"},
                        "validation": {
                            "exit_code": 0,
                            "result": "pass",
                            "summary": "pass",
                            "model_visible_bytes": "lots",
                        },
                        "artifact": {
                            "scope_safety": "pass",
                            "artifact_quality": "pass",
                            "codex_repair_size": "none",
                        },
                        "risk_flags": [],
                        "untrusted_data_notice": True,
                    }
                ),
                encoding="utf-8",
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "accept-zcode-manifest",
                        "--manifest",
                        str(manifest),
                        "--secret-scan-result",
                        "pass",
                    ]
                )

            self.assertEqual(exit_code, 1)
            violations = json.loads(output.getvalue())["violations"]
            self.assertIn("diffstat_files_changed_invalid", violations)
            self.assertIn("validation_visible_bytes_invalid", violations)

    def test_manifest_acceptance_requires_zero_validation_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": "zcode_result_manifest.v1",
                        "task_id": "bad-exit",
                        "changed_files": ["src/app.py"],
                        "allowed_files_only": True,
                        "diffstat": {"insertions": 1, "deletions": 0, "files_changed": 1},
                        "validation": {"exit_code": 1, "result": "pass", "summary": "pass", "model_visible_bytes": 12},
                        "artifact": {
                            "scope_safety": "pass",
                            "artifact_quality": "pass",
                            "codex_repair_size": "none",
                        },
                        "risk_flags": [],
                        "untrusted_data_notice": True,
                    }
                ),
                encoding="utf-8",
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "accept-zcode-manifest",
                        "--manifest",
                        str(manifest),
                        "--secret-scan-result",
                        "pass",
                    ]
                )

            self.assertEqual(exit_code, 1)
            self.assertIn("validation_exit_code", json.loads(output.getvalue())["violations"])

    def test_manifest_acceptance_enforces_manifest_contract_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": "zcode_result_manifest.v0",
                        "task_id": "bad-contract",
                        "changed_files": ["src/app.py"],
                        "allowed_files_only": True,
                        "diffstat": {"insertions": -1, "deletions": 0, "files_changed": 1},
                        "validation": {"exit_code": 0, "result": "pass", "summary": "pass", "model_visible_bytes": 12},
                        "artifact": {
                            "scope_safety": "pass",
                            "artifact_quality": "pass",
                            "codex_repair_size": "none",
                        },
                        "risk_flags": [],
                        "untrusted_data_notice": False,
                    }
                ),
                encoding="utf-8",
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "accept-zcode-manifest",
                        "--manifest",
                        str(manifest),
                        "--secret-scan-result",
                        "pass",
                    ]
                )

            self.assertEqual(exit_code, 1)
            violations = json.loads(output.getvalue())["violations"]
            self.assertIn("schema_version", violations)
            self.assertIn("untrusted_data_notice", violations)
            self.assertIn("diffstat_insertions_invalid", violations)

    def test_experiment_arm_logging_only_does_not_change_default_workflow(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main(["describe-token-reduction-arm", "--arm", "baseline_plus_logging_only"])

        self.assertEqual(exit_code, 0)
        payload = json.loads(output.getvalue())
        arm = payload["baseline_plus_logging_only"]
        self.assertFalse(arm["default_workflow_changed"])
        self.assertEqual(arm["feature_flags"], ["context_intake_logging"])

    def test_codex_acceptance_schema_declares_emitted_gate_fields(self):
        schema = json.loads(Path("docs/zcode-token-reduction-v2/schemas/codex_acceptance.schema.json").read_text(encoding="utf-8"))

        self.assertIn("violations", schema["properties"])
        self.assertIn("green_path_acceptance", schema["properties"])
        self.assertIn("violations", schema["required"])
        self.assertIn("green_path_acceptance", schema["required"])

    def test_direct_harness_non_llm_planner_scaffold_builds_packet(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = root / "task.json"
            packet = root / "packet.json"
            task.write_text(
                json.dumps(
                    {
                        "objective": "change one file",
                        "allowed_files": ["src/app.py"],
                        "validation_commands": ["python3 -m unittest"],
                        "acceptance_criteria": ["tests pass"],
                    }
                ),
                encoding="utf-8",
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "build-zcode-direct-harness-packet",
                        "--task-definition",
                        str(task),
                        "--out",
                        str(packet),
                    ]
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertTrue(payload["scaffolded"])
            self.assertFalse(payload["codex_planning_required"])
            self.assertEqual(payload["allowed_files"], ["src/app.py"])

    def test_token_reduction_summary_reports_missing_usage_as_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            usage_ledger = root / "usage.jsonl"
            context_ledger = root / "context.jsonl"
            summary = root / "summary.md"
            usage_ledger.write_text(
                json.dumps(
                    {
                        "experiment_id": "exp-missing",
                        "task_id": "task-a",
                        "mode": "zcode_nested_codex",
                        "phase": "implementation",
                        "attempt_valid_for_benchmark": True,
                        "invalid_measurement": False,
                        "exclusion_reason": None,
                        "usage": {
                            "usage_missing": True,
                            "effective_codex_work": None,
                        },
                        "quality": {
                            "validation_result": "pass",
                            "scope_safety": "pass",
                        },
                        "failure": {"failed": False},
                        "user_impact": {},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            context_ledger.write_text("", encoding="utf-8")

            exit_code = main(
                [
                    "summarize-token-reduction",
                    "--usage-ledger",
                    str(usage_ledger),
                    "--context-ledger",
                    str(context_ledger),
                    "--experiment-id",
                    "exp-missing",
                    "--out",
                    str(summary),
                ]
            )

            self.assertEqual(exit_code, 0)
            text = summary.read_text(encoding="utf-8")
            self.assertIn("| benchmark_clean_usage | 1 | unavailable | 1 |", text)
            self.assertIn("| operator_total_usage | 1 | unavailable | 1 |", text)
            self.assertIn("| task-a | zcode_nested_codex | implementation | 1 | unavailable |", text)
            self.assertIn("no fully measured usage rows", text)

    def test_build_strict_contract_expands_rubric_and_records_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = root / "contract.json"
            ledger = root / "contract-ledger.jsonl"

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "build-strict-contract",
                        "--rubric-id",
                        "billing_cent_rounding.v1",
                        "--task-id",
                        "billing-credit-contract",
                        "--allowed-file",
                        "src/billing.py",
                        "--validation-command",
                        "python3 -m pytest tests/test_billing.py",
                        "--out",
                        str(contract),
                        "--contract-ledger",
                        str(ledger),
                    ]
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["schema_version"], "task_contract.v1")
            self.assertEqual(payload["contract_id"], "billing-credit-contract@billing_cent_rounding.v1")
            self.assertEqual(payload["allowed_files"], ["src/billing.py"])
            self.assertEqual(payload["risk_level"], "L2")
            self.assertEqual(payload["self_audit_schema_ref"], "docs/zcode-strict-contract-v3/schemas/zcode_self_audit.schema.json")
            self.assertIn("round the aggregate once", payload["goal"])
            self.assertIn("do not floor, truncate, or round each row", payload["requirements"][0]["must"])
            record = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(record["schema_version"], "contract_ledger.v1")
            self.assertTrue(record["contract_size"]["expanded_by_non_llm"])
            self.assertTrue(record["contract_generation"]["usage_missing"])

    def test_accept_strict_contract_audit_accepts_complete_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = self._build_strict_contract(root)
            self_audit = self._write_self_audit(root, contract)
            acceptance = root / "acceptance.json"
            trace_ledger = root / "trace.jsonl"
            acceptance_ledger = root / "acceptance.jsonl"
            roi_ledger = root / "roi.jsonl"

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "accept-strict-contract-audit",
                        "--contract",
                        str(root / "contract.json"),
                        "--self-audit",
                        str(self_audit),
                        "--acceptance-out",
                        str(acceptance),
                        "--mode",
                        "green_path_skip",
                        "--validation-result",
                        "pass",
                        "--validation-exit-code",
                        "0",
                        "--changed-file",
                        "src/billing.py",
                        "--files-changed",
                        "1",
                        "--insertions",
                        "2",
                        "--deletions",
                        "1",
                        "--trace-ledger",
                        str(trace_ledger),
                        "--acceptance-ledger",
                        str(acceptance_ledger),
                        "--roi-ledger",
                        str(roi_ledger),
                    ]
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertTrue(payload["accepted"])
            self.assertEqual(payload["violations"], [])
            self.assertEqual(payload["codex_acceptance_audit"]["mode"], "green_path_skip")
            self.assertFalse(payload["codex_acceptance_audit"]["full_diff_read"])
            self.assertEqual(payload["trace_coverage"]["requirements_with_evidence"], 3)
            self.assertEqual(json.loads(acceptance_ledger.read_text(encoding="utf-8"))["schema_version"], "codex_acceptance_audit.v1")
            self.assertEqual(json.loads(roi_ledger.read_text(encoding="utf-8"))["estimate_method"], "unavailable")

    def test_accept_strict_contract_audit_fails_missing_required_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = self._build_strict_contract(root)
            audit = self._self_audit_payload(contract)
            audit["requirements"][0]["evidence"] = [
                {"type": "changed_file", "ref": "src/billing.py", "summary": "changed implementation"}
            ]
            self_audit = root / "self-audit.json"
            self_audit.write_text(json.dumps(audit), encoding="utf-8")

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "accept-strict-contract-audit",
                        "--contract",
                        str(root / "contract.json"),
                        "--self-audit",
                        str(self_audit),
                        "--validation-result",
                        "pass",
                        "--validation-exit-code",
                        "0",
                        "--changed-file",
                        "src/billing.py",
                        "--trace-ledger",
                        str(root / "trace.jsonl"),
                        "--acceptance-ledger",
                        str(root / "acceptance.jsonl"),
                        "--roi-ledger",
                        str(root / "roi.jsonl"),
                    ]
                )

            self.assertEqual(exit_code, 1)
            payload = json.loads(output.getvalue())
            self.assertFalse(payload["accepted"])
            self.assertIn("REQ-001:missing_evidence:validation_result", payload["violations"])

    def test_accept_strict_contract_audit_fails_blocked_risk_and_deviation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = self._build_strict_contract(root)
            audit = self._self_audit_payload(contract)
            audit["overall_status"] = "blocked"
            audit["blocked_reasons"] = ["allowed file scope cannot verify REQ-001"]
            audit["risk_flags"] = ["diff budget exceeded"]
            audit["deviations_from_plan"] = [
                {
                    "plan_id": "PLAN-001",
                    "reason": "changed a different implementation point",
                    "impact": "medium",
                    "requires_codex_audit": True,
                }
            ]
            self_audit = root / "self-audit.json"
            self_audit.write_text(json.dumps(audit), encoding="utf-8")

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "accept-strict-contract-audit",
                        "--contract",
                        str(root / "contract.json"),
                        "--self-audit",
                        str(self_audit),
                        "--validation-result",
                        "pass",
                        "--validation-exit-code",
                        "0",
                        "--changed-file",
                        "src/billing.py",
                        "--trace-ledger",
                        str(root / "trace.jsonl"),
                        "--acceptance-ledger",
                        str(root / "acceptance.jsonl"),
                        "--roi-ledger",
                        str(root / "roi.jsonl"),
                    ]
                )

            self.assertEqual(exit_code, 1)
            payload = json.loads(output.getvalue())
            self.assertFalse(payload["accepted"])
            self.assertIn("overall_status:blocked", payload["violations"])
            self.assertIn("deviations_from_plan", payload["violations"])
            self.assertIn("risk_flags", payload["violations"])

    def test_accept_strict_contract_audit_normalizes_live_self_audit_variants(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = self._build_strict_contract(root)
            audit = self._self_audit_payload(contract)
            audit.pop("overall_status")
            audit.pop("requirements")
            audit["requirements_evidence"] = {
                trace["id"]: dict(trace, status="evidence_provided_local_validation_unverified")
                for trace in self._self_audit_payload(contract)["requirements"]
            }
            audit["edge_case_coverage"] = {
                "EDGE-001": {
                    "case": "fractional cent boundary",
                    "expected": "no over-crediting",
                    "evidence": "manual trace only",
                }
            }
            audit["changed_files"] = [{"path": "src/billing.py", "allowed": True}]
            audit["risk_flags"] = [
                {
                    "id": "RISK-001",
                    "area": "validation",
                    "detail": "worker could not run validation",
                    "level": "L2",
                }
            ]
            self_audit = root / "self-audit.json"
            self_audit.write_text(json.dumps(audit), encoding="utf-8")

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "accept-strict-contract-audit",
                        "--contract",
                        str(root / "contract.json"),
                        "--self-audit",
                        str(self_audit),
                        "--validation-result",
                        "pass",
                        "--validation-exit-code",
                        "0",
                        "--changed-file",
                        "src/billing.py",
                    ]
                )

            self.assertEqual(exit_code, 1)
            payload = json.loads(output.getvalue())
            self.assertFalse(payload["accepted"])
            self.assertIn("overall_status:None", payload["violations"])
            self.assertIn("risk_flags", payload["violations"])
            self.assertIn("REQ-001:blocking_not_satisfied", payload["violations"])
            self.assertIn(
                "RISK-001: worker could not run validation",
                payload["codex_acceptance_audit"]["risk_flags"],
            )

    def test_accept_strict_contract_audit_backfills_worker_validation_skip_from_supervisor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = self._build_strict_contract(root)
            audit = self._self_audit_payload(contract)
            audit["overall_status"] = "blocked"
            audit["validation"] = {
                "result": "skipped",
                "summary": "local validation could not run; Codex supervisor rerun is authoritative",
            }
            audit["blocked_reasons"] = ["local validation could not run because Bash permission was unavailable"]
            audit["unresolved_questions"] = ["validation skipped locally; supervisor must confirm"]
            audit["risk_flags"] = ["local validation skipped; supervisor validation required"]
            audit["deviations_from_plan"] = [{"summary": "No deviations from the plan."}]
            for trace in audit["requirements"]:
                skipped_types = {"validation_result"}
                if trace["id"] == "REQ-002":
                    skipped_types.add("diffstat")
                trace["evidence"] = [item for item in trace["evidence"] if item["type"] not in skipped_types]
                for item in trace["evidence"]:
                    if item["type"] == "changed_file":
                        item["ref"] = f"{item['ref']}:6"
            self_audit = root / "self-audit.json"
            self_audit.write_text(json.dumps(audit), encoding="utf-8")

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "accept-strict-contract-audit",
                        "--contract",
                        str(root / "contract.json"),
                        "--self-audit",
                        str(self_audit),
                        "--validation-result",
                        "pass",
                        "--validation-exit-code",
                        "0",
                        "--changed-file",
                        "src/billing.py",
                        "--files-changed",
                        "1",
                    ]
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertTrue(payload["accepted"])
            self.assertTrue(payload["validation_backfill"]["used"])
            self.assertEqual(payload["validation_backfill"]["source"], "codex_supervisor")
            self.assertTrue(payload["diffstat_backfill"]["used"])

    def test_accept_strict_contract_audit_keeps_non_validation_risk_after_backfill(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = self._build_strict_contract(root)
            audit = self._self_audit_payload(contract)
            audit["overall_status"] = "blocked"
            audit["validation"] = {"result": "skipped", "summary": "validation skipped locally"}
            audit["blocked_reasons"] = ["local validation could not run"]
            audit["risk_flags"] = [
                "local validation skipped",
                "diff budget changed implementation plan",
            ]
            self_audit = root / "self-audit.json"
            self_audit.write_text(json.dumps(audit), encoding="utf-8")

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "accept-strict-contract-audit",
                        "--contract",
                        str(root / "contract.json"),
                        "--self-audit",
                        str(self_audit),
                        "--validation-result",
                        "pass",
                        "--validation-exit-code",
                        "0",
                        "--changed-file",
                        "src/billing.py",
                    ]
                )

            self.assertEqual(exit_code, 1)
            payload = json.loads(output.getvalue())
            self.assertTrue(payload["validation_backfill"]["used"])
            self.assertIn("risk_flags", payload["violations"])

    def test_accept_strict_contract_audit_backfills_unknown_when_only_validation_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = self._build_strict_contract(root)
            audit = self._self_audit_payload(contract)
            audit["overall_status"] = "unknown"
            audit["validation"] = {
                "result": "skipped",
                "summary": "local validation could not run; supervisor validation is authoritative",
            }
            audit["risk_flags"] = ["local validation skipped; supervisor validation required"]
            self_audit = root / "self-audit.json"
            self_audit.write_text(json.dumps(audit), encoding="utf-8")

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "accept-strict-contract-audit",
                        "--contract",
                        str(root / "contract.json"),
                        "--self-audit",
                        str(self_audit),
                        "--validation-result",
                        "pass",
                        "--validation-exit-code",
                        "0",
                        "--changed-file",
                        "src/billing.py",
                        "--files-changed",
                        "1",
                    ]
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertTrue(payload["accepted"])
            self.assertTrue(payload["validation_backfill"]["used"])

    def test_accept_strict_contract_audit_backfills_unknown_requirement_when_only_validation_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = self._build_strict_contract(root)
            audit = self._self_audit_payload(contract)
            audit["overall_status"] = "unknown"
            audit["validation"] = {
                "result": "unknown",
                "summary": "local validation could not run because Bash was unavailable; supervisor validation is authoritative",
            }
            audit["risk_flags"] = [
                "Exact behavior is only confirmed by the authoritative npm test run, which is pending."
            ]
            for trace in audit["requirements"]:
                if trace["id"] != "REQ-001":
                    continue
                trace["status"] = "unknown"
                trace["evidence"] = [
                    item for item in trace["evidence"] if item["type"] != "validation_result"
                ]
                trace["evidence"].append(
                    {
                        "type": "note",
                        "ref": "validation:npm-test",
                        "summary": "Local validation could not run because Bash was unavailable; supervisor validation will backfill validation_result evidence.",
                    }
                )
            self_audit = root / "self-audit.json"
            self_audit.write_text(json.dumps(audit), encoding="utf-8")

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "accept-strict-contract-audit",
                        "--contract",
                        str(root / "contract.json"),
                        "--self-audit",
                        str(self_audit),
                        "--validation-result",
                        "pass",
                        "--validation-exit-code",
                        "0",
                        "--changed-file",
                        "src/billing.py",
                        "--files-changed",
                        "1",
                    ]
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertTrue(payload["accepted"])
            self.assertTrue(payload["validation_backfill"]["used"])
            self.assertEqual(payload["trace_coverage"]["requirements_claimed_satisfied"], 2)
            self.assertEqual(payload["trace_coverage"]["requirements_effectively_satisfied"], 3)

    def test_accept_strict_contract_audit_fails_diff_budget_without_risk_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = self._build_strict_contract(root)
            self_audit = self._write_self_audit(root, contract)

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "accept-strict-contract-audit",
                        "--contract",
                        str(root / "contract.json"),
                        "--self-audit",
                        str(self_audit),
                        "--validation-result",
                        "pass",
                        "--validation-exit-code",
                        "0",
                        "--changed-file",
                        "src/billing.py",
                        "--files-changed",
                        "2",
                        "--insertions",
                        "99",
                        "--trace-ledger",
                        str(root / "trace.jsonl"),
                        "--acceptance-ledger",
                        str(root / "acceptance.jsonl"),
                        "--roi-ledger",
                        str(root / "roi.jsonl"),
                    ]
                )

            self.assertEqual(exit_code, 1)
            payload = json.loads(output.getvalue())
            self.assertIn("diff_budget_exceeded_without_risk_flag", payload["violations"])

    def test_strict_contract_summary_reports_green_path_and_unavailable_roi(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = self._build_strict_contract(root)
            self_audit = self._write_self_audit(root, contract)
            trace_ledger = root / "trace.jsonl"
            acceptance_ledger = root / "acceptance.jsonl"
            roi_ledger = root / "roi.jsonl"
            summary = root / "summary.json"

            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = main(
                    [
                        "accept-strict-contract-audit",
                        "--contract",
                        str(root / "contract.json"),
                        "--self-audit",
                        str(self_audit),
                        "--validation-result",
                        "pass",
                        "--validation-exit-code",
                        "0",
                        "--changed-file",
                        "src/billing.py",
                        "--files-changed",
                        "1",
                        "--green-path-non-llm",
                        "--shadow-codex-audit-enabled",
                        "--shadow-codex-audit-result",
                        "agree",
                        "--trace-ledger",
                        str(trace_ledger),
                        "--acceptance-ledger",
                        str(acceptance_ledger),
                        "--roi-ledger",
                        str(roi_ledger),
                    ]
                )
            self.assertEqual(exit_code, 0)

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "summarize-strict-contract",
                        "--contract-ledger",
                        str(root / "contract-ledger.jsonl"),
                        "--trace-ledger",
                        str(trace_ledger),
                        "--acceptance-ledger",
                        str(acceptance_ledger),
                        "--roi-ledger",
                        str(roi_ledger),
                        "--out",
                        str(summary),
                    ]
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["primary_metric"], "effective_codex_work")
            self.assertEqual(payload["quality_gates"][0]["mode"], "green_path_skip")
            self.assertEqual(payload["contract_roi"][0]["estimate_method"], "unavailable")
            self.assertFalse(payload["contract_roi"][0]["measured"])
            self.assertEqual(payload["phase_split_measurement"]["phases"][0], "contract_generation")
            self.assertEqual(payload["wall_clock_breakdown"]["status"], "unavailable")

    def _build_strict_contract(self, root: Path) -> dict:
        contract = root / "contract.json"
        ledger = root / "contract-ledger.jsonl"
        with contextlib.redirect_stdout(io.StringIO()):
            exit_code = main(
                [
                    "build-strict-contract",
                    "--rubric-id",
                    "billing_cent_rounding.v1",
                    "--task-id",
                    "billing-credit-contract",
                    "--allowed-file",
                    "src/billing.py",
                    "--validation-command",
                    "python3 -m pytest tests/test_billing.py",
                    "--out",
                    str(contract),
                    "--contract-ledger",
                    str(ledger),
                ]
            )
        self.assertEqual(exit_code, 0)
        return json.loads(contract.read_text(encoding="utf-8"))

    def _self_audit_payload(self, contract: dict) -> dict:
        requirements = []
        for requirement in contract["requirements"]:
            evidence = []
            for evidence_type in requirement["evidence_required"]:
                evidence.append(
                    {
                        "type": evidence_type,
                        "ref": "src/billing.py" if evidence_type == "changed_file" else f"{evidence_type}.json",
                        "summary": f"{evidence_type} evidence present",
                    }
                )
            requirements.append({"id": requirement["id"], "status": "satisfied", "evidence": evidence})
        return {
            "schema_version": "zcode_self_audit.v1",
            "contract_id": contract["contract_id"],
            "task_id": contract["task_id"],
            "overall_status": "pass",
            "requirements": requirements,
            "validation": {"result": "pass", "summary": "validation passed"},
            "deviations_from_plan": [],
            "unresolved_questions": [],
            "risk_flags": [],
            "blocked_reasons": [],
        }

    def _write_self_audit(self, root: Path, contract: dict) -> Path:
        self_audit = root / "self-audit.json"
        self_audit.write_text(json.dumps(self._self_audit_payload(contract)), encoding="utf-8")
        return self_audit


if __name__ == "__main__":
    unittest.main()
