"""Validation helpers for the ZCode-required one-task success loop."""

from __future__ import annotations

from typing import Any

SUCCESS_OUTCOME = "zcode_one_task_strict_green_measured"
BLOCKED_OUTCOME = "terminal_zcode_one_task_blocked"
CLAIM_FAMILY = "zcode_required_one_task"
ALLOWED_SUCCESS_ROUTES = {"zcode_cli", "zcode_app_cdp"}
TERMINAL_BLOCKERS = {
    "terminal_provider_unavailable",
    "terminal_zcode_route_unavailable",
    "terminal_workspace_control_unavailable",
    "terminal_usage_capture_unavailable_after_repair",
    "terminal_repeated_zcode_strict_failure",
    "terminal_codex_touched_target_artifact",
    "terminal_other_precise",
}


class OutcomeValidationError(ValueError):
    """Raised when final one-task evidence violates the goal contract."""


def validate_final_outcome(payload: dict[str, Any]) -> None:
    errors = final_outcome_errors(payload)
    if errors:
        raise OutcomeValidationError("; ".join(errors))


def final_outcome_errors(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    outcome = payload.get("final_outcome")
    if outcome not in {SUCCESS_OUTCOME, BLOCKED_OUTCOME}:
        errors.append("final_outcome:unsupported")
        return errors
    if payload.get("claim_family") != CLAIM_FAMILY:
        errors.append("claim_family:not_zcode_required_one_task")
    if payload.get("strict_gate_weakened") is not False:
        errors.append("strict_gate_weakened:not_false")
    if payload.get("production_green_path_enabled") is not False:
        errors.append("production_green_path_enabled:not_false")
    if payload.get("direct_mode_default") is not False:
        errors.append("direct_mode_default:not_false")
    if payload.get("twenty_plus_live_count") != 0:
        errors.append("twenty_plus_live_count:not_zero")
    if payload.get("glm_5_2_fixed") is not True:
        errors.append("glm_5_2_fixed:not_true")
    if payload.get("glm_4_7_fallback") is not False:
        errors.append("glm_4_7_fallback:not_false")
    if payload.get("time_of_day_gate") is not False:
        errors.append("time_of_day_gate:not_false")
    if outcome == SUCCESS_OUTCOME:
        errors.extend(success_errors(payload))
    else:
        errors.extend(blocked_errors(payload))
    return errors


def success_errors(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("route_used") not in ALLOWED_SUCCESS_ROUTES:
        errors.append("route_used:not_zcode_route")
    if payload.get("route_used") == "codex_fallback":
        errors.append("route_used:codex_fallback")
    route_ok = payload.get("route_rc") == 0 or payload.get("app_route_equivalent_success") is True
    if not route_ok:
        errors.append("route_success:not_proven")
    if payload.get("zcode_implemented") is not True:
        errors.append("zcode_implemented:not_true")
    if payload.get("codex_touched_target_artifact") is not False:
        errors.append("codex_touched_target_artifact:not_false")
    if not payload.get("zcode_changed_allowed_files"):
        errors.append("zcode_changed_allowed_files:missing")
    if payload.get("forbidden_files_unchanged") is not True:
        errors.append("forbidden_files_unchanged:not_true")
    if payload.get("final_validation_rc") != 0:
        errors.append("final_validation_rc:not_zero")
    if payload.get("acceptance_rc") not in {0, None}:
        errors.append("acceptance_rc:not_zero_or_null")
    if payload.get("strict_accepted") is not True:
        errors.append("strict_accepted:not_true")
    if payload.get("worker_usage_status") != "measured":
        errors.append("worker_usage_status:not_measured")
    if payload.get("worker_usage_unit") != "tokens":
        errors.append("worker_usage_unit:not_tokens")
    if positive_int(payload.get("worker_total_tokens")) is None:
        errors.append("worker_total_tokens:not_positive")
    if not non_empty_string(payload.get("worker_usage_source_path")):
        errors.append("worker_usage_source_path:missing")
    if not payload.get("row_ids") and not payload.get("source_evidence"):
        errors.append("source_evidence:missing")
    if positive_int(payload.get("zcode_attempts")) is None:
        errors.append("zcode_attempts:not_positive")
    return errors


def blocked_errors(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    blocker = payload.get("terminal_blocker")
    if blocker not in TERMINAL_BLOCKERS:
        errors.append("terminal_blocker:unsupported")
    if payload.get("autonomous_routes_exhausted") is not True:
        errors.append("autonomous_routes_exhausted:not_true")
    if blocker != "terminal_codex_touched_target_artifact" and payload.get("codex_touched_target_artifact") is not False:
        errors.append("codex_touched_target_artifact:not_false")
    if not non_empty_string(payload.get("blocker_evidence")):
        errors.append("blocker_evidence:missing")
    return errors


def positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        integer = int(value)
    except (TypeError, ValueError):
        return None
    return integer if integer > 0 else None


def non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())
