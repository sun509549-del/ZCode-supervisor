"""Goal P Codex-worker comparison report helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "goal_p_codex_worker_comparison.v1"
CLAIM_FAMILY = "codex_worker_same_packet_baseline"
ROUTE_USED = "codex_worker_baseline"
ZCODE_CLAIM_FAMILY = "zcode_required_20_live"
OUTCOME_MEASURED = "goal_p_codex_worker_comparison_done_measured"
OUTCOME_USAGE_UNAVAILABLE = "goal_p_codex_worker_comparison_done_usage_unavailable"
OUTCOME_RUNNER_UNAVAILABLE = "terminal_codex_worker_runner_unavailable"
OUTCOME_EVIDENCE_MISSING = "terminal_evidence_missing"
OUTCOME_USAGE_UNAVAILABLE_AFTER_REPAIR = "terminal_codex_worker_usage_capture_unavailable_after_repair"
OUTCOME_STRICT_UNAVAILABLE_AFTER_REPAIR = "terminal_codex_worker_strict_acceptance_unavailable_after_repair"


def read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON payload must be an object: {path}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None


def zcode_summary(evidence_root: Path) -> dict[str, Any]:
    final_path = evidence_root / "final-outcome.json"
    if not final_path.exists():
        raise FileNotFoundError(f"missing final-outcome.json: {final_path}")
    final = read_json_object(final_path)
    if final.get("claim_family") != ZCODE_CLAIM_FAMILY:
        raise ValueError("ZCode evidence claim_family mismatch")
    return {
        "zcode_worker_total_tokens": final.get("worker_total_tokens"),
        "zcode_worker_measured_rows": final.get("worker_token_measured_count"),
        "zcode_strict_green_count": final.get("strict_green_count"),
        "zcode_task_count": final.get("task_count"),
        "zcode_delegated_rows": final.get("delegated_rows"),
        "zcode_claim_family": final.get("claim_family"),
        "zcode_source_final_outcome": final.get("final_outcome"),
    }


def usage_rollup(rows: list[dict[str, Any]]) -> dict[str, Any]:
    measured = [
        row for row in rows
        if row.get("codex_worker_usage_status") == "measured"
        and positive_int(row.get("codex_worker_total_tokens")) is not None
    ]
    if len(measured) == len(rows) and rows:
        return {
            "codex_worker_usage_status": "measured",
            "codex_worker_total_tokens": sum(int(row["codex_worker_total_tokens"]) for row in measured),
            "codex_worker_measured_rows": len(measured),
        }
    return {
        "codex_worker_usage_status": "unavailable" if not measured else "partial",
        "codex_worker_total_tokens": None,
        "codex_worker_measured_rows": len(measured),
    }


def final_outcome(
    runner_available: bool,
    usage_measured: bool,
    *,
    strict_available: bool = True,
    repair_attempted: bool = False,
) -> str:
    if not runner_available:
        return OUTCOME_RUNNER_UNAVAILABLE
    if repair_attempted and not strict_available:
        return OUTCOME_STRICT_UNAVAILABLE_AFTER_REPAIR
    if repair_attempted and not usage_measured:
        return OUTCOME_USAGE_UNAVAILABLE_AFTER_REPAIR
    return OUTCOME_MEASURED if usage_measured else OUTCOME_USAGE_UNAVAILABLE


def report_blocker(
    *,
    runner_available: bool,
    strict_available: bool,
    measured: bool,
    repair_attempted: bool,
) -> str | None:
    if not runner_available:
        return "codex_worker_rows_below_required_minimum"
    if repair_attempted and not strict_available:
        return OUTCOME_STRICT_UNAVAILABLE_AFTER_REPAIR
    if repair_attempted and not measured:
        return OUTCOME_USAGE_UNAVAILABLE_AFTER_REPAIR
    if not measured:
        return "codex_worker_usage_unavailable"
    return None


def aggregate_validation_rc(rows: list[dict[str, Any]]) -> int:
    return 0 if rows and all(row.get("final_validation_rc") == 0 for row in rows) else 1


def aggregate_acceptance_rc(rows: list[dict[str, Any]]) -> int:
    values = [row.get("acceptance_rc") for row in rows if row.get("acceptance_rc") is not None]
    return 0 if values and all(value == 0 for value in values) else 1


def build_report(
    *,
    comparison_root: Path,
    evidence_root: Path,
    archive_root: Path | None,
    rows: list[dict[str, Any]],
    min_task_count: int,
    repair_attempted: bool = False,
) -> dict[str, Any]:
    zcode = zcode_summary(evidence_root)
    usage = usage_rollup(rows)
    strict_green = sum(1 for row in rows if row.get("strict_accepted") is True)
    runner_available = len(rows) >= min_task_count
    measured = usage["codex_worker_usage_status"] == "measured"
    strict_available = strict_green > 0
    blocker = report_blocker(
        runner_available=runner_available,
        strict_available=strict_available,
        measured=measured,
        repair_attempted=repair_attempted,
    )
    outcome = final_outcome(
        runner_available, measured, strict_available=strict_available, repair_attempted=repair_attempted
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "final_outcome": outcome,
        "comparison_root": str(comparison_root),
        "source_zcode_evidence_root": str(evidence_root),
        "archive_root": str(archive_root) if archive_root else None,
        "route_used": ROUTE_USED,
        "claim_family": CLAIM_FAMILY,
        "zcode_claim_family": zcode["zcode_claim_family"],
        "task_count": len(rows),
        "codex_worker_rows": len(rows),
        "strict_green_count": strict_green,
        "final_validation_rc": aggregate_validation_rc(rows),
        "acceptance_rc": aggregate_acceptance_rc(rows),
        **usage,
        **zcode,
        "codex_orchestration_token_status": "unavailable",
        "codex_orchestration_total_tokens": None,
        "total_workflow_savings_claimable": False,
        "blocker": blocker or "codex_orchestration_usage_unavailable",
        "production_green_path_enabled": False,
        "direct_mode_default": False,
        "strict_gate_weakened": False,
        "rows": rows,
    }


def write_markdown_report(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Goal P Codex Worker Comparison",
        "",
        f"- final_outcome: `{report['final_outcome']}`",
        f"- task_count: `{report['task_count']}`",
        f"- codex_worker_rows: `{report['codex_worker_rows']}`",
        f"- codex strict_green_count: `{report['strict_green_count']}`",
        f"- zcode strict_green_count: `{report['zcode_strict_green_count']}`",
        f"- codex_worker_usage_status: `{report['codex_worker_usage_status']}`",
        f"- codex_worker_total_tokens: `{report['codex_worker_total_tokens']}`",
        f"- zcode_worker_total_tokens: `{report['zcode_worker_total_tokens']}`",
        f"- total_workflow_savings_claimable: `{report['total_workflow_savings_claimable']}`",
        f"- blocker: `{report['blocker']}`",
        "",
        "| task_id | quality | strict | validation_rc | acceptance_rc | usage | tokens |",
        "|---|---:|---:|---:|---:|---|---:|",
    ]
    for row in report["rows"]:
        lines.append(
            f"| {row['task_id']} | {row['quality']} | {row['strict_accepted']} | "
            f"{row['final_validation_rc']} | {row['acceptance_rc']} | "
            f"{row['codex_worker_usage_status']} | {row['codex_worker_total_tokens']} |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def runner_unavailable_report(args: Any, blocker: str) -> dict[str, Any]:
    try:
        zcode = zcode_summary(args.zcode_evidence_root)
    except (FileNotFoundError, ValueError):
        zcode = {}
    report = {
        "schema_version": SCHEMA_VERSION,
        "final_outcome": OUTCOME_RUNNER_UNAVAILABLE,
        "comparison_root": str(args.comparison_root),
        "source_zcode_evidence_root": str(args.zcode_evidence_root),
        "route_used": ROUTE_USED,
        "claim_family": CLAIM_FAMILY,
        "task_count": 0,
        "codex_worker_rows": 0,
        "strict_green_count": 0,
        "final_validation_rc": None,
        "acceptance_rc": None,
        "codex_worker_usage_status": "unavailable",
        "codex_worker_total_tokens": None,
        "codex_worker_measured_rows": 0,
        **zcode,
        "codex_orchestration_token_status": "unavailable",
        "codex_orchestration_total_tokens": None,
        "total_workflow_savings_claimable": False,
        "blocker": blocker,
        "rows": [],
    }
    write_json(args.comparison_root / "final-outcome.json", report)
    return report
