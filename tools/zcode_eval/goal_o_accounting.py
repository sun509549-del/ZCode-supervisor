"""Goal O accounting for existing ZCode-required 20+ evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from .metrics import COMPARISON_METRIC_FIELDS
except ImportError:  # pragma: no cover - direct script execution
    from metrics import COMPARISON_METRIC_FIELDS

SCHEMA_VERSION = "goal_o_accounting.v1"
CLAIM_FAMILY = "zcode_required_20_live"

OUTCOME_CLAIMABLE = "goal_o_accounting_done_claimable"
OUTCOME_BLOCKED_CODEX_UNAVAILABLE = "goal_o_accounting_done_blocked_codex_orchestration_unavailable"
OUTCOME_BLOCKED_PHASE_SPLIT = "goal_o_accounting_done_blocked_codex_phase_split_unavailable"
OUTCOME_EVIDENCE_MISSING = "terminal_evidence_missing"

ORCHESTRATION_PHASES = {
    "plan",
    "planning",
    "contract_generation",
    "packet_generation",
    "zcode_launch",
    "validation",
    "deterministic_acceptance",
    "acceptance",
    "zcode_self_audit_parse",
    "codex_acceptance_audit",
    "validation_audit",
    "summary_render",
    "report",
    "final_report",
}

ENGINEERING_PHASES = {
    "implementation",
    "runner_repair",
    "harness_repair",
    "usage_capture_repair",
    "debugging",
    "debug",
    "tooling_repair",
}


def read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON payload must be an object: {path}")
    return payload


def read_jsonl_objects(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            payload = json.loads(text)
            if not isinstance(payload, dict):
                raise ValueError(f"{path}:{line_no}: JSONL row must be an object")
            rows.append(payload)
    return rows


def positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None


def usage_metrics(record: dict[str, Any]) -> dict[str, Any] | None:
    usage = record.get("usage") if isinstance(record.get("usage"), dict) else None
    if usage and usage.get("usage_missing") is not True:
        return usage
    if all(field in record for field in COMPARISON_METRIC_FIELDS):
        return record
    return None


def effective_tokens(record: dict[str, Any]) -> int | None:
    usage = usage_metrics(record)
    if not usage:
        return None
    return positive_int(usage.get("effective_codex_work"))


def record_phase(record: dict[str, Any]) -> str | None:
    phase = record.get("phase")
    return phase if isinstance(phase, str) and phase else None


def record_claim_family(record: dict[str, Any]) -> str | None:
    value = record.get("claim_family")
    if isinstance(value, str):
        return value
    context = record.get("context")
    if isinstance(context, dict) and isinstance(context.get("claim_family"), str):
        return context["claim_family"]
    return None


def load_optional_jsonl(paths: list[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        if path.exists():
            records.extend(read_jsonl_objects(path))
    return records


def load_goal_usage(paths: list[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            records.extend(item for item in payload if isinstance(item, dict))
        elif isinstance(payload, dict):
            rows = payload.get("records")
            if isinstance(rows, list):
                records.extend(item for item in rows if isinstance(item, dict))
            else:
                records.append(payload)
    return records


def goal_usage_as_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for record in records:
        tokens = positive_int(record.get("effective_codex_work")) or positive_int(record.get("tokensUsed"))
        if tokens is None:
            normalized.append({"phase": record.get("phase"), "usage": {"usage_missing": True}})
            continue
        normalized.append(
            {
                "phase": record.get("phase"),
                "claim_family": record.get("claim_family"),
                "usage": {"effective_codex_work": tokens, "usage_missing": False},
                "source_type": "goal_tool_usage",
                "classification": record.get("classification"),
            }
        )
    return normalized


def worker_summary(final: dict[str, Any]) -> dict[str, Any]:
    rows = final.get("rows") if isinstance(final.get("rows"), list) else []
    measured_rows = [
        row
        for row in rows
        if isinstance(row, dict)
        and row.get("worker_usage_status") == "measured"
        and row.get("worker_usage_unit") == "tokens"
        and positive_int(row.get("worker_total_tokens")) is not None
    ]
    top_total = positive_int(final.get("worker_total_tokens"))
    row_total = sum(int(row["worker_total_tokens"]) for row in measured_rows)
    measured_count = positive_int(final.get("worker_token_measured_count")) or len(measured_rows)
    return {
        "zcode_worker_total_tokens": top_total or (row_total if row_total > 0 else None),
        "zcode_worker_measured_rows": measured_count,
        "worker_row_token_sum": row_total if row_total > 0 else None,
        "worker_usage_unit": final.get("worker_usage_unit"),
        "worker_usage_source_paths": final.get("worker_usage_source_paths") or [],
    }


def classify_codex_usage(records: list[dict[str, Any]], claim_family: str) -> dict[str, Any]:
    if not records:
        return {
            "status": "unavailable",
            "total": None,
            "phase_tokens": None,
            "engineering_overhead_tokens": None,
            "blocker": "codex_orchestration_usage_unavailable",
            "record_count": 0,
        }

    phase_tokens: dict[str, int] = {}
    overhead = 0
    overhead_seen = False
    unknown_phase = False
    missing_usage = False
    mixed_claim_family = False
    orchestration_seen = False

    for record in records:
        family = record_claim_family(record)
        if family is not None and family != claim_family:
            mixed_claim_family = True
            continue
        phase = record_phase(record)
        tokens = effective_tokens(record)
        if tokens is None:
            missing_usage = True
            continue
        if phase in ENGINEERING_PHASES or record.get("classification") == "engineering_overhead":
            overhead += tokens
            overhead_seen = True
            continue
        if phase in ORCHESTRATION_PHASES:
            phase_tokens[phase] = phase_tokens.get(phase, 0) + tokens
            orchestration_seen = True
            continue
        unknown_phase = True

    if mixed_claim_family:
        return unavailable_result("mixed_claim_family_codex_usage")
    if unknown_phase:
        return phase_split_result("codex_phase_split_unavailable", overhead if overhead_seen else None)
    if missing_usage and not orchestration_seen:
        return unavailable_result("codex_orchestration_usage_unavailable")
    if missing_usage:
        return unavailable_result("codex_orchestration_usage_incomplete")
    if not orchestration_seen:
        return unavailable_result("codex_orchestration_usage_unavailable")

    total = sum(phase_tokens.values())
    if total <= 0:
        return unavailable_result("codex_orchestration_zero_usage_invalid")
    return {
        "status": "measured",
        "total": total,
        "phase_tokens": dict(sorted(phase_tokens.items())),
        "engineering_overhead_tokens": overhead if overhead_seen else None,
        "blocker": None,
        "record_count": len(records),
    }


def unavailable_result(blocker: str) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "total": None,
        "phase_tokens": None,
        "engineering_overhead_tokens": None,
        "blocker": blocker,
        "record_count": 0,
    }


def phase_split_result(blocker: str, overhead: int | None) -> dict[str, Any]:
    return {
        "status": "phase_split_unavailable",
        "total": None,
        "phase_tokens": None,
        "engineering_overhead_tokens": overhead,
        "blocker": blocker,
        "record_count": 0,
    }


def final_outcome_for(report: dict[str, Any]) -> str:
    if report.get("terminal_evidence_missing"):
        return OUTCOME_EVIDENCE_MISSING
    status = report.get("codex_orchestration_token_status")
    if status == "measured" and report.get("total_workflow_savings_claimable") is True:
        return OUTCOME_CLAIMABLE
    if status == "phase_split_unavailable":
        return OUTCOME_BLOCKED_PHASE_SPLIT
    return OUTCOME_BLOCKED_CODEX_UNAVAILABLE


def build_goal_o_accounting_report(
    evidence_root: Path,
    *,
    codex_usage_ledgers: list[Path] | None = None,
    goal_usage_json: list[Path] | None = None,
    archive_root: Path | None = None,
) -> dict[str, Any]:
    final_path = evidence_root / "final-outcome.json"
    if not final_path.exists():
        return missing_report(evidence_root, "final_outcome_json_missing")

    final = read_json_object(final_path)
    worker = worker_summary(final)
    claim_family = final.get("claim_family")
    records = load_optional_jsonl(codex_usage_ledgers or [])
    records.extend(goal_usage_as_records(load_goal_usage(goal_usage_json or [])))
    codex = classify_codex_usage(records, CLAIM_FAMILY)
    terminal_missing = False
    blocker = codex["blocker"]

    if claim_family != CLAIM_FAMILY:
        terminal_missing = True
        blocker = "claim_family_mismatch"
    elif worker["zcode_worker_total_tokens"] is None:
        terminal_missing = True
        blocker = "zcode_worker_token_evidence_missing"
    elif worker["zcode_worker_measured_rows"] != final.get("delegated_rows"):
        terminal_missing = True
        blocker = "zcode_worker_measured_rows_mismatch"

    claimable = (
        not terminal_missing
        and codex["status"] == "measured"
        and worker["zcode_worker_total_tokens"] is not None
        and final.get("delegated_rows") == worker["zcode_worker_measured_rows"]
    )
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "claim_family": claim_family,
        "source_evidence_root": str(evidence_root),
        "archive_root": str(archive_root) if archive_root else None,
        "source_final_outcome": final.get("final_outcome"),
        "zcode_worker_total_tokens": worker["zcode_worker_total_tokens"],
        "zcode_worker_measured_rows": worker["zcode_worker_measured_rows"],
        "delegated_rows": final.get("delegated_rows"),
        "strict_green_count": final.get("strict_green_count"),
        "failure_count": final.get("failure_count"),
        "codex_orchestration_token_status": codex["status"],
        "codex_orchestration_total_tokens": codex["total"],
        "codex_phase_tokens": codex["phase_tokens"],
        "codex_phase_split_status": "available" if codex["status"] == "measured" else "unavailable",
        "engineering_overhead_tokens": codex["engineering_overhead_tokens"],
        "engineering_overhead_status": "measured" if codex["engineering_overhead_tokens"] is not None else "unavailable",
        "total_workflow_savings_claimable": claimable,
        "blocker": None if claimable else blocker,
        "terminal_evidence_missing": terminal_missing,
        "route_used_summary": final.get("route_used_summary"),
        "codex_fallback_count": (final.get("route_used_summary") or {}).get("codex_fallback"),
        "zcode_implemented": final.get("zcode_implemented"),
        "codex_touched_target_artifact": final.get("codex_touched_target_artifact"),
        "production_green_path_enabled": final.get("production_green_path_enabled"),
        "direct_mode_default": final.get("direct_mode_default"),
        "worker_usage_source_paths": worker["worker_usage_source_paths"],
    }
    report["final_outcome"] = final_outcome_for(report)
    return report


def missing_report(evidence_root: Path, blocker: str) -> dict[str, Any]:
    report = {
        "schema_version": SCHEMA_VERSION,
        "claim_family": None,
        "source_evidence_root": str(evidence_root),
        "source_final_outcome": None,
        "zcode_worker_total_tokens": None,
        "zcode_worker_measured_rows": None,
        "delegated_rows": None,
        "strict_green_count": None,
        "codex_orchestration_token_status": "unavailable",
        "codex_orchestration_total_tokens": None,
        "codex_phase_tokens": None,
        "engineering_overhead_tokens": None,
        "total_workflow_savings_claimable": False,
        "blocker": blocker,
        "terminal_evidence_missing": True,
    }
    report["final_outcome"] = OUTCOME_EVIDENCE_MISSING
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--archive-root", type=Path)
    parser.add_argument("--codex-usage-ledger", type=Path, action="append", default=[])
    parser.add_argument("--goal-usage-json", type=Path, action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_goal_o_accounting_report(
        args.evidence_root,
        archive_root=args.archive_root,
        codex_usage_ledgers=args.codex_usage_ledger,
        goal_usage_json=args.goal_usage_json,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
