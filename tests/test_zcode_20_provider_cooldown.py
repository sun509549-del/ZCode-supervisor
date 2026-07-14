import json
import tempfile
import unittest
from pathlib import Path

from tools.zcode_supervisor.zcode_20_resumable import (
    PROVIDER_PAUSED_STATUS,
    ResumableRunner,
)
from tests.test_zcode_20_resumable_state import make_fixture_root


class ProviderBlockedExecutor:
    def __init__(self):
        self.calls = 0

    def run(self, task, *, repair=False):
        self.calls += 1
        return {
            "ok": False,
            "status": "retryable_provider_error",
            "exit_code": 143,
            "changed_count": 0,
            "provider_error": True,
            "provider_error_kind": "provider_rate_limit_1302",
            "provider_code": "1302",
            "provider_rate_limit_1302": True,
            "provider_rate_limit_1302_count": 1,
            "worker_usage_status": "unavailable",
            "worker_usage_unit": "unknown",
            "worker_total_tokens": None,
        }


class TestRunner(ResumableRunner):
    def install_repo(self, workspace: Path) -> None:
        return None

    def target_artifacts_dirty(self) -> bool:
        return False


def make_provider_runner(root: Path, executor, waits, **kwargs):
    return TestRunner(
        repo_root=root,
        state_path=root / "state.json",
        final_outcome_path=root / "final.json",
        readiness_path=root / "readiness.json",
        manifest_path=root / "manifest.json",
        task_count=20,
        work_root=root / "work",
        executor=executor,
        sleep_fn=waits.append,
        cooldown_seconds=(0, 0, 0, 0),
        provider_cooldown_seconds_arg=0,
        **kwargs,
    )


class ZCode20ProviderCooldownTests(unittest.TestCase):
    def test_provider_1302_pauses_without_consuming_task_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_fixture_root(root)
            waits = []
            executor = ProviderBlockedExecutor()
            runner = make_provider_runner(root, executor, waits)

            final = runner.run()

            self.assertEqual(final["final_outcome"], "provider_paused_resume_later")
            self.assertEqual(final["provider_pause_status"], PROVIDER_PAUSED_STATUS)
            self.assertEqual(final["provider_pause_count"], 1)
            self.assertEqual(final["provider_rate_limit_count"], 1)
            self.assertEqual(final["delegated_rows"], 0)
            self.assertEqual(final["total_attempts"], 0)
            self.assertEqual(waits, [])
            self.assertIn("--until-done", final["resume_command"])
            self.assertEqual(executor.calls, 1)
            state = json.loads((root / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["tasks"][0]["status"], "pending")
            self.assertEqual(state["tasks"][0]["attempts"], 0)
            self.assertEqual(state["tasks"][0]["blocker"], PROVIDER_PAUSED_STATUS)
            self.assertEqual(state["tasks"][0]["route_attempts"][0]["attempt"], None)
            self.assertFalse(state["tasks"][0]["route_attempts"][0]["task_attempt_consumed"])

    def test_until_done_uses_bounded_cooldown_and_saves_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_fixture_root(root)
            waits = []
            executor = ProviderBlockedExecutor()
            runner = make_provider_runner(
                root,
                executor,
                waits,
                until_done=True,
                provider_max_pause_cycles=1,
            )

            final = runner.run()

            self.assertEqual(final["final_outcome"], "provider_paused_resume_later")
            self.assertEqual(final["provider_pause_count"], 2)
            self.assertEqual(final["provider_rate_limit_count"], 2)
            self.assertEqual(final["cooldown_cycles_used"], 1)
            self.assertEqual(final["total_attempts"], 0)
            self.assertEqual(waits, [0])
            self.assertEqual(executor.calls, 2)
            state = json.loads((root / "state.json").read_text(encoding="utf-8"))
            attempts = state["tasks"][0]["route_attempts"]
            self.assertEqual([item["provider_pause_event"] for item in attempts], [1, 2])
            self.assertEqual([item["attempt"] for item in attempts], [None, None])


if __name__ == "__main__":
    unittest.main()
