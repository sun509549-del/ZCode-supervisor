import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check_goal_n_final_gate1.py"


def load_gate1_module():
    spec = importlib.util.spec_from_file_location("goal_n_final_gate1", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class GoalNFinalGate1Tests(unittest.TestCase):
    def test_launcher_checker_accepts_quoted_task_dir_provider_ledger(self):
        gate1 = load_gate1_module()
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "row with spaces"
            task_dir.mkdir()
            script = task_dir / "run_zcode_launcher.sh"
            script.write_text(
                f"TASK_DIR={str(task_dir)!r}\n"
                'unset ZCODE_WORKER_USAGE_SIDECAR ZCODE_WORKER_USAGE_LEDGER ZCODE_USAGE_LEDGER\n'
                'export ZCODE_PROVIDER_USAGE_LEDGER="$TASK_DIR/worker-usage.jsonl"\n'
                '# preserve_worker_usage_sidecar worker_usage_source_path\n',
                encoding="utf-8",
            )

            checks = gate1.check_launcher(script)

        self.assertTrue(all(item["ok"] for item in checks), checks)

    def test_upstream_emission_fails_without_append_hook(self):
        gate1 = load_gate1_module()
        ok, details = gate1.wrapper_append_configured(
            "function sanitizeChildEnv() { return process.env; }",
            "zcode cli without the expected ledger hook",
        )

        self.assertFalse(ok)
        self.assertFalse(details["zcode_cli_hook"])
        self.assertFalse(details["repo_wrapper_append"])

    def test_upstream_emission_accepts_provider_hook(self):
        gate1 = load_gate1_module()
        ok, details = gate1.wrapper_append_configured(
            "function sanitizeChildEnv() { return process.env; }",
            "process.env.ZCODE_PROVIDER_USAGE_LEDGER; worker-usage.jsonl",
        )

        self.assertTrue(ok)
        self.assertTrue(details["zcode_cli_hook"])

    def test_upstream_emission_accepts_repo_wrapper_append(self):
        gate1 = load_gate1_module()
        ok, details = gate1.wrapper_append_configured(
            "const ledger = process.env.ZCODE_PROVIDER_USAGE_LEDGER; appendFile(ledger, provider_usage_ledger);",
            "",
        )

        self.assertTrue(ok)
        self.assertTrue(details["repo_wrapper_append"])

    def test_missing_upstream_hook_is_terminal_blocker_not_quality_failure(self):
        gate1 = load_gate1_module()
        checks = [
            gate1.check(True, "direct_dry_run_generates_launcher", "ok"),
            gate1.check(True, "direct_launcher_exports_row_scoped_provider_ledger", "ok"),
            gate1.check(True, "provider_ledger_path_inside_delegated_row_dir", "ok"),
            gate1.check(True, "launcher_preserves_worker_usage_sidecar", "ok"),
            gate1.check(True, "expected_jsonl_measured_tokens_positive_total", "ok"),
            gate1.check(True, "empty_usage_still_blocks", "ok"),
            gate1.check(True, "provider_success_without_usage_payload_still_blocks", "ok"),
            gate1.check(False, "provider_or_wrapper_expected_to_append_token_jsonl", "missing"),
        ]

        payload, exit_code = gate1.build_payload(checks)

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["gate1_quality_ready"])
        self.assertFalse(payload["gate1_passed"])
        self.assertFalse(payload["canary_allowed"])
        self.assertEqual(payload["final_outcome"], "blocked_upstream_usage_emission_missing")

    def test_quality_failure_exits_nonzero(self):
        gate1 = load_gate1_module()
        checks = [
            gate1.check(False, "direct_dry_run_generates_launcher", "missing"),
            gate1.check(True, "direct_launcher_exports_row_scoped_provider_ledger", "ok"),
            gate1.check(True, "provider_ledger_path_inside_delegated_row_dir", "ok"),
            gate1.check(True, "launcher_preserves_worker_usage_sidecar", "ok"),
            gate1.check(True, "expected_jsonl_measured_tokens_positive_total", "ok"),
            gate1.check(True, "empty_usage_still_blocks", "ok"),
            gate1.check(True, "provider_success_without_usage_payload_still_blocks", "ok"),
            gate1.check(False, "provider_or_wrapper_expected_to_append_token_jsonl", "missing"),
        ]

        payload, exit_code = gate1.build_payload(checks)

        self.assertEqual(exit_code, 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["final_outcome"], "blocked_quality")


if __name__ == "__main__":
    unittest.main()
