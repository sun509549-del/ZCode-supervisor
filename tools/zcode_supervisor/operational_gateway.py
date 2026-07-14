"""Operational gateway result schema for Codex-managed ZCode attempts."""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = 1
ROUTE_ZCODE_CLI = "zcode_cli"
ROUTE_ZCODE_APP_CDP = "zcode_app_cdp"
ROUTE_CODEX_FALLBACK = "codex_fallback"

CLAIM_FAMILY_ZCODE_CLI = "codex_mediated_zcode_cli"
CLAIM_FAMILY_ZCODE_APP_CDP = "codex_mediated_zcode_app_cdp"
CLAIM_FAMILY_CODEX_FALLBACK = "codex_fallback_no_zcode_claim"

FALLBACK_PROVIDER_TIMEOUT = "provider_timeout"
FALLBACK_PROVIDER_OVERLOAD = "provider_overload"
FALLBACK_WORKSPACE_NOT_BOUND = "workspace_not_bound"
FALLBACK_USAGE_UNAVAILABLE = "usage_unavailable"
FALLBACK_VALIDATION_FAILURE = "validation_failure"
FALLBACK_STRICT_FAILURE = "strict_failure"
FALLBACK_ZCODE_UNAVAILABLE = "zcode_unavailable"
FALLBACK_SKIPPED_BY_POLICY = "skipped_by_policy"
FALLBACK_ROUTING_CONFIG_MISSING = "routing_config_missing"


def operational_gateway_fields(
    *,
    route: str | None,
    reason: str | None,
    zcode_attempted: bool,
    zcode_ok: bool,
    run_json: dict[str, Any] | None = None,
    failure_reason: str | None = None,
    timed_out: bool = False,
) -> dict[str, Any]:
    """Build the stable route/usage/fallback fields for final reporting."""

    usage_status, total_tokens = zcode_usage(run_json, attempted=zcode_attempted)
    strict_accepted = strict_accepted_from_run(run_json)
    provider_error_kind = provider_kind(run_json)
    route_used = route_used_for(run_json, zcode_ok=zcode_ok)
    fallback_reason = None
    if not zcode_ok:
        fallback_reason = classify_fallback_reason(
            route=route,
            reason=reason,
            failure_reason=failure_reason,
            run_json=run_json,
            timed_out=timed_out,
            usage_status=usage_status,
        )
    return {
        "operational_gateway_schema_version": SCHEMA_VERSION,
        "route_used": route_used,
        "zcode_attempted": zcode_attempted,
        "zcode_ok": zcode_ok,
        "zcode_usage_status": usage_status,
        "zcode_total_tokens": total_tokens,
        "fallback_reason": fallback_reason,
        "provider_error_kind": provider_error_kind,
        "strict_accepted": strict_accepted,
        "strict_gate_weakened": False,
        "task_acceptance_ready": zcode_ok and strict_accepted is not False,
        "claim_family": claim_family_for(route_used),
        "usage_zero_guard": usage_status != "unavailable" or total_tokens is None,
    }


def zcode_usage(run_json: dict[str, Any] | None, *, attempted: bool) -> tuple[str, int | None]:
    if not attempted:
        return "skipped", None
    total = positive_int(first_present(
        get(run_json, "worker_total_tokens"),
        get(run_json, "usage_accounting", "worker_total_tokens"),
        get(run_json, "usage_accounting", "total_tokens"),
        get(run_json, "usage_accounting", "tokens_used"),
        get(run_json, "usage_normalized", "total_tokens"),
        get(run_json, "usage", "total_tokens"),
    ))
    raw_status = first_present(
        get(run_json, "worker_usage_status"),
        get(run_json, "usage_accounting", "worker_usage_status"),
    )
    if total is not None:
        return "measured", total
    if raw_status == "measured":
        return "unavailable", None
    return "unavailable", None


def route_used_for(run_json: dict[str, Any] | None, *, zcode_ok: bool) -> str:
    if not zcode_ok:
        return ROUTE_CODEX_FALLBACK
    backend = first_present(
        get(run_json, "worker_execution_backend"),
        get(run_json, "backend"),
    )
    if backend == ROUTE_ZCODE_APP_CDP:
        return ROUTE_ZCODE_APP_CDP
    return ROUTE_ZCODE_CLI


def claim_family_for(route_used: str) -> str:
    if route_used == ROUTE_ZCODE_APP_CDP:
        return CLAIM_FAMILY_ZCODE_APP_CDP
    if route_used == ROUTE_ZCODE_CLI:
        return CLAIM_FAMILY_ZCODE_CLI
    return CLAIM_FAMILY_CODEX_FALLBACK


def classify_fallback_reason(
    *,
    route: str | None,
    reason: str | None,
    failure_reason: str | None,
    run_json: dict[str, Any] | None,
    timed_out: bool,
    usage_status: str,
) -> str:
    status = first_present(get(run_json, "status"), get(run_json, "supervisor_state"))
    if route == "codex_direct" and reason == "routing_config_missing":
        return FALLBACK_ROUTING_CONFIG_MISSING
    if not run_json and not failure_reason:
        return FALLBACK_SKIPPED_BY_POLICY
    if provider_kind(run_json) == "provider_overload":
        return FALLBACK_PROVIDER_OVERLOAD
    if timed_out or status == "run_timeout" or failure_reason == "run_timeout":
        return FALLBACK_PROVIDER_TIMEOUT
    if status in {
        "app_cdp_workspace_not_bound",
        "app_cdp_workspace_unknown",
        "app_cdp_workspace_mismatch",
    }:
        return FALLBACK_WORKSPACE_NOT_BOUND
    if validation_failed(run_json):
        return FALLBACK_VALIDATION_FAILURE
    if strict_failed(run_json) or failure_reason == "zcode_no_changes":
        return FALLBACK_STRICT_FAILURE
    if usage_status == "unavailable" and failure_reason == "usage_unavailable":
        return FALLBACK_USAGE_UNAVAILABLE
    if route != "delegate_zcode":
        return FALLBACK_SKIPPED_BY_POLICY
    return FALLBACK_ZCODE_UNAVAILABLE


def strict_accepted_from_run(run_json: dict[str, Any] | None) -> bool | None:
    value = first_present(
        get(run_json, "strict_accepted"),
        get(run_json, "audit", "strict_accepted"),
        get(run_json, "acceptance", "strict_accepted"),
        get(run_json, "zcode_acceptance", "strict_accepted"),
    )
    return value if isinstance(value, bool) else None


def strict_failed(run_json: dict[str, Any] | None) -> bool:
    if strict_accepted_from_run(run_json) is False:
        return True
    violations = get(run_json, "strict_violations")
    return isinstance(violations, list) and bool(violations)


def validation_failed(run_json: dict[str, Any] | None) -> bool:
    values = (
        get(run_json, "validation_ok"),
        get(run_json, "audit", "validation", "ok"),
    )
    return any(value is False for value in values)


def provider_kind(run_json: dict[str, Any] | None) -> str | None:
    value = first_present(
        get(run_json, "provider_error_kind"),
        get(run_json, "provider", "provider_error_kind"),
    )
    return value if isinstance(value, str) else None


def get(value: Any, *keys: str) -> Any:
    cursor = value
    for key in keys:
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(key)
    return cursor


def first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        integer = int(value)
    except (TypeError, ValueError):
        return None
    return integer if integer > 0 else None
