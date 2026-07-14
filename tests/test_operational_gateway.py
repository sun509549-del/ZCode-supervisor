"""Regression coverage for stable operational gateway result fields."""

import unittest

from tools.zcode_supervisor.operational_gateway import operational_gateway_fields


class OperationalGatewayTests(unittest.TestCase):
    def test_reads_nested_strict_contract_acceptance(self) -> None:
        result = operational_gateway_fields(
            route="delegate_zcode",
            reason=None,
            zcode_attempted=True,
            zcode_ok=True,
            run_json={
                "audit": {"strict_contract": {"accepted": True, "violations": []}},
                "worker_total_tokens": 42,
            },
        )

        self.assertTrue(result["strict_accepted"])
        self.assertTrue(result["task_acceptance_ready"])
        self.assertIsNone(result["fallback_reason"])

    def test_nested_strict_contract_failure_is_classified(self) -> None:
        result = operational_gateway_fields(
            route="delegate_zcode",
            reason=None,
            zcode_attempted=True,
            zcode_ok=False,
            run_json={
                "status": "audit_failed",
                "audit": {
                    "strict_contract": {
                        "accepted": False,
                        "violations": [{"type": "missing_evidence"}],
                    }
                },
                "worker_total_tokens": 42,
            },
        )

        self.assertFalse(result["strict_accepted"])
        self.assertFalse(result["task_acceptance_ready"])
        self.assertEqual(result["fallback_reason"], "strict_failure")


if __name__ == "__main__":
    unittest.main()
