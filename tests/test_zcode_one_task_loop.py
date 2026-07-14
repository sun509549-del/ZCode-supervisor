import unittest

from tools.zcode_supervisor.one_task_loop import (
    BLOCKED_OUTCOME,
    CLAIM_FAMILY,
    SUCCESS_OUTCOME,
    final_outcome_errors,
    validate_final_outcome,
)


def success_payload(**overrides):
    payload = {
        "final_outcome": SUCCESS_OUTCOME,
        "route_used": "zcode_cli",
        "route_rc": 0,
        "zcode_implemented": True,
        "codex_touched_target_artifact": False,
        "zcode_changed_allowed_files": [
            "benchmarks/hard-token-fixtures/policy-reason-contract/src/policy.js"
        ],
        "forbidden_files_unchanged": True,
        "final_validation_rc": 0,
        "acceptance_rc": 0,
        "strict_accepted": True,
        "strict_gate_weakened": False,
        "worker_usage_status": "measured",
        "worker_usage_unit": "tokens",
        "worker_total_tokens": 42,
        "worker_usage_source_path": ".codex/zcode/runs/worker-usage.json",
        "row_ids": ["attempt-1"],
        "claim_family": CLAIM_FAMILY,
        "zcode_attempts": 1,
        "production_green_path_enabled": False,
        "direct_mode_default": False,
        "twenty_plus_live_count": 0,
        "glm_5_2_fixed": True,
        "glm_4_7_fallback": False,
        "time_of_day_gate": False,
    }
    payload.update(overrides)
    return payload


class ZCodeOneTaskLoopTests(unittest.TestCase):
    def test_success_requires_zcode_route_measured_tokens_and_one_task_claim(self):
        validate_final_outcome(success_payload())

    def test_codex_fallback_route_fails_success(self):
        errors = final_outcome_errors(success_payload(route_used="codex_fallback"))
        self.assertIn("route_used:not_zcode_route", errors)
        self.assertIn("route_used:codex_fallback", errors)

    def test_unavailable_usage_is_not_success_or_zero(self):
        errors = final_outcome_errors(
            success_payload(
                worker_usage_status="unavailable",
                worker_usage_unit="unknown",
                worker_total_tokens=0,
            )
        )
        self.assertIn("worker_usage_status:not_measured", errors)
        self.assertIn("worker_usage_unit:not_tokens", errors)
        self.assertIn("worker_total_tokens:not_positive", errors)

    def test_target_artifact_codex_touch_blocks_success(self):
        errors = final_outcome_errors(success_payload(codex_touched_target_artifact=True))
        self.assertIn("codex_touched_target_artifact:not_false", errors)

    def test_terminal_blocker_requires_exhaustion_and_evidence(self):
        payload = success_payload(
            final_outcome=BLOCKED_OUTCOME,
            route_used="zcode_cli",
            terminal_blocker="terminal_provider_unavailable",
            autonomous_routes_exhausted=True,
            blocker_evidence="provider overload repeated after fail-fast retry",
            worker_total_tokens=None,
        )

        validate_final_outcome(payload)

    def test_terminal_blocker_rejects_unknown_blocker(self):
        errors = final_outcome_errors(
            success_payload(
                final_outcome=BLOCKED_OUTCOME,
                terminal_blocker="provider_sad",
                autonomous_routes_exhausted=True,
                blocker_evidence="x",
            )
        )
        self.assertIn("terminal_blocker:unsupported", errors)


if __name__ == "__main__":
    unittest.main()
