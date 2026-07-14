import argparse
import unittest

from tools.zcode_eval.strict_contract import (
    build_acceptance_result_for_payload,
    parse_path_reference,
    path_policy_violations,
)


class StrictPathReferenceNormalizationTests(unittest.TestCase):
    def test_reason_suffix_and_ranges_parse_separately(self):
        ref = parse_path_reference("src/credits.js:4-8,12-15:outside_allowed_files")

        self.assertEqual(ref.path, "src/credits.js")
        self.assertEqual(ref.ranges, [(4, 8), (12, 15)])
        self.assertEqual(ref.reason, "outside_allowed_files")
        self.assertIsNone(ref.safety_error)

    def test_allowed_file_with_range_is_not_outside_allowed(self):
        violations = path_policy_violations(
            ["src/credits.js:4-8,12-15"],
            self._contract(),
        )

        self.assertEqual(violations, [])

    def test_allowed_file_with_single_line_is_not_outside_allowed(self):
        violations = path_policy_violations(
            ["src/credits.js:4"],
            self._contract(),
        )

        self.assertEqual(violations, [])

    def test_reason_suffix_without_range_keeps_path(self):
        ref = parse_path_reference("src/credits.js:outside_allowed_files")

        self.assertEqual(ref.path, "src/credits.js")
        self.assertEqual(ref.ranges, [])
        self.assertEqual(ref.reason, "outside_allowed_files")

    def test_forbidden_file_with_range_still_fails(self):
        violations = path_policy_violations(
            ["src/secrets.js:1-3:outside_allowed_files"],
            self._contract(),
        )

        self.assertIn("src/secrets.js:outside_allowed_files", violations)
        self.assertIn("src/secrets.js:forbidden_file_changed", violations)

    def test_absolute_and_traversal_paths_fail_closed(self):
        absolute = path_policy_violations(["/tmp/work/src/credits.js:1-3"], self._contract())
        traversal = path_policy_violations(["../src/credits.js:1-3"], self._contract())

        self.assertIn("/tmp/work/src/credits.js:1-3:path_safety:path_absolute", absolute)
        self.assertIn("../src/credits.js:1-3:path_safety:path_parent", traversal)

    def test_empty_path_fails_closed(self):
        violations = path_policy_violations([""], self._contract())

        self.assertIn(":path_safety:path_empty", violations)

    def test_risk_flags_fail_closed(self):
        audit = self._audit()
        audit["risk_flags"] = ["diff budget exceeded"]

        payload = build_acceptance_result_for_payload(
            self._args(changed_file=["src/credits.js"]),
            self._contract(),
            audit,
        )

        self.assertFalse(payload["accepted"])
        self.assertIn("risk_flags", payload["violations"])

    def _contract(self):
        return {
            "contract_id": "billing-credit-contract@billing_cent_rounding.v1",
            "task_id": "billing-credit-contract",
            "allowed_files": ["src/credits.js"],
            "forbidden_files": ["src/secrets.js"],
            "requirements": [],
            "edge_cases": [],
            "expected_diff_budget": {},
        }

    def _audit(self):
        return {
            "contract_id": "billing-credit-contract@billing_cent_rounding.v1",
            "task_id": "billing-credit-contract",
            "overall_status": "pass",
            "requirements": [],
            "validation": {"result": "pass", "summary": "validation passed"},
            "deviations_from_plan": [],
            "unresolved_questions": [],
            "risk_flags": [],
            "blocked_reasons": [],
        }

    def _args(self, *, changed_file):
        return argparse.Namespace(
            experiment_id="test",
            mode="manifest_only",
            validation_result="pass",
            validation_exit_code=0,
            changed_file=changed_file,
            files_changed=len(changed_file),
            insertions=0,
            deletions=0,
            codex_repair_size="none",
            full_diff_read=False,
            full_log_read=False,
            green_path_non_llm=False,
            shadow_codex_audit_enabled=False,
            shadow_codex_audit_result="unavailable",
            missed_risk_flag=[],
        )


if __name__ == "__main__":
    unittest.main()
