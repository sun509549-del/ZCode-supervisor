import json
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import tools.zcode_eval.goal_p_codex_worker_comparison as comparison
from tools.zcode_eval.goal_p_codex_worker_comparison import (
    CLAIM_FAMILY,
    CODEX_WORKER_SANDBOX,
    OUTCOME_MEASURED,
    OUTCOME_USAGE_UNAVAILABLE,
    ROUTE_USED,
    build_parser,
    build_report,
    codex_worker_writable_dirs,
    load_zcode_task_packets,
    measured_codex_usage,
    packet_task_contract,
    packet_validation_command,
    prepare_worker_audit_dir,
    safe_fixture_name,
    safe_task_id,
    strict_acceptance,
    worker_audit_output_path,
    worker_prompt,
)
from tools.zcode_eval.goal_p_codex_worker_audit import normalize_self_audit, workspace_isolation_result
from tools.zcode_eval.goal_p_codex_worker_report import (
    OUTCOME_STRICT_UNAVAILABLE_AFTER_REPAIR,
    OUTCOME_USAGE_UNAVAILABLE_AFTER_REPAIR,
)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def packet(task_id: str, allowed: str = "src/credits.js") -> dict:
    return {
        "objective": f"Fix {task_id}",
        "allowed_files": [allowed],
        "validation_commands": ["npm test"],
        "acceptance_criteria": ["Validation command passes."],
        "max_changed_files": 1,
        "strict_contract": {
            "task_contract": {
                "schema_version": "task_contract.v1",
                "contract_id": f"{task_id}@rubric.v1",
                "task_id": task_id,
                "rubric_id": "rubric.v1",
                "allowed_files": [allowed],
                "forbidden_files": [],
                "requirements": [
                    {
                        "id": "REQ-001",
                        "blocking": True,
                        "evidence_required": ["validation_result", "changed_file"],
                    }
                ],
                "edge_cases": [{"id": "EDGE-001", "evidence_required": ["validation_result"]}],
            }
        },
    }


def zcode_final() -> dict:
    return {
        "claim_family": "zcode_required_20_live",
        "final_outcome": "zcode_20_live_partial_measured",
        "task_count": 20,
        "delegated_rows": 20,
        "strict_green_count": 18,
        "worker_token_measured_count": 20,
        "worker_total_tokens": 9729458,
    }


def row(task_id: str, *, measured: bool = True, tokens: int = 10, strict: bool = True) -> dict:
    return {
        "task_id": task_id,
        "route_used": ROUTE_USED,
        "claim_family": CLAIM_FAMILY,
        "strict_accepted": strict,
        "final_validation_rc": 0,
        "acceptance_rc": 0 if strict else 1,
        "codex_worker_usage_status": "measured" if measured else "unavailable",
        "codex_worker_total_tokens": tokens if measured else None,
    }


class GoalPCodexWorkerComparisonTests(unittest.TestCase):
    def test_report_measured_only_when_all_worker_rows_have_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence = root / "evidence"
            write_json(evidence / "final-outcome.json", zcode_final())
            rows = [row(f"task-{index:02d}", tokens=7) for index in range(20)]

            report = build_report(
                comparison_root=root / "comparison",
                evidence_root=evidence,
                archive_root=None,
                rows=rows,
                min_task_count=20,
            )

            self.assertEqual(report["final_outcome"], OUTCOME_MEASURED)
            self.assertEqual(report["codex_worker_usage_status"], "measured")
            self.assertEqual(report["codex_worker_total_tokens"], 140)
            self.assertEqual(report["zcode_worker_total_tokens"], 9729458)
            self.assertEqual(report["zcode_strict_green_count"], 18)
            self.assertFalse(report["total_workflow_savings_claimable"])

    def test_unavailable_worker_usage_stays_null(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence = root / "evidence"
            write_json(evidence / "final-outcome.json", zcode_final())
            rows = [row(f"task-{index:02d}", measured=index != 3) for index in range(20)]

            report = build_report(
                comparison_root=root / "comparison",
                evidence_root=evidence,
                archive_root=None,
                rows=rows,
                min_task_count=20,
            )

            self.assertEqual(report["final_outcome"], OUTCOME_USAGE_UNAVAILABLE)
            self.assertEqual(report["codex_worker_usage_status"], "partial")
            self.assertIsNone(report["codex_worker_total_tokens"])
            self.assertEqual(report["blocker"], "codex_worker_usage_unavailable")

    def test_repair_attempt_records_exact_usage_terminal_blocker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence = root / "evidence"
            write_json(evidence / "final-outcome.json", zcode_final())
            rows = [row(f"task-{index:02d}", measured=index != 3) for index in range(20)]

            report = build_report(
                comparison_root=root / "comparison",
                evidence_root=evidence,
                archive_root=None,
                rows=rows,
                min_task_count=20,
                repair_attempted=True,
            )

            self.assertEqual(report["final_outcome"], OUTCOME_USAGE_UNAVAILABLE_AFTER_REPAIR)
            self.assertEqual(report["blocker"], OUTCOME_USAGE_UNAVAILABLE_AFTER_REPAIR)
            self.assertIsNone(report["codex_worker_total_tokens"])

    def test_repair_attempt_records_exact_strict_terminal_blocker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence = root / "evidence"
            write_json(evidence / "final-outcome.json", zcode_final())
            rows = [row(f"task-{index:02d}", strict=False) for index in range(20)]

            report = build_report(
                comparison_root=root / "comparison",
                evidence_root=evidence,
                archive_root=None,
                rows=rows,
                min_task_count=20,
                repair_attempted=True,
            )

            self.assertEqual(report["final_outcome"], OUTCOME_STRICT_UNAVAILABLE_AFTER_REPAIR)
            self.assertEqual(report["blocker"], OUTCOME_STRICT_UNAVAILABLE_AFTER_REPAIR)

    def test_measured_codex_usage_rejects_missing_and_zero(self):
        missing_status, missing_tokens, _ = measured_codex_usage(None)
        zero_status, zero_tokens, _ = measured_codex_usage({"usage_missing": False, "effective_codex_work": 0})
        measured_status, measured_tokens, _ = measured_codex_usage({"usage_missing": False, "effective_codex_work": 42})

        self.assertEqual(missing_status, "unavailable")
        self.assertIsNone(missing_tokens)
        self.assertEqual(zero_status, "unavailable")
        self.assertIsNone(zero_tokens)
        self.assertEqual(measured_status, "measured")
        self.assertEqual(measured_tokens, 42)

    def test_rejects_unsafe_task_ids_before_path_use(self):
        self.assertEqual(safe_task_id("billing-credit-contract-01"), "billing-credit-contract-01")
        for value in ["../../victim", "/tmp/victim", "task/child", r"task\\child", "..", "task..bad", ""]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    safe_task_id(value)

    def test_rejects_unsafe_fixture_names_before_copy(self):
        self.assertEqual(safe_fixture_name("billing-credit-contract"), "billing-credit-contract")
        for value in ["../../victim", "/tmp/victim", "fixture/child", r"fixture\\child", "..", "fixture..bad", ""]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    safe_fixture_name(value)

    def test_workspace_isolation_detects_main_repo_status_changes(self):
        before = {"ok": True, "lines": [" M GOAL.md"], "error": None}
        after = {"ok": True, "lines": [" M GOAL.md", " M README.md"], "error": None}

        clean = workspace_isolation_result(before, before)
        dirty = workspace_isolation_result(before, after)

        self.assertTrue(clean["workspace_isolation_ok"])
        self.assertFalse(dirty["workspace_isolation_ok"])
        self.assertEqual(dirty["workspace_isolation_violations"], ["main_repo_status_changed"])

    def test_normalizes_codex_audit_shape_before_strict_acceptance(self):
        audit = {
            "schema_version": "zcode_self_audit.v1",
            "contract_id": "task-01@rubric.v1",
            "task_id": "task-01",
            "overall_status": "pass",
            "requirements": [{"id": "REQ-001", "status": "pass", "evidence": [
                {"type": "validation_result", "ref": "npm test", "summary": "passed"},
                {"type": "changed_file", "ref": "src/credits.js", "summary": "changed"},
            ]}],
            "edge_cases": [{"id": "EDGE-001", "status": "pass", "evidence": [
                {"type": "validation_result", "ref": "npm test", "summary": "passed"}
            ]}],
            "validation": [{"command": "npm test", "result": "passed", "evidence": [
                {"type": "validation_result", "ref": "npm test", "summary": "2 tests passed"}
            ]}],
            "deviations_from_plan": [],
            "unresolved_questions": [],
            "risk_flags": [],
            "blocked_reasons": [],
        }
        normalized = normalize_self_audit(audit)

        self.assertEqual(normalized["requirements"][0]["status"], "satisfied")
        self.assertEqual(normalized["edge_cases"][0]["status"], "covered")
        self.assertEqual(normalized["validation"]["result"], "pass")

    def test_strict_acceptance_uses_normalized_codex_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_contract = packet("task-01")["strict_contract"]["task_contract"]
            self_audit = root / "zcode_self_audit.json"
            normalized = root / "normalized-self-audit.json"
            write_json(self_audit, {
                "schema_version": "zcode_self_audit.v1",
                "contract_id": "task-01@rubric.v1",
                "task_id": "task-01",
                "overall_status": "pass",
                "requirements": [{"id": "REQ-001", "status": "pass", "evidence": [
                    {"type": "validation_result", "ref": "npm test", "summary": "passed"},
                    {"type": "changed_file", "ref": "src/credits.js", "summary": "changed"},
                ]}],
                "edge_cases": [],
                "validation": [{"command": "npm test", "result": "passed", "summary": "passed"}],
                "deviations_from_plan": [],
                "unresolved_questions": [],
                "risk_flags": [],
                "blocked_reasons": [],
            })

            rc, result = strict_acceptance(
                contract=task_contract,
                self_audit=self_audit,
                normalized_audit=normalized,
                changed=["src/credits.js"],
                diffs=[{"file": "src/credits.js", "added": 1, "deleted": 0}],
                validation_rc=0,
            )

            self.assertEqual(rc, 0)
            self.assertTrue(result["accepted"])
            self.assertTrue(normalized.exists())

    def test_worker_timeout_defaults_long_enough_for_goal_p(self):
        args = build_parser().parse_args(["--zcode-evidence-root", "evidence"])
        self.assertEqual(args.worker_timeout, 1800)

    def test_goal_p_codex_worker_uses_workspace_write_sandbox(self):
        task_dir = Path("/tmp/task-dir")

        self.assertEqual(CODEX_WORKER_SANDBOX, "workspace-write")
        self.assertEqual(codex_worker_writable_dirs(task_dir), [task_dir])

    def test_prepares_worker_audit_dir_for_workspace_write_sandbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            audit_dir = prepare_worker_audit_dir(workspace)

            self.assertEqual(audit_dir, workspace / ".codex" / "zcode" / "runs")
            self.assertTrue(audit_dir.is_dir())

    def test_worker_audit_output_path_is_attempt_specific(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "task"

            first = worker_audit_output_path(task_dir)
            second = worker_audit_output_path(task_dir)

            self.assertNotEqual(first, task_dir / "zcode_self_audit.json")
            self.assertNotEqual(first, second)
            self.assertEqual(first.parent, task_dir)

    def test_loads_same_packet_tasks_from_zcode_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            packet_path = root / "work/tasks/billing/.codex/zcode/packets/packet.json"
            write_json(packet_path, packet("billing-credit-contract-01"))
            write_json(
                root / "state.json",
                {
                    "tasks": [
                        {
                            "task_id": "billing-credit-contract-01",
                            "fixture": "billing-credit-contract",
                            "allowed_file": "src/credits.js",
                            "validation": "npm test",
                            "packet_path": ".local/zcode-20-resumable/work/tasks/billing/.codex/zcode/packets/packet.json",
                        }
                    ]
                },
            )

            rows = load_zcode_task_packets(root, 1)

            self.assertEqual(rows[0]["task"]["task_id"], "billing-credit-contract-01")
            self.assertEqual(rows[0]["packet"]["objective"], "Fix billing-credit-contract-01")

    def test_rejects_packet_paths_that_escape_evidence_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(
                root / "state.json",
                {
                    "tasks": [
                        {
                            "task_id": "billing-credit-contract-01",
                            "fixture": "billing-credit-contract",
                            "allowed_file": "src/credits.js",
                            "validation": "npm test",
                            "packet_path": "../outside.json",
                        }
                    ]
                },
            )

            with self.assertRaises(ValueError):
                load_zcode_task_packets(root, 1)

    def test_worker_prompt_rebinds_packet_without_zcode_execution(self):
        text = worker_prompt(
            {
                "task_id": "policy-reason-contract-02",
                "allowed_file": "src/policy.js",
                "validation": "npm test",
            },
            packet("policy-reason-contract-02", allowed="src/policy.js"),
        )

        self.assertIn("Do not run ZCode", text)
        self.assertIn("codex-as-worker baseline", text.lower())
        self.assertIn("Use exact requirement statuses", text)
        self.assertIn("validation as one object", text)
        self.assertIn("zcode_self_audit.v1", text)
        self.assertIn("policy-reason-contract-02", text)

    def test_worker_prompt_preserves_full_original_packet_prompt(self):
        pkt = packet("policy-reason-contract-02", allowed="src/policy.js")
        pkt["workspace"] = "/old/zcode/workspace"
        pkt["prompt"] = (
            "You are a ZCode worker under Codex audit.\n"
            "Workspace: /old/zcode/workspace\n"
            "Forbidden files: README.md\n"
            "Required final report shape: Changed files, Validation result\n"
            "What not to do: Do not edit tests.\n"
        )

        text = worker_prompt(
            {"task_id": "policy-reason-contract-02", "allowed_file": "src/policy.js"},
            pkt,
            audit_output=Path("/tmp/task/zcode_self_audit-current.json"),
            workspace=Path("/tmp/isolated/workspace"),
        )

        self.assertIn("Forbidden files: README.md", text)
        self.assertIn("Required final report shape: Changed files, Validation result", text)
        self.assertIn("What not to do: Do not edit tests.", text)
        self.assertIn("Workspace: /tmp/isolated/workspace", text)
        self.assertNotIn("/old/zcode/workspace", text)
        self.assertIn("/tmp/task/zcode_self_audit-current.json", text)

    def test_packet_validation_command_overrides_stale_state_validation(self):
        task = {"validation": "npm run stale-state-test"}
        pkt = packet("policy-reason-contract-02", allowed="src/policy.js")
        pkt["validation_commands"] = ["npm run packet-test"]

        self.assertEqual(packet_validation_command(pkt, task), "npm run packet-test")
        text = worker_prompt({"task_id": "policy-reason-contract-02", **task}, pkt)
        self.assertIn("Validation command: npm run packet-test", text)
        self.assertNotIn("npm run stale-state-test", text)

    def test_evaluate_workspace_validates_with_packet_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = {"validation": "npm run stale-state-test"}
            pkt = packet("policy-reason-contract-02", allowed="src/policy.js")
            pkt["validation_commands"] = ["npm run packet-test"]

            with (
                mock.patch.object(
                    comparison, "run_validation", return_value=type("Validation", (), {"rc": 0})()
                ) as run_validation,
                mock.patch.object(comparison, "changed_files", return_value=[]),
                mock.patch.object(comparison, "strict_acceptance", return_value=(0, {"ok": True})) as accept,
            ):
                sidecar = root / "task" / "zcode_self_audit-current.json"
                comparison.evaluate_workspace(
                    workspace=root / "workspace",
                    base=root / "base",
                    task=task,
                    packet=pkt,
                    task_dir=root / "task",
                    contract=packet_task_contract(pkt),
                    sidecar_audit=sidecar,
                )

            self.assertEqual(run_validation.call_args.args[1], "npm run packet-test")
            self.assertEqual(accept.call_args.kwargs["self_audit"], sidecar)

    def test_worker_prompt_can_route_audit_to_task_sidecar(self):
        text = worker_prompt(
            {
                "task_id": "policy-reason-contract-02",
                "allowed_file": "src/policy.js",
                "validation": "npm test",
            },
            packet("policy-reason-contract-02", allowed="src/policy.js"),
            audit_output=Path("/tmp/task/zcode_self_audit.json"),
        )

        self.assertIn("/tmp/task/zcode_self_audit.json", text)

    def test_checker_accepts_documented_runner_unavailable_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence = root / "evidence"
            comparison = root / "comparison"
            write_json(evidence / "final-outcome.json", zcode_final())
            terminal = {
                "schema_version": "goal_p_codex_worker_comparison.v1",
                "final_outcome": "terminal_codex_worker_runner_unavailable",
                "route_used": ROUTE_USED,
                "claim_family": CLAIM_FAMILY,
                "task_count": 0,
                "codex_worker_rows": 0,
                "codex_worker_usage_status": "unavailable",
                "codex_worker_total_tokens": None,
                "total_workflow_savings_claimable": False,
                "blocker": "codex_cli_unavailable",
                "rows": [],
            }
            terminal.update({
                "zcode_claim_family": "zcode_required_20_live",
                "zcode_worker_total_tokens": 9729458,
                "zcode_strict_green_count": 18,
            })
            write_json(comparison / "final-outcome.json", terminal)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/check_goal_p_codex_worker_comparison.py",
                    "--zcode-evidence-root",
                    str(evidence),
                    "--comparison-root",
                    str(comparison),
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout)


if __name__ == "__main__":
    unittest.main()
