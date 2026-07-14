"""Live evidence manifest validation for rollout readiness."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


LIVE_EVIDENCE_MANIFEST_SCHEMA_VERSION = "strict_contract_live_evidence_manifest.v1"
LIVE_EVIDENCE_VALIDATION_SCHEMA_VERSION = "strict_contract_live_evidence_validation.v1"
CLAIM_FAMILIES = {
    "direct_orchestrated_delegation_savings",
    "codex_mediated_delegation_savings",
}
LIVE_BENCHMARK_KINDS = {"fixture_ready_plan", "live_provider_benchmark"}
TOKEN_REJECTED_SOURCE_TYPES = {"quota_percent", "credit", "credits", "percent", "quota_credit_percent"}
REQUIRED_FIELDS = (
    "schema_version",
    "benchmark_run_id",
    "benchmark_run_kind",
    "task_count",
    "task_slugs",
    "provider_models",
    "operator",
    "created_at",
    "approval_status",
    "claim_family",
    "strict_result_status",
    "quality_result_status",
    "worker_usage",
    "codex_usage",
    "total_workflow_savings",
    "artifact_paths",
)


def validate_live_evidence_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a Goal H live-evidence manifest without trusting live claims."""

    errors: list[str] = []
    warnings: list[str] = []
    _validate_required_fields(payload, errors)
    _validate_identity(payload, errors)
    _validate_task_shape(payload, errors)
    families = _validate_claim_family(payload, errors)
    _validate_usage_layer("worker_usage", payload.get("worker_usage"), errors, warnings)
    _validate_usage_layer("codex_usage", payload.get("codex_usage"), errors, warnings)
    _validate_total_workflow(payload, families, errors)
    _validate_live_run_controls(payload, errors, warnings)

    workflow_status = _manifest_workflow_status(payload)
    return {
        "schema_version": LIVE_EVIDENCE_VALIDATION_SCHEMA_VERSION,
        "manifest_schema_version": payload.get("schema_version"),
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "benchmark_run_id": payload.get("benchmark_run_id"),
        "benchmark_run_kind": payload.get("benchmark_run_kind"),
        "task_count": payload.get("task_count"),
        "claim_families": sorted(families),
        "live_benchmark_status": _live_status(payload, errors),
        "worker_usage_status": _manifest_worker_status(payload),
        "total_workflow_savings_status": workflow_status,
        "claimable_total_workflow_savings": workflow_status == "measured" and not errors,
        "approval_status": payload.get("approval_status"),
    }


def summarize_live_evidence_manifest(path: Path | None) -> dict[str, Any]:
    if path is None:
        return _missing_summary("not_provided", [])
    payload = _read_json(path)
    if payload is None:
        return _missing_summary("missing_or_invalid_json", ["manifest:missing_or_invalid_json"])
    validation = validate_live_evidence_manifest(payload)
    return {
        "live_benchmark_status": validation["live_benchmark_status"],
        "worker_usage_status": validation["worker_usage_status"],
        "total_workflow_savings_status": validation["total_workflow_savings_status"],
        "live_evidence_manifest_status": "valid" if validation["valid"] else "invalid",
        "live_evidence_manifest_errors": validation["errors"],
        "live_evidence_manifest_warnings": validation["warnings"],
        "live_evidence_manifest_validation": validation,
    }


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _missing_summary(status: str, errors: list[str]) -> dict[str, Any]:
    return {
        "live_benchmark_status": "missing" if errors else "unavailable",
        "worker_usage_status": "unavailable",
        "total_workflow_savings_status": "blocked_unavailable_usage",
        "live_evidence_manifest_status": status,
        "live_evidence_manifest_errors": errors,
    }


def _validate_required_fields(payload: dict[str, Any], errors: list[str]) -> None:
    for key in REQUIRED_FIELDS:
        if key not in payload:
            errors.append(f"{key}:missing")


def _validate_identity(payload: dict[str, Any], errors: list[str]) -> None:
    if payload.get("schema_version") != LIVE_EVIDENCE_MANIFEST_SCHEMA_VERSION:
        errors.append("schema_version:unsupported")
    if payload.get("benchmark_run_kind") not in LIVE_BENCHMARK_KINDS:
        errors.append("benchmark_run_kind:unsupported")


def _validate_task_shape(payload: dict[str, Any], errors: list[str]) -> None:
    task_count = payload.get("task_count")
    task_slugs = payload.get("task_slugs")
    if not isinstance(task_count, int) or task_count < 20:
        errors.append("task_count:must_be_at_least_20")
    if not isinstance(task_slugs, list) or not all(isinstance(item, str) for item in task_slugs):
        errors.append("task_slugs:must_be_string_list")
    elif isinstance(task_count, int) and len(set(task_slugs)) != task_count:
        errors.append("task_slugs:unique_count_must_match_task_count")
    provider_models = payload.get("provider_models")
    if not isinstance(provider_models, list) or not provider_models:
        errors.append("provider_models:must_be_non_empty_list")
    artifact_paths = payload.get("artifact_paths")
    if not isinstance(artifact_paths, list) or not all(isinstance(item, str) for item in artifact_paths):
        errors.append("artifact_paths:must_be_string_list")


def _validate_claim_family(payload: dict[str, Any], errors: list[str]) -> set[str]:
    families = _claim_families(payload)
    if not families:
        errors.append("claim_family:missing")
    elif not families.issubset(CLAIM_FAMILIES):
        errors.append("claim_family:unsupported")
    elif len(families) > 1:
        errors.append("claim_family:mixed_direct_and_codex_mediated")
    return families


def _claim_families(payload: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    family = payload.get("claim_family")
    if isinstance(family, str):
        values.add(family)
    families = payload.get("claim_families")
    if isinstance(families, list):
        values.update(item for item in families if isinstance(item, str))
    savings = payload.get("total_workflow_savings")
    if isinstance(savings, dict) and isinstance(savings.get("claim_scope"), str):
        values.add(savings["claim_scope"])
    return values


def _validate_usage_layer(name: str, usage: Any, errors: list[str], warnings: list[str]) -> None:
    if not isinstance(usage, dict):
        errors.append(f"{name}:missing_object")
        return
    status = usage.get("status")
    if status not in {"measured", "unavailable"}:
        errors.append(f"{name}:invalid_status")
        return
    source_type = str(usage.get("source_type") or "").strip()
    rejected_counts = any(key in usage for key in ("quota_percent", "credit_balance", "credits"))
    if status == "measured":
        _validate_measured_usage(name, usage, source_type, rejected_counts, errors)
    elif not usage.get("no_usage_reason"):
        warnings.append(f"{name}:unavailable_usage_should_include_no_usage_reason")


def _validate_measured_usage(
    name: str,
    usage: dict[str, Any],
    source_type: str,
    rejected_counts: bool,
    errors: list[str],
) -> None:
    if source_type in TOKEN_REJECTED_SOURCE_TYPES or rejected_counts:
        errors.append(f"{name}:quota_credit_percent_not_token_evidence")
    if usage.get("unit") != "tokens":
        errors.append(f"{name}:measured_usage_requires_token_unit")
    if not usage.get("source_path"):
        errors.append(f"{name}:measured_usage_requires_source_path")
    if not isinstance(usage.get("total_tokens"), int) or usage.get("total_tokens") <= 0:
        errors.append(f"{name}:measured_usage_requires_positive_total_tokens")


def _validate_total_workflow(payload: dict[str, Any], families: set[str], errors: list[str]) -> None:
    savings = payload.get("total_workflow_savings")
    if not isinstance(savings, dict):
        errors.append("total_workflow_savings:missing_object")
        return
    status = savings.get("status")
    if status not in {"measured", "blocked"}:
        errors.append("total_workflow_savings:invalid_status")
        return
    if status == "measured":
        _validate_measured_workflow(payload, families, savings, errors)
    elif not savings.get("no_claim_reason"):
        errors.append("total_workflow_savings:blocked_requires_no_claim_reason")


def _validate_measured_workflow(
    payload: dict[str, Any],
    families: set[str],
    savings: dict[str, Any],
    errors: list[str],
) -> None:
    if savings.get("unit") != "tokens":
        errors.append("total_workflow_savings:measured_requires_token_unit")
    if not _usage_measured_tokens(payload.get("worker_usage")):
        errors.append("total_workflow_savings:requires_worker_measured_tokens")
    if not _usage_measured_tokens(payload.get("codex_usage")):
        errors.append("total_workflow_savings:requires_codex_measured_tokens")
    if savings.get("claim_scope") not in CLAIM_FAMILIES or {savings.get("claim_scope")} != families:
        errors.append("total_workflow_savings:claim_scope_mismatch")


def _validate_live_run_controls(payload: dict[str, Any], errors: list[str], warnings: list[str]) -> None:
    approval = payload.get("approval_status")
    if approval not in {"pending", "approved"}:
        errors.append("approval_status:invalid")
    if approval == "approved" and not payload.get("approval_reference"):
        errors.append("approval_status:approved_requires_reference")
    if payload.get("production_green_path_enabled") is True:
        errors.append("production_green_path_enabled:must_be_false")
    if payload.get("direct_mode_default") is True:
        errors.append("direct_mode_default:must_be_false")
    if payload.get("benchmark_run_kind") == "live_provider_benchmark":
        _validate_live_benchmark_controls(payload, errors)
    else:
        warnings.append("fixture_or_plan_manifest_is_not_live_production_evidence")


def _validate_live_benchmark_controls(payload: dict[str, Any], errors: list[str]) -> None:
    if payload.get("provider_auth_status") != "pass":
        errors.append("provider_auth_status:live_run_requires_pass")
    if payload.get("partial_run") is True:
        errors.append("partial_run:live_run_must_not_be_partial")
    if not _usage_measured_tokens(payload.get("worker_usage")):
        errors.append("worker_usage:live_run_requires_measured_tokens")
    if not _usage_measured_tokens(payload.get("codex_usage")):
        errors.append("codex_usage:live_run_requires_measured_tokens")
    if payload.get("strict_result_status") != "pass":
        errors.append("strict_result_status:live_run_requires_pass")
    if payload.get("quality_result_status") != "pass":
        errors.append("quality_result_status:live_run_requires_pass")
    if payload.get("artifact_paths") == []:
        errors.append("artifact_paths:live_run_requires_artifacts")


def _usage_measured_tokens(usage: Any) -> bool:
    if not isinstance(usage, dict):
        return False
    return (
        usage.get("status") == "measured"
        and usage.get("unit") == "tokens"
        and bool(usage.get("source_path"))
        and isinstance(usage.get("total_tokens"), int)
        and usage.get("total_tokens") > 0
    )


def _live_status(payload: dict[str, Any], errors: list[str]) -> str:
    if errors:
        return "invalid_manifest"
    if payload.get("benchmark_run_kind") != "live_provider_benchmark":
        return "unavailable"
    return "passed"


def _manifest_worker_status(payload: dict[str, Any]) -> str:
    usage = payload.get("worker_usage")
    if _usage_measured_tokens(usage):
        return "measured_tokens"
    if isinstance(usage, dict) and (
        "worker_usage_empty_sidecar" in str(usage.get("no_usage_reason") or "")
        or (
            usage.get("no_usage_reason") == "provider_success_without_usage_payload"
            and isinstance(usage.get("usage"), dict)
            and not usage.get("usage")
        )
    ):
        return "worker_usage_empty_sidecar"
    return "unavailable"


def _manifest_workflow_status(payload: dict[str, Any]) -> str:
    savings = payload.get("total_workflow_savings")
    if not isinstance(savings, dict) or savings.get("status") != "measured":
        return "blocked_unavailable_usage"
    if not _usage_measured_tokens(payload.get("worker_usage")):
        return "blocked_unavailable_usage"
    if not _usage_measured_tokens(payload.get("codex_usage")):
        return "blocked_unavailable_usage"
    return "measured"
