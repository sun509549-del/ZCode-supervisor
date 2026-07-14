import unittest

from tools.zcode_eval.strict_contract_breakdown import apply_report_schema, zero_usage_metrics


def timeout_row() -> dict:
    return {
        "task": "policy-reason-contract",
        "kind": "policy routing exact labels",
        "mode": "zcode_direct_launcher",
        "quality": "fail",
        "duration_seconds": 300.832,
        "allowed_file": "src/policy.js",
        "changed_files": ["src/policy.js"],
        "diff": [{"file": "src/policy.js", "added": 9, "deleted": 1}],
        "launcher_rc": 0,
        "route_rc": 143,
        "acceptance_rc": 1,
        "final_validation_rc": 0,
        "scope_ok": True,
        "timed_out": False,
        "usage": zero_usage_metrics(),
        "usage_status": "not_applicable",
        "strict_accepted": None,
        "strict_violations": [
            {
                "type": "strict_contract_self_audit_missing",
                "path": ".codex/zcode/runs/zcode_self_audit.json",
            }
        ],
        "codex_exec_rows": [],
        "supervisor_state": "run_timeout",
        "provider_error": True,
        "provider_error_kind": "provider_error",
        "provider_code": "1302",
        "worker_usage_status": "measured",
        "worker_usage_unit": "tokens",
        "worker_total_tokens": 151019,
        "zcode_acceptance": {
            "artifact_quality": "fail",
            "audit_ok": False,
            "changed_count": 1,
            "codex_repair_size": "moderate fix",
            "ok": False,
            "scope_safety": "pass",
            "status": "run_timeout",
            "supervisor_state": "run_timeout",
            "timed_out": True,
            "validation_ok": True,
            "validation_result": "pass",
        },
    }


class TimeoutClassificationTests(unittest.TestCase):
    def enrich(self, row: dict) -> dict:
        return apply_report_schema(
            row,
            measurement_mode="direct",
            fresh_run=True,
            baseline_cache_hit=False,
            baseline_cache_key=None,
        )

    def test_timeout_with_measured_tokens_records_validated_partial_blocker(self):
        enriched = self.enrich(timeout_row())

        self.assertEqual(enriched["worker_usage_status"], "measured")
        self.assertEqual(enriched["worker_usage_unit"], "tokens")
        self.assertEqual(enriched["worker_total_tokens"], 151019)
        self.assertEqual(
            enriched["timeout_acceptance_outcome"],
            "timeout_after_valid_artifact_but_acceptance_blocked",
        )
        self.assertTrue(enriched["validated_partial_artifact_present"])
        self.assertTrue(enriched["artifact_valid_before_timeout"])
        self.assertEqual(enriched["provider_timeout_classification"], "provider_rate_limit_1302")
        self.assertTrue(enriched["provider_rate_limit_1302_detected"])
        self.assertEqual(enriched["timeout_phase"], "after_final_validation_before_final_report")

    def test_final_validation_pass_with_route_timeout_still_fails_closed(self):
        enriched = self.enrich(timeout_row())

        self.assertTrue(enriched["final_validation_passed"])
        self.assertTrue(enriched["final_validation_rc_preserved_after_timeout"])
        self.assertTrue(enriched["route_rc_143_fails_closed"])
        self.assertEqual(
            enriched["acceptance_timeout_diagnostic"],
            "final_validation_passed_but_route_timeout_missing_self_audit_fail_closed",
        )
        self.assertIn("artifact_quality", enriched["strict_failure_classification"])
        self.assertIn("codex_repair_size", enriched["strict_failure_classification"])

    def test_timeout_is_never_strict_success(self):
        enriched = self.enrich(timeout_row())

        self.assertEqual(enriched["quality"], "fail")
        self.assertIsNone(enriched["strict_accepted"])
        self.assertTrue(enriched["timeout_not_treated_as_success"])
        self.assertNotEqual(enriched["timeout_acceptance_outcome"], "supervisor_timeout_classification_bug_fixed")


if __name__ == "__main__":
    unittest.main()
