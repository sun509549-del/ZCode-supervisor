"""Conservative rollout readiness assessment for strict direct delegation."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

try:
    from .live_evidence import summarize_live_evidence_manifest
    from .strict_contract_breakdown import DEFAULT_REPAIR_ATTEMPT_BUDGET, REPAIR_POLICY_VERSION
    from .strict_contract_comparison import build_parser
    from .strict_contract_tasks import task_specs
except ImportError:  # pragma: no cover - direct script execution
    from live_evidence import summarize_live_evidence_manifest
    from strict_contract_breakdown import DEFAULT_REPAIR_ATTEMPT_BUDGET, REPAIR_POLICY_VERSION
    from strict_contract_comparison import build_parser
    from strict_contract_tasks import task_specs


ROLLOUT_READINESS_SCHEMA_VERSION = "strict_contract_rollout_readiness.v1"
DEFAULT_EVIDENCE_PATH = Path("artifacts/reports/rollout-readiness/latest.json")
GREEN_PATH_ENV_VARS = (
    "ZCODE_CANDIDATE_GATE_GREEN_PATH_NON_LLM",
    "ZCODE_CANDIDATE_GATE_STRICT_SHADOW",
    "ZCODE_PRODUCTION_GREEN_PATH_SKIP",
)
FATAL_CHECK_IDS = {
    "benchmark_task_count_20",
    "bounded_repair_policy",
    "direct_mode_default_disabled",
    "production_green_path_disabled",
    "strict_gates",
    "claim_boundaries",
    "live_evidence_manifest_valid",
}


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() not in {"", "0", "false", "no", "off"}


def production_green_path_enabled(environ: dict[str, str] | None = None) -> bool:
    env = environ if environ is not None else os.environ
    return any(_truthy(env.get(name)) for name in GREEN_PATH_ENV_VARS)


def direct_mode_default() -> bool:
    return build_parser().parse_args([]).delegation_execution == "direct"


def benchmark_task_count(base: Path | None = None) -> int:
    root = base or Path(".local/rollout-readiness-task-count")
    return len(task_specs(root))


def repair_policy_status() -> str:
    if not REPAIR_POLICY_VERSION:
        return "missing"
    if DEFAULT_REPAIR_ATTEMPT_BUDGET > 1:
        return "unbounded"
    return "enabled_bounded"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def summarize_live_benchmark(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {
            "live_benchmark_status": "unavailable",
            "worker_usage_status": "unavailable",
            "total_workflow_savings_status": "blocked_unavailable_usage",
        }
    payload = _read_json(path)
    if payload is None:
        return {
            "live_benchmark_status": "missing",
            "worker_usage_status": "unavailable",
            "total_workflow_savings_status": "blocked_unavailable_usage",
        }

    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    delegated = [row for row in rows if row.get("mode") in {"zcode_delegated", "zcode_direct_launcher"}]
    task_count = len({row.get("task") for row in delegated if row.get("task")})
    provider_blocker = _provider_blocker_status(delegated)
    live_status = (
        provider_blocker
        if provider_blocker
        else "passed" if task_count >= 20 and delegated and not payload.get("dry_run") else "insufficient"
    )
    worker_measured = [
        row
        for row in delegated
        if row.get("worker_usage_status") == "measured" and row.get("worker_usage_unit") == "tokens"
    ]
    worker_status = _worker_usage_status(delegated, worker_measured)
    workflow_status = _total_workflow_status(payload)
    return {
        "live_benchmark_status": live_status,
        "provider_blocker_status": provider_blocker,
        "worker_usage_status": worker_status,
        "total_workflow_savings_status": workflow_status,
    }


def _provider_blocker_status(rows: list[dict[str, Any]]) -> str | None:
    if any(row.get("provider_error_kind") == "provider_overload" for row in rows):
        return "provider_overload_infrastructure_blocker"
    if any(row.get("blocker_kind") == "infrastructure_blocker" for row in rows):
        return "infrastructure_blocker"
    return None


def _worker_usage_status(rows: list[dict[str, Any]], measured: list[dict[str, Any]]) -> str:
    if rows and len(measured) == len(rows):
        return "measured_tokens"
    if any(
        row.get("worker_usage_empty_sidecar") is True
        or row.get("worker_usage_no_usage_reason") == "worker_usage_empty_sidecar"
        for row in rows
    ):
        return "worker_usage_empty_sidecar"
    return "unavailable"


def _total_workflow_status(payload: dict[str, Any]) -> str:
    accounting = payload.get("end_to_end_accounting")
    if not isinstance(accounting, dict):
        return "blocked_unavailable_usage"
    for scope in accounting.values():
        if not isinstance(scope, dict):
            continue
        for arm in scope.values():
            if not isinstance(arm, dict):
                continue
            layer = arm.get("total_workflow_savings")
            if isinstance(layer, dict) and layer.get("usage_status") == "measured":
                return "measured"
    return "blocked_unavailable_usage"


def _check(check_id: str, passed: bool, reason: str, required: bool = True) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "required": required,
        "status": "pass" if passed else "blocked",
        "reason": reason,
    }


def _owner_actions(checks: list[dict[str, Any]]) -> list[str]:
    actions: list[str] = []
    for check in checks:
        if check["status"] == "pass":
            continue
        check_id = check["check_id"]
        if check_id == "live_20_provider_benchmark":
            if "provider_overload" in str(check["reason"]):
                actions.append("Wait for provider availability recovery and get fresh approval before retrying live 20+ evidence.")
            else:
                actions.append("Run and attach live 20+ provider benchmark evidence before production rollout.")
        elif check_id == "worker_token_usage":
            actions.append("Capture delegated worker token usage; quota percent or credits are not enough.")
        elif check_id == "total_workflow_savings":
            actions.append("Keep total workflow savings and production savings claims blocked.")
        elif check_id == "manual_approval":
            actions.append("Record explicit maintainer approval before opt-in rollout.")
        else:
            actions.append(f"Fix blocked readiness check: {check_id}.")
    return actions


def assess_rollout_readiness(
    *,
    live_benchmark_summary: Path | None = None,
    live_evidence_manifest: Path | None = None,
    manual_approval_status: str = "pending",
    production_green_path: bool | None = None,
    benchmark_count: int | None = None,
    live_benchmark_status: str | None = None,
    worker_usage_status: str | None = None,
    total_workflow_savings_status: str | None = None,
    claim_boundary_status: str = "protected",
    strict_gates_status: str = "pass",
    direct_default: bool | None = None,
) -> dict[str, Any]:
    live = _select_live_summary(live_evidence_manifest, live_benchmark_summary)
    count = benchmark_count if benchmark_count is not None else benchmark_task_count()
    green_enabled = production_green_path_enabled() if production_green_path is None else production_green_path
    direct_default_enabled = direct_mode_default() if direct_default is None else direct_default
    live_status = live_benchmark_status or live["live_benchmark_status"]
    worker_status = worker_usage_status or live["worker_usage_status"]
    workflow_status = total_workflow_savings_status or live["total_workflow_savings_status"]
    repair_status = repair_policy_status()
    manual_status = manual_approval_status
    required_checks = _build_required_checks(
        count=count,
        repair_status=repair_status,
        direct_default_enabled=direct_default_enabled,
        green_enabled=green_enabled,
        strict_gates_status=strict_gates_status,
        claim_boundary_status=claim_boundary_status,
        live_status=live_status,
        worker_status=worker_status,
        workflow_status=workflow_status,
        manual_status=manual_status,
        live=live,
        manifest_present=live_evidence_manifest is not None,
    )
    blocked_checks = [check for check in required_checks if check["status"] != "pass"]
    rollout_status, rollout_mode = _rollout_state(blocked_checks)
    return _readiness_payload(
        rollout_status=rollout_status,
        rollout_mode=rollout_mode,
        green_enabled=green_enabled,
        direct_default_enabled=direct_default_enabled,
        required_checks=required_checks,
        blocked_checks=blocked_checks,
        manual_status=manual_status,
        count=count,
        live_status=live_status,
        worker_status=worker_status,
        workflow_status=workflow_status,
        repair_status=repair_status,
        claim_boundary_status=claim_boundary_status,
        live_benchmark_summary=live_benchmark_summary,
        live_evidence_manifest=live_evidence_manifest,
        live=live,
    )


def _select_live_summary(live_evidence_manifest: Path | None, live_benchmark_summary: Path | None) -> dict[str, Any]:
    if live_evidence_manifest is not None:
        return summarize_live_evidence_manifest(live_evidence_manifest)
    return summarize_live_benchmark(live_benchmark_summary)


def _build_required_checks(
    *,
    count: int,
    repair_status: str,
    direct_default_enabled: bool,
    green_enabled: bool,
    strict_gates_status: str,
    claim_boundary_status: str,
    live_status: str,
    worker_status: str,
    workflow_status: str,
    manual_status: str,
    live: dict[str, Any],
    manifest_present: bool,
) -> list[dict[str, Any]]:
    required_checks = [
        _check("benchmark_task_count_20", count >= 20, f"benchmark_task_count={count}"),
        _check("bounded_repair_policy", repair_status == "enabled_bounded", repair_status),
        _check("direct_mode_default_disabled", not direct_default_enabled, f"direct_mode_default={direct_default_enabled}"),
        _check("production_green_path_disabled", not green_enabled, f"production_green_path_enabled={green_enabled}"),
        _check("strict_gates", strict_gates_status == "pass", strict_gates_status),
        _check("claim_boundaries", claim_boundary_status == "protected", claim_boundary_status),
        _check("live_20_provider_benchmark", live_status == "passed", live_status),
        _check("worker_token_usage", worker_status == "measured_tokens", worker_status),
        _check("total_workflow_savings", workflow_status == "measured", workflow_status),
        _check("manual_approval", manual_status == "approved", manual_status),
    ]
    if manifest_present:
        manifest_status = live.get("live_evidence_manifest_status")
        manifest_reason = ",".join(live.get("live_evidence_manifest_errors", [])) or str(manifest_status)
        required_checks.insert(6, _check("live_evidence_manifest_valid", manifest_status == "valid", manifest_reason))
    return required_checks


def _rollout_state(blocked_checks: list[dict[str, Any]]) -> tuple[str, str]:
    fatal_blockers = [check for check in blocked_checks if check["check_id"] in FATAL_CHECK_IDS]
    if fatal_blockers:
        return "blocked", "docs_only"
    if not blocked_checks:
        return "ready", "production_ready"
    if {check["check_id"] for check in blocked_checks} == {"manual_approval"}:
        return "candidate", "opt_in_candidate"
    return "fixture_ready", "dry_run_only"


def _readiness_payload(
    *,
    rollout_status: str,
    rollout_mode: str,
    green_enabled: bool,
    direct_default_enabled: bool,
    required_checks: list[dict[str, Any]],
    blocked_checks: list[dict[str, Any]],
    manual_status: str,
    count: int,
    live_status: str,
    worker_status: str,
    workflow_status: str,
    repair_status: str,
    claim_boundary_status: str,
    live_benchmark_summary: Path | None,
    live_evidence_manifest: Path | None,
    live: dict[str, Any],
) -> dict[str, Any]:
    warnings = [
        "Dry-run and fixture evidence do not prove production savings.",
        "Direct and codex-mediated claim families must remain separate.",
        "Production green-path skip must remain disabled by default.",
    ]
    return {
        "rollout_readiness_schema_version": ROLLOUT_READINESS_SCHEMA_VERSION,
        "rollout_status": rollout_status,
        "rollout_mode": rollout_mode,
        "production_green_path_enabled": green_enabled,
        "direct_mode_default": direct_default_enabled,
        "required_checks": required_checks,
        "passed_checks": [check["check_id"] for check in required_checks if check["status"] == "pass"],
        "blocked_checks": blocked_checks,
        "manual_approval_required": True,
        "manual_approval_status": manual_status,
        "readiness_blockers": [f"{check['check_id']}:{check['reason']}" for check in blocked_checks],
        "readiness_warnings": warnings,
        "benchmark_task_count": count,
        "live_benchmark_status": live_status,
        "provider_blocker_status": live.get("provider_blocker_status"),
        "worker_usage_status": worker_status,
        "total_workflow_savings_status": workflow_status,
        "repair_policy_status": repair_status,
        "claim_boundary_status": claim_boundary_status,
        "rollback_plan": [
            "Keep direct mode behind explicit wrapper and --delegation-execution direct.",
            "Do not enable production green-path skip.",
            "Revert readiness command/docs if any guard fails.",
        ],
        "owner_action_required": _owner_actions(blocked_checks),
        "optional_checks": [
            "external_human_review",
            "additional provider benchmark repetitions",
            "manual UX review of readiness docs",
        ],
        "live_benchmark_summary": str(live_benchmark_summary) if live_benchmark_summary else None,
        "live_evidence_manifest": str(live_evidence_manifest) if live_evidence_manifest else None,
        "live_evidence_manifest_status": live.get("live_evidence_manifest_status", "not_provided"),
        "live_evidence_manifest_errors": live.get("live_evidence_manifest_errors", []),
        "live_evidence_manifest_warnings": live.get("live_evidence_manifest_warnings", []),
    }


def write_readiness(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
