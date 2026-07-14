import json
import tempfile
import unittest
from pathlib import Path

from tests.test_zcode_20_resumable_state import TestRunner, make_fixture_root
from tests.test_zcode_model_usage_db_delta import create_model_usage_db, insert_usage
from tools.zcode_eval.zcode_model_usage_db_delta import capture_before_marker
from tools.zcode_supervisor.zcode_20_resumable import (
    DB_DELTA_SOURCE_TYPE,
    ResumableRunner,
    SubprocessExecutor,
    create_initial_state,
    usage_measured,
)


class ExecutorWithDb:
    def __init__(self, db: Path):
        self.model_usage_db = db

    def run(self, task, *, repair=False):
        raise AssertionError("saved terminal state should be repaired without rerunning ZCode")


class ZCode20UsageCaptureRepairTests(unittest.TestCase):
    def test_usage_measured_accepts_usage_accounting_fallback(self):
        run = {
            "usage_accounting": {
                "worker_usage_status": "measured",
                "worker_usage_unit": "tokens",
                "worker_total_tokens": 42,
                "worker_usage_source_path": "worker-usage.jsonl",
            }
        }

        self.assertTrue(usage_measured(run))

    def test_saved_usage_failure_is_repaired_from_db_delta_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_fixture_root(root)
            db = root / "db.sqlite"
            create_model_usage_db(db, with_attribution_columns=True)
            before = capture_before_marker(db)
            insert_usage(
                db,
                total=40,
                input_tokens=30,
                output_tokens=10,
                reasoning_tokens=0,
                cache_write_tokens=0,
                cache_read_tokens=3,
                status="completed",
                started_at=before["captured_at_ms"],
                completed_at=before["captured_at_ms"],
            )
            run_path = root / "work/tasks/billing/.codex/zcode/runs/billing-credit-contract-01-route-3/zcode-run.json"
            run_path.parent.mkdir(parents=True)
            (run_path.parent / "zcode-model-usage-before.json").write_text(json.dumps(before), encoding="utf-8")
            (run_path.parent / "worker-usage.jsonl").write_text(
                json.dumps({"source_type": "old_provider_ledger", "unit": "tokens", "usage": {"total_tokens": 1}}) + "\n",
                encoding="utf-8",
            )
            run_path.write_text(
                json.dumps(
                    {
                        "ok": False,
                        "status": "audit_failed",
                        "audit": {
                            "changed_count": 1,
                            "validation": {"ok": True, "returncode": 0},
                            "strict_contract": {"accepted": False},
                        },
                        "usage_accounting": {
                            "usage_available": True,
                            "tokens_source": "zcode_cli_json_usage",
                            "tokens_used": 40,
                        },
                    }
                ),
                encoding="utf-8",
            )
            state = create_initial_state(task_count=1, work_root=root / "work", resume_command="resume")
            task = state["tasks"][0]
            task.update(
                {
                    "status": "failed",
                    "attempts": 1,
                    "blocker": "worker_usage_unavailable_after_zcode_run",
                    "run_path": str(run_path),
                    "workspace": str(root / "work/tasks/billing"),
                    "zcode_implemented": True,
                }
            )
            state["usage_capture_global_failure"] = True
            state["terminal_blocker"] = "worker_usage_unavailable_after_zcode_run"
            state_path = root / "state.json"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            runner = TestRunner(
                repo_root=root,
                state_path=state_path,
                final_outcome_path=root / "final.json",
                readiness_path=root / "readiness.json",
                manifest_path=root / "manifest.json",
                task_count=1,
                work_root=root / "work",
                executor=ExecutorWithDb(db),
            )

            final = runner.run()
            repaired = json.loads(state_path.read_text(encoding="utf-8"))
            repaired_task = repaired["tasks"][0]

            self.assertEqual(final["final_outcome"], "zcode_20_live_partial_measured")
            self.assertEqual(final["worker_token_measured_count"], 1)
            self.assertEqual(final["worker_total_tokens"], 40)
            self.assertFalse(repaired["usage_capture_global_failure"])
            self.assertTrue(repaired["usage_capture_repair_active"])
            self.assertTrue(repaired_task["usage_capture_repaired"])
            self.assertEqual(repaired_task["worker_usage_status"], "measured")
            self.assertEqual(repaired_task["worker_usage_unit"], "tokens")
            self.assertEqual(repaired_task["worker_total_tokens"], 40)
            self.assertEqual(repaired_task["worker_usage_capture_method"], DB_DELTA_SOURCE_TYPE)
            self.assertEqual(repaired_task["row_ids"], [1])
            self.assertTrue(repaired_task["worker_usage_source_path"].endswith("worker-usage.jsonl"))
            self.assertIn("usage-repair", repaired_task["worker_usage_source_path"])

    def test_subprocess_executor_uses_route_local_output_and_no_provider_ledger_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            captured = {}
            original = SubprocessExecutor.run_json_command

            def fake_run_json_command(command, *, cwd, timeout, env=None):
                if "run-packet" in command:
                    out = Path(command[command.index("--out") + 1])
                    captured["out"] = out
                    captured["env_has_provider_ledger"] = env is not None and "ZCODE_PROVIDER_USAGE_LEDGER" in env
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text(json.dumps({"ok": False, "status": "audit_failed"}), encoding="utf-8")
                    return {"ok": False, "status": "audit_failed"}
                return {"ok": True}

            try:
                SubprocessExecutor.run_json_command = staticmethod(fake_run_json_command)
                executor = SubprocessExecutor(
                    repo_root=root,
                    model_usage_db=root / "db.sqlite",
                    timeout_ms=1000,
                    validation_timeout=1,
                )
                task = {
                    "task_id": "billing-credit-contract-01",
                    "workspace": str(workspace),
                    "route_attempts": [],
                    "objective": "x",
                    "allowed_file": "src/credits.js",
                    "validation": "true",
                    "task_class": "small-fix",
                    "strict_rubric_id": "rubric",
                    "strict_risk_level": "L2",
                }

                executor.run(task)
            finally:
                SubprocessExecutor.run_json_command = original

            self.assertEqual(captured["out"].name, "zcode-run.json")
            self.assertEqual(captured["out"].parent.name, "billing-credit-contract-01-route-1")
            self.assertFalse(captured["env_has_provider_ledger"])


if __name__ == "__main__":
    unittest.main()
