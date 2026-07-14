import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.zcode_eval.live_retry_preflight import canary_preflight
from tools.zcode_eval.strict_contract_comparison import PROVIDER_SUCCESS_WITHOUT_USAGE_PAYLOAD_REASON


class LiveRetryPreflightTests(unittest.TestCase):
    def test_empty_worker_usage_sidecar_blocks_live_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp)
            summary = self._write_summary(report, worker_source="tasks/policy/zcode-direct-launcher/worker-usage.json")
            self._write_sidecar(
                report / "tasks/policy/zcode-direct-launcher/worker-usage.json",
                {
                    "source_type": "worker_usage_sidecar",
                    "status": "unavailable",
                    "unit": "unknown",
                    "usage": {},
                    "no_usage_reason": "zcode_cli_usage_missing",
                },
            )

            payload = canary_preflight(summary, cwd=report)

            self.assertFalse(payload["eligible_for_live_20_retry"])
            self.assertEqual(payload["worker_usage_status"], "worker_usage_empty_sidecar")
            self.assertIn("worker_token_usage:policy:worker_usage_empty_sidecar", payload["blockers"])

    def test_provider_success_without_usage_payload_blocks_with_precise_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp)
            summary = self._write_summary(
                report,
                worker_source="tasks/policy/zcode-direct-launcher/worker-usage.json",
                worker_reason=PROVIDER_SUCCESS_WITHOUT_USAGE_PAYLOAD_REASON,
            )
            self._write_sidecar(
                report / "tasks/policy/zcode-direct-launcher/worker-usage.json",
                {
                    "source_type": "worker_usage_sidecar",
                    "status": "unavailable",
                    "unit": "unknown",
                    "usage": {},
                    "no_usage_reason": PROVIDER_SUCCESS_WITHOUT_USAGE_PAYLOAD_REASON,
                },
            )

            payload = canary_preflight(summary, cwd=report)

            self.assertFalse(payload["eligible_for_live_20_retry"])
            self.assertEqual(payload["worker_usage_status"], "worker_usage_empty_sidecar")
            self.assertIn(
                f"worker_token_usage:policy:{PROVIDER_SUCCESS_WITHOUT_USAGE_PAYLOAD_REASON}",
                payload["blockers"],
            )
            self.assertTrue(payload["worker_usage_rows"][0]["empty_sidecar"])

    def test_valid_worker_usage_sidecar_allows_live_retry_preflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp)
            summary = self._write_summary(report, worker_source="tasks/policy/zcode-direct-launcher/worker-usage.json")
            self._write_sidecar(
                report / "tasks/policy/zcode-direct-launcher/worker-usage.json",
                {
                    "source_type": "provider_usage_ledger",
                    "provider": "zai",
                    "model": "glm-live",
                    "unit": "tokens",
                    "usage": {
                        "total_tokens": 17,
                        "input_tokens": 12,
                        "output_tokens": 5,
                    },
                },
            )

            payload = canary_preflight(summary, cwd=report)

            self.assertTrue(payload["eligible_for_live_20_retry"])
            self.assertEqual(payload["worker_usage_status"], "measured_tokens")
            self.assertEqual(payload["blockers"], [])
            self.assertEqual(payload["worker_usage_rows"][0]["worker_total_tokens"], 17)

    def test_row_scoped_provider_usage_ledger_allows_live_retry_preflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp)
            summary = self._write_summary(report, worker_source="tasks/policy/zcode-direct-launcher/worker-usage.jsonl")
            self._write_sidecar(
                report / "tasks/policy/zcode-direct-launcher/worker-usage.jsonl",
                {
                    "source_type": "provider_usage_ledger",
                    "provider": "zai",
                    "model": "glm-live",
                    "unit": "tokens",
                    "usage": {
                        "total_tokens": 23,
                        "input_tokens": 18,
                        "output_tokens": 5,
                    },
                },
            )

            payload = canary_preflight(summary, cwd=report)

            self.assertTrue(payload["eligible_for_live_20_retry"])
            self.assertEqual(payload["worker_usage_status"], "measured_tokens")
            self.assertEqual(payload["blockers"], [])
            self.assertEqual(payload["worker_usage_rows"][0]["worker_usage_source_path"], str(report / "tasks/policy/zcode-direct-launcher/worker-usage.jsonl"))
            self.assertEqual(payload["worker_usage_rows"][0]["worker_total_tokens"], 23)

    def test_cli_returns_nonzero_for_blocked_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp)
            summary = self._write_summary(report, worker_source="tasks/policy/zcode-direct-launcher/worker-usage.json")
            self._write_sidecar(
                report / "tasks/policy/zcode-direct-launcher/worker-usage.json",
                {
                    "source_type": "worker_usage_sidecar",
                    "status": "unavailable",
                    "unit": "unknown",
                    "usage": {},
                },
            )

            result = subprocess.run(
                [sys.executable, "scripts/check_live_retry_preflight.py", str(summary), "--json"],
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )

            self.assertNotEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["eligible_for_live_20_retry"])
            self.assertEqual(payload["worker_usage_status"], "worker_usage_empty_sidecar")

    def _write_summary(
        self,
        report: Path,
        *,
        worker_source: str,
        worker_reason: str = "zcode_cli_usage_missing",
    ) -> Path:
        path = report / "summary.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "rows": [
                        {
                            "task": "policy",
                            "mode": "zcode_direct_launcher",
                            "quality": "pass",
                            "strict_accepted": True,
                            "timed_out": False,
                            "provider_error_kind": None,
                            "worker_usage_status": "unavailable",
                            "worker_usage_unit": "unknown",
                            "worker_total_tokens": None,
                            "worker_usage_source_path": worker_source,
                            "worker_usage_no_usage_reason": worker_reason,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return path

    def _write_sidecar(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
