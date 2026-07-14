import tempfile
import unittest
from pathlib import Path

from tools.zcode_supervisor.zcode_20_resumable import (
    CLAIM_FAMILY,
    PROVIDER_PAUSED_STATUS,
    ResumableRunner,
    create_initial_state,
    normalize_state,
)


class FakeExecutor:
    def __init__(self):
        self.calls = []

    def run(self, task, *, repair=False):
        self.calls.append((task["task_id"], repair))
        return {
            "ok": True,
            "status": "success",
            "changed_count": 1,
            "strict_accepted": True,
            "final_validation_rc": 0,
            "worker_usage_status": "measured",
            "worker_usage_unit": "tokens",
            "worker_total_tokens": 11,
            "worker_usage_source_path": "worker-usage.jsonl",
            "row_ids": [len(self.calls)],
        }


class TestRunner(ResumableRunner):
    def install_repo(self, workspace: Path) -> None:
        return None

    def target_artifacts_dirty(self) -> bool:
        return False


def make_fixture_root(root: Path) -> None:
    for fixture, src in (
        ("billing-credit-contract", "src/credits.js"),
        ("policy-reason-contract", "src/policy.js"),
    ):
        path = root / "benchmarks/hard-token-fixtures" / fixture
        (path / Path(src).parent).mkdir(parents=True)
        (path / src).write_text("export default {}\n", encoding="utf-8")
        (path / "package.json").write_text('{"type":"module"}\n', encoding="utf-8")


class ZCode20ResumableStateTests(unittest.TestCase):
    def test_initial_state_records_twenty_pending_tasks(self):
        state = create_initial_state(
            task_count=20,
            work_root=Path(".local/zcode-20-resumable/work"),
            resume_command="python3 scripts/run_zcode_20_resumable.py --resume",
        )

        self.assertEqual(state["claim_family"], CLAIM_FAMILY)
        self.assertEqual(len(state["tasks"]), 20)
        self.assertEqual({task["status"] for task in state["tasks"]}, {"pending"})
        self.assertEqual(state["limits"]["max_total_attempts"], 40)
        self.assertEqual(state["limits"]["max_attempts_per_task"], 2)

    def test_normalize_running_task_to_pending_for_resume(self):
        state = create_initial_state(task_count=2, work_root=Path("work"), resume_command="resume")
        state["tasks"][0]["status"] = "running"

        normalized = normalize_state(
            state,
            task_count=2,
            work_root=Path("work"),
            resume_command="resume",
        )

        self.assertEqual(normalized["tasks"][0]["status"], "pending")
        self.assertEqual(normalized["tasks"][0]["blocker"], "resumed_after_interrupted_running")

    def test_normalize_migrates_preimplementation_provider_block_to_global_pause(self):
        state = create_initial_state(task_count=2, work_root=Path("work"), resume_command="resume")
        task = state["tasks"][0]
        task["status"] = "provider_blocked"
        task["attempts"] = 2
        task["blocker"] = "provider_rate_limit_1302"
        task["provider_rate_limit_1302_count"] = 12
        task["run_path"] = "work/tasks/billing/.codex/zcode/runs/route-2.zcode.json"
        task["route_attempts"] = [
            {
                "attempt": 1,
                "route_used": "zcode_cli",
                "provider_rate_limit_1302": True,
                "provider_error_kind": "provider_rate_limit_1302",
                "provider_code": "1302",
                "provider_rate_limit_1302_count": 6,
                "changed_count": 0,
            },
            {
                "attempt": 2,
                "route_used": "zcode_cli",
                "provider_rate_limit_1302": True,
                "provider_error_kind": "provider_rate_limit_1302",
                "provider_code": "1302",
                "provider_rate_limit_1302_count": 6,
                "changed_count": 0,
            },
        ]

        normalized = normalize_state(
            state,
            task_count=2,
            work_root=Path("work"),
            resume_command="resume",
        )

        self.assertEqual(normalized["provider_pause_status"], PROVIDER_PAUSED_STATUS)
        self.assertEqual(normalized["provider_pause_count"], 2)
        self.assertEqual(normalized["last_provider_code"], "1302")
        self.assertEqual(normalized["last_route_output_path"], task["run_path"])
        self.assertEqual(normalized["current_task_id"], task["task_id"])
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["attempts"], 0)
        self.assertEqual(normalized["delegated_rows"], 0)
        self.assertEqual(normalized["total_attempts"], 0)
        self.assertEqual([item["attempt"] for item in task["route_attempts"]], [None, None])
        self.assertEqual([item["original_attempt"] for item in task["route_attempts"]], [1, 2])

    def test_strict_green_task_is_not_rerun_on_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_fixture_root(root)
            state_path = root / "state.json"
            final_path = root / "final.json"
            readiness_path = root / "readiness.json"
            manifest_path = root / "manifest.json"
            state = create_initial_state(task_count=3, work_root=root / "work", resume_command="resume")
            state["tasks"][0]["status"] = "strict_green"
            state["tasks"][0]["attempts"] = 1
            state["tasks"][0]["worker_usage_status"] = "measured"
            state["tasks"][0]["worker_usage_unit"] = "tokens"
            state["tasks"][0]["worker_total_tokens"] = 7
            state["tasks"][0]["worker_usage_source_path"] = "old-worker-usage.jsonl"
            state_path.write_text(__import__("json").dumps(state), encoding="utf-8")
            executor = FakeExecutor()
            runner = TestRunner(
                repo_root=root,
                state_path=state_path,
                final_outcome_path=final_path,
                readiness_path=readiness_path,
                manifest_path=manifest_path,
                task_count=3,
                work_root=root / "work",
                executor=executor,
                cooldown_seconds=(0, 0, 0, 0),
            )

            final = runner.run()

            self.assertEqual(final["final_outcome"], "zcode_20_live_green_measured")
            self.assertEqual([call[0] for call in executor.calls], [
                "policy-reason-contract-02",
                "billing-credit-contract-03",
            ])


if __name__ == "__main__":
    unittest.main()
