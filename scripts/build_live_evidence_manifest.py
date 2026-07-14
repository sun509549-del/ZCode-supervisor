#!/usr/bin/env python3
"""Build a live evidence manifest from a strict-contract summary."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.zcode_eval.live_evidence import LIVE_EVIDENCE_MANIFEST_SCHEMA_VERSION


DELEGATED_MODES = {"zcode_delegated", "zcode_direct_launcher"}
CLAIM_FAMILY_BY_MODE = {
    "zcode_delegated": "codex_mediated_delegation_savings",
    "zcode_direct_launcher": "direct_orchestrated_delegation_savings",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--operator", required=True)
    parser.add_argument("--approval-reference", required=True)
    parser.add_argument("--run-id")
    return parser


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"summary must be a JSON object: {path}")
    return payload


def ordered_unique(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def delegated_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = summary.get("rows")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict) and row.get("mode") in DELEGATED_MODES]


def claim_family(rows: list[dict[str, Any]]) -> str:
    families = {
        row.get("claim_family") or CLAIM_FAMILY_BY_MODE.get(str(row.get("mode")))
        for row in rows
        if row.get("mode") in DELEGATED_MODES
    }
    families = {family for family in families if isinstance(family, str)}
    if len(families) != 1:
        raise SystemExit(f"expected one claim family, got {sorted(families)}")
    return next(iter(families))


def token_sum(rows: list[dict[str, Any]], field: str) -> int | None:
    values = [row.get(field) for row in rows if isinstance(row.get(field), int)]
    return sum(values) if values else None


def no_usage_reason(rows: list[dict[str, Any]], field: str, fallback: str) -> str:
    reasons = ordered_unique([row.get(field) for row in rows])
    return ";".join(reasons) if reasons else fallback


def worker_usage(rows: list[dict[str, Any]], summary_path: Path) -> dict[str, Any]:
    measured = [
        row
        for row in rows
        if row.get("worker_usage_status") == "measured"
        and row.get("worker_usage_unit") == "tokens"
        and isinstance(row.get("worker_total_tokens"), int)
        and row.get("worker_total_tokens") > 0
        and row.get("worker_usage_source_path")
    ]
    if len(measured) != len(rows) or not rows:
        return {
            "status": "unavailable",
            "unit": None,
            "source_path": None,
            "source_type": "usage_accounting",
            "no_usage_reason": no_usage_reason(rows, "worker_usage_no_usage_reason", "worker_token_usage_unavailable"),
        }
    source_types = ordered_unique([row.get("worker_usage_capture_method") for row in measured])
    source_type = source_types[0] if len(source_types) == 1 else "mixed_worker_usage_sources"
    if not source_types:
        source_type = "zcode_cli_json_usage"
    payload: dict[str, Any] = {
        "status": "measured",
        "unit": "tokens",
        "source_path": str(summary_path),
        "source_type": source_type,
        "total_tokens": sum(int(row["worker_total_tokens"]) for row in rows),
        "source_paths": ordered_unique([row.get("worker_usage_source_path") for row in rows]),
    }
    for target, field in (("input_tokens", "worker_input_tokens"), ("output_tokens", "worker_output_tokens")):
        value = token_sum(rows, field)
        if value is not None:
            payload[target] = value
    reasoning = token_sum(rows, "worker_reasoning_tokens")
    if reasoning is not None:
        payload["reasoning_tokens"] = reasoning
    return payload


def codex_usage(summary: dict[str, Any], summary_path: Path) -> dict[str, Any]:
    rows = [row for row in summary.get("rows", []) if isinstance(row, dict)]
    measured = [row for row in rows if row.get("codex_usage_status") == "measured" and isinstance(row.get("codex_effective_work"), int)]
    if not measured:
        return {
            "status": "unavailable",
            "unit": None,
            "source_path": None,
            "source_type": "codex_exec_jsonl",
            "no_usage_reason": "codex_usage_unavailable",
        }
    payload: dict[str, Any] = {
        "status": "measured",
        "unit": "tokens",
        "source_path": str(summary_path),
        "source_type": "codex_exec_jsonl",
        "total_tokens": sum(int(row["codex_effective_work"]) for row in measured),
    }
    for target, key in (("input_tokens", "uncached_input_tokens"), ("output_tokens", "output_tokens")):
        value = usage_sum(measured, key)
        if value is not None:
            payload[target] = value
    reasoning = usage_sum(measured, "reasoning_output_tokens")
    if reasoning is not None:
        payload["reasoning_tokens"] = reasoning
    return payload


def usage_sum(rows: list[dict[str, Any]], key: str) -> int | None:
    values: list[int] = []
    for row in rows:
        usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
        value = usage.get(key)
        if isinstance(value, int):
            values.append(value)
    return sum(values) if values else None


def total_workflow_savings(summary: dict[str, Any], family: str) -> dict[str, Any]:
    layer = workflow_layer(summary)
    if layer and layer.get("usage_status") == "measured":
        return {"status": "measured", "unit": "tokens", "claim_scope": family}
    reason = layer.get("no_usage_reason") if isinstance(layer, dict) else None
    return {
        "status": "blocked",
        "unit": None,
        "claim_scope": family,
        "no_claim_reason": reason or "total_workflow_savings_unavailable",
    }


def workflow_layer(summary: dict[str, Any]) -> dict[str, Any] | None:
    accounting = summary.get("end_to_end_accounting")
    if not isinstance(accounting, dict):
        return None
    for scope in ("strict_green_subset", "quality_green_subset", "all_rows"):
        arms = accounting.get(scope)
        if not isinstance(arms, dict):
            continue
        for arm in arms.values():
            if isinstance(arm, dict) and isinstance(arm.get("total_workflow_savings"), dict):
                return arm["total_workflow_savings"]
    return None


def provider_models(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    models: list[dict[str, str]] = []
    for row in rows:
        provider = str(row.get("worker_provider") or "unavailable")
        model = str(row.get("worker_model") or "unavailable")
        status = "pass" if row.get("worker_usage_status") == "measured" else "unavailable"
        models.append({"provider": provider, "model": model, "status": status})
    unique = {json.dumps(item, sort_keys=True): item for item in models}
    return list(unique.values()) or [{"provider": "unavailable", "model": "unavailable", "status": "unavailable"}]


def live_manifest_blockers(rows: list[dict[str, Any]], tasks: list[str]) -> list[str]:
    blockers: list[str] = []
    if len(tasks) < 20:
        blockers.append(f"task_count:must_be_at_least_20:{len(tasks)}")
    if any(row.get("blocker_kind") == "infrastructure_blocker" for row in rows):
        blockers.append("infrastructure_blocker")
    if any(row.get("provider_error_kind") == "provider_overload" for row in rows):
        blockers.append("provider_overload")
    if any(row.get("benchmark_evidence_qualified") is False for row in rows):
        blockers.append("benchmark_evidence_qualified:false")
    if any(row.get("timed_out") is True for row in rows):
        blockers.append("timed_out")
    worker_blockers = worker_usage_blockers(rows)
    blockers.extend(worker_blockers)
    return ordered_unique(blockers)


def worker_usage_blockers(rows: list[dict[str, Any]]) -> list[str]:
    blockers: list[str] = []
    for row in rows:
        if (
            row.get("worker_usage_status") == "measured"
            and row.get("worker_usage_unit") == "tokens"
            and isinstance(row.get("worker_total_tokens"), int)
            and row.get("worker_total_tokens") > 0
            and row.get("worker_usage_source_path")
        ):
            continue
        task = row.get("task") or "unknown_task"
        reason = row.get("worker_usage_no_usage_reason") or "worker_token_usage_unavailable"
        blockers.append(f"worker_token_usage:{task}:{reason}")
    return blockers


def build_manifest(summary: dict[str, Any], summary_path: Path, *, operator: str, approval_reference: str, run_id: str | None = None) -> dict[str, Any]:
    rows = delegated_rows(summary)
    tasks = ordered_unique([row.get("task") for row in rows])
    family = claim_family(rows)
    blockers = live_manifest_blockers(rows, tasks)
    if blockers:
        raise SystemExit(f"live evidence manifest blocked: {','.join(blockers)}")
    strict_pass = bool(rows) and len(tasks) >= 20 and all(row.get("strict_accepted") is True for row in rows)
    quality_pass = bool(rows) and len(tasks) >= 20 and all(row.get("quality") == "pass" for row in rows)
    partial = len(tasks) < 20 or any(row.get("timed_out") is True for row in rows)
    return {
        "schema_version": LIVE_EVIDENCE_MANIFEST_SCHEMA_VERSION,
        "benchmark_run_id": run_id or summary_path.parent.name,
        "benchmark_run_kind": "live_provider_benchmark",
        "task_count": len(tasks),
        "task_slugs": tasks,
        "provider_models": provider_models(rows),
        "operator": operator,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "approval_status": "approved",
        "approval_reference": approval_reference,
        "claim_family": family,
        "strict_result_status": "pass" if strict_pass else "fail",
        "quality_result_status": "pass" if quality_pass else "fail",
        "worker_usage": worker_usage(rows, summary_path),
        "codex_usage": codex_usage(summary, summary_path),
        "total_workflow_savings": total_workflow_savings(summary, family),
        "artifact_paths": artifact_paths(summary_path),
        "production_green_path_enabled": False,
        "direct_mode_default": False,
        "provider_auth_status": "pass" if rows and not partial else "fail",
        "partial_run": partial,
    }


def artifact_paths(summary_path: Path) -> list[str]:
    root = summary_path.parent
    candidates = [summary_path, root / "summary.md", root / "task-plan.json", root / "dirty-status.txt", root / "dirty-diff.sha256"]
    return [str(path) for path in candidates if path.exists()]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest = build_manifest(
        read_json(args.summary),
        args.summary,
        operator=args.operator,
        approval_reference=args.approval_reference,
        run_id=args.run_id,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
