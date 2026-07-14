import tempfile
import unittest
from pathlib import Path

from tools.zcode_supervisor.zcode_20_resumable import (
    OUTCOME_CODEX_TOUCHED,
    OUTCOME_PROVIDER_PAUSED,
    OUTCOME_USAGE_FAILURE,
    PROVIDER_PAUSED_STATUS,
    ResumableRunner,
    create_initial_state,
    final_outcome_errors,
    validate_final_outcome,
    write_json,
)
from tests.test_zcode_20_resumable_state import make_fixture_root


def green(tokens=13):
    return {
        "ok": True,
        "status": "success",
        "changed_count": 1,
        "strict_accepted": True,
        "final_validation_rc": 0,
        "worker_usage_status": "measured",
        "worker_usage_unit": "tokens",
        "worker_total_tokens": tokens,
        "worker_usage_source_path": "worker-usage.jsonl",
        "worker_usage_capture_method": "zcode_cli_model_usage_db_delta",
        "row_ids": [tokens],
    }


class SequenceExecutor:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def run(self, task, *, repair=False):
        self.calls.append((task["task_id"], repair))
        if self.results:
            result = self.results.pop(0)
            return result() if callable(result) else result
        return green()


class DirtyAfterFirstCheckRunner(ResumableRunner):
    def __init__(self, *args, dirty_after=1, **kwargs):
        super().__init__(*args, **kwargs)
        self.checks = 0
        self.dirty_after = dirty_after

    def install_repo(self, workspace: Path) -> None:
        return None

    def target_artifacts_dirty(self) -> bool:
        self.checks += 1
        return self.checks > self.dirty_after


class TestRunner(ResumableRunner):
    def install_repo(self, workspace: Path) -> None:
        return None

    def target_artifacts_dirty(self) -> bool:
        return False


def make_runner(root: Path, executor, task_count=20):
    return TestRunner(
        repo_root=root,
        state_path=root / "state.json",
        final_outcome_path=root / "final.json",
        readiness_path=root / "readiness.json",
        manifest_path=root / "manifest.json",
        task_count=task_count,
        work_root=root / "work",
        executor=executor,
        cooldown_seconds=(0, 0, 0, 0),
    )


class ZCode20ResumableRunnerTests(unittest.TestCase):
    def test_twenty_task_green_outcome_validates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_fixture_root(root)
            runner = make_runner(root, SequenceExecutor([lambda: green(5)] * 20))

            final = runner.run()

            self.assertEqual(final["final_outcome"], "zcode_20_live_green_measured")
            self.assertEqual(final["task_count"], 20)
            self.assertEqual(final["delegated_rows"], 20)
            self.assertEqual(final["strict_green_count"], 20)
            self.assertEqual(final["worker_token_measured_count"], 20)
            self.assertEqual(final["worker_total_tokens"], 100)
            self.assertEqual(final["route_used_summary"]["codex_fallback"], 0)
            validate_final_outcome(final)

    def test_usage_unavailable_after_zcode_run_is_global_terminal_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_fixture_root(root)
            runner = make_runner(
                root,
                SequenceExecutor([
                    {
                        "ok": True,
                        "status": "success",
                        "changed_count": 1,
                        "strict_accepted": True,
                        "final_validation_rc": 0,
                        "worker_usage_status": "unavailable",
                        "worker_usage_unit": "unknown",
                        "worker_total_tokens": None,
                        "no_usage_reason": "zcode_model_usage_no_new_rows",
                    }
                ]),
            )

            final = runner.run()

            self.assertEqual(final["final_outcome"], OUTCOME_USAGE_FAILURE)
            self.assertEqual(final["terminal_blocker"], "zcode_model_usage_no_new_rows")
            validate_final_outcome(final)

    def test_validation_failure_gets_one_narrow_repair_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_fixture_root(root)
            validation_failed = {
                "ok": False,
                "status": "audit_failed",
                "changed_count": 1,
                "validation_ok": False,
                "strict_accepted": False,
                "final_validation_rc": 1,
                "worker_usage_status": "measured",
                "worker_usage_unit": "tokens",
                "worker_total_tokens": 3,
                "worker_usage_source_path": "worker-usage.jsonl",
            }
            executor = SequenceExecutor([validation_failed, green(9), green(9)])
            runner = make_runner(root, executor, task_count=2)

            final = runner.run()

            self.assertEqual(final["final_outcome"], "zcode_20_live_green_measured")
            self.assertEqual(executor.calls[0], ("billing-credit-contract-01", False))
            self.assertEqual(executor.calls[1], ("billing-credit-contract-01", True))

    def test_wall_clock_bound_saves_resume_later_before_next_task_after_provider_pause(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_fixture_root(root)
            executor = SequenceExecutor([green()])
            runner = TestRunner(
                repo_root=root,
                state_path=root / "state.json",
                final_outcome_path=root / "final.json",
                readiness_path=root / "readiness.json",
                manifest_path=root / "manifest.json",
                task_count=20,
                work_root=root / "work",
                executor=executor,
                cooldown_seconds=(0, 0, 0, 0),
                until_done=True,
                max_wall_clock_hours=0,
            )
            state = create_initial_state(task_count=20, work_root=root / "work", resume_command=runner.resume_command())
            state["tasks"][0].update(green(7))
            state["tasks"][0]["status"] = "strict_green"
            state["tasks"][0]["attempts"] = 1
            state["tasks"][0]["task_id"] = "billing-credit-contract-01"
            state["tasks"][0]["provider_rate_limit_1302_count"] = 1
            state["provider_rate_limit_count"] = 1
            state["provider_pause_count"] = 1
            state["provider_pause_status"] = PROVIDER_PAUSED_STATUS
            state["last_provider_code"] = "1302"
            state["last_route_output_path"] = "provider-blocked.json"
            state["current_task_id"] = "policy-reason-contract-02"
            write_json(root / "state.json", state)

            final = runner.run()

            self.assertEqual(final["final_outcome"], OUTCOME_PROVIDER_PAUSED)
            self.assertEqual(final["terminal_blocker"], "wall_clock_bound_reached")
            self.assertTrue(final["wall_clock_bound_reached"])
            self.assertEqual(executor.calls, [])
            validate_final_outcome(final)

    def test_codex_target_touch_terminal_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_fixture_root(root)
            runner = DirtyAfterFirstCheckRunner(
                repo_root=root,
                state_path=root / "state.json",
                final_outcome_path=root / "final.json",
                readiness_path=root / "readiness.json",
                manifest_path=root / "manifest.json",
                task_count=20,
                work_root=root / "work",
                executor=SequenceExecutor([green()]),
                cooldown_seconds=(0, 0, 0, 0),
                dirty_after=1,
            )

            final = runner.run()

            self.assertEqual(final["final_outcome"], OUTCOME_CODEX_TOUCHED)
            self.assertTrue(final["codex_touched_target_artifact"])

    def test_final_outcome_rejects_codex_fallback_route_summary(self):
        payload = {
            "final_outcome": "zcode_20_live_partial_measured",
            "claim_family": "zcode_required_20_live",
            "task_count": 20,
            "delegated_rows": 20,
            "total_attempts": 20,
            "route_used_summary": {"codex_fallback": 1},
            "production_green_path_enabled": False,
            "direct_mode_default": False,
            "strict_gate_weakened": False,
            "glm_5_2_fixed": True,
            "glm_4_7_fallback": False,
            "time_of_day_gate": False,
            "codex_touched_target_artifact": False,
            "worker_token_measured_count": 1,
            "worker_usage_unit": "tokens",
            "worker_total_tokens": 10,
            "worker_usage_source_paths": ["worker-usage.jsonl"],
        }

        self.assertIn("route_used_summary:codex_fallback_nonzero", final_outcome_errors(payload))


if __name__ == "__main__":
    unittest.main()
