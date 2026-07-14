"""Preflight a canary artifact before allowing a live 20+ retry."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from .strict_contract_comparison import (
        WORKER_USAGE_EMPTY_SIDECAR_REASON,
        WORKER_USAGE_SIDECAR_NAMES,
        empty_sidecar_no_usage_reason,
        read_worker_usage_payload,
        usage_empty_sidecar_payload,
        usage_explicit_total_tokens,
        usage_has_non_token_evidence,
        usage_sidecar_has_measured_tokens,
    )
except ImportError:  # pragma: no cover - direct script execution
    from strict_contract_comparison import (
        WORKER_USAGE_EMPTY_SIDECAR_REASON,
        WORKER_USAGE_SIDECAR_NAMES,
        empty_sidecar_no_usage_reason,
        read_worker_usage_payload,
        usage_empty_sidecar_payload,
        usage_explicit_total_tokens,
        usage_has_non_token_evidence,
        usage_sidecar_has_measured_tokens,
    )


LIVE_RETRY_PREFLIGHT_SCHEMA_VERSION = "strict_contract_live_retry_preflight.v1"
DELEGATED_MODES = {"zcode_delegated", "zcode_direct_launcher"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path, help="Canary result JSON, summary.json, or report directory.")
    parser.add_argument("--json", action="store_true", help="Print JSON. This is the default output shape.")
    parser.add_argument("--allow-blocked-exit-zero", action="store_true", help="Return 0 even when retry is blocked.")
    return parser


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"artifact must be a JSON object: {path}")
    return payload


def canary_preflight(artifact: Path, *, cwd: Path | None = None) -> dict[str, Any]:
    cwd = cwd or Path.cwd()
    payload, summary_path = load_summary_payload(artifact, cwd=cwd)
    rows = delegated_rows(payload)
    row_results = [classify_worker_usage(row, summary_path=summary_path, cwd=cwd) for row in rows]
    blockers = preflight_blockers(payload, rows, row_results)
    worker_status = worker_usage_status(row_results)
    return {
        "schema_version": LIVE_RETRY_PREFLIGHT_SCHEMA_VERSION,
        "artifact": str(artifact),
        "summary_path": str(summary_path) if summary_path else None,
        "eligible_for_live_20_retry": not blockers,
        "blockers": blockers,
        "delegated_rows": len(rows),
        "provider_overload": any(row.get("provider_error_kind") == "provider_overload" for row in rows) or payload.get("provider_overload") is True,
        "provider_auth_failure": payload.get("provider_auth_failure") is True,
        "timed_out": any(row.get("timed_out") is True for row in rows) or payload.get("timed_out") is True,
        "worker_usage_status": worker_status,
        "worker_usage_rows": row_results,
        "production_green_path_enabled": payload.get("production_green_path_enabled") is True,
        "direct_mode_default": payload.get("direct_mode_default") is True,
    }


def load_summary_payload(artifact: Path, *, cwd: Path) -> tuple[dict[str, Any], Path | None]:
    path = artifact
    if path.is_dir():
        path = path / "summary.json"
    payload = read_json(path)
    if isinstance(payload.get("rows"), list):
        return payload, path
    summary = payload.get("canary_summary")
    if isinstance(summary, str) and summary.strip():
        summary_path = resolve_path(summary, path.parent, cwd)
        if summary_path is not None:
            return read_json(summary_path), summary_path
    return canary_result_as_summary(payload), path


def resolve_path(raw: str, base: Path, cwd: Path) -> Path | None:
    candidate = Path(raw)
    candidates = [candidate] if candidate.is_absolute() else [base / candidate, cwd / candidate]
    for item in candidates:
        if item.is_file():
            return item
    return None


def canary_result_as_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "rows": [
            {
                "task": payload.get("canary_task"),
                "mode": "zcode_direct_launcher" if payload.get("direct_mode") is True else "zcode_delegated",
                "provider_error_kind": "provider_overload" if payload.get("provider_overload") is True else None,
                "timed_out": payload.get("timed_out"),
                "worker_usage_status": payload.get("worker_usage_status"),
                "worker_usage_unit": payload.get("worker_usage_unit"),
                "worker_total_tokens": payload.get("worker_total_tokens"),
                "worker_usage_source_path": payload.get("worker_usage_source_path"),
                "worker_usage_no_usage_reason": payload.get("worker_usage_no_usage_reason"),
            }
        ]
    }


def delegated_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict) and row.get("mode") in DELEGATED_MODES]


def classify_worker_usage(row: dict[str, Any], *, summary_path: Path | None, cwd: Path) -> dict[str, Any]:
    sidecar = first_existing_sidecar(row, summary_path=summary_path, cwd=cwd)
    if sidecar is not None:
        payload = read_worker_usage_payload(sidecar)
        if isinstance(payload, dict):
            if usage_empty_sidecar_payload(payload):
                return worker_row_result(
                    row,
                    "unavailable",
                    empty_sidecar_no_usage_reason(payload),
                    sidecar,
                    None,
                    empty_sidecar=True,
                )
            if usage_sidecar_has_measured_tokens(payload):
                return worker_row_result(row, "measured_tokens", "measured_tokens", sidecar, usage_explicit_total_tokens(payload))
            non_token = usage_has_non_token_evidence(payload)
            if non_token is not None:
                return worker_row_result(row, "partial", f"worker_token_usage_unavailable_{non_token}_only", sidecar, None)

    total = row.get("worker_total_tokens")
    if (
        row.get("worker_usage_status") == "measured"
        and row.get("worker_usage_unit") == "tokens"
        and isinstance(total, int)
        and total > 0
        and row.get("worker_usage_source_path")
    ):
        return worker_row_result(row, "measured_tokens", "measured_tokens", sidecar, total)
    reason = row.get("worker_usage_no_usage_reason") or "worker_token_usage_unavailable"
    return worker_row_result(row, "unavailable", str(reason), sidecar, None)


def first_existing_sidecar(row: dict[str, Any], *, summary_path: Path | None, cwd: Path) -> Path | None:
    candidates: list[Path] = []
    source = row.get("worker_usage_source_path")
    if isinstance(source, str) and source.strip():
        for path in source_path_candidates(source.strip(), summary_path=summary_path, cwd=cwd):
            if path.name in WORKER_USAGE_SIDECAR_NAMES:
                candidates.append(path)
            if path.name == "zcode-run.json":
                candidates.extend(path.parent / name for name in WORKER_USAGE_SIDECAR_NAMES)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def source_path_candidates(raw: str, *, summary_path: Path | None, cwd: Path) -> list[Path]:
    path = Path(raw)
    if path.is_absolute():
        return [path]
    candidates = [cwd / path]
    if summary_path is not None:
        candidates.append(summary_path.parent / path)
    return candidates


def worker_row_result(
    row: dict[str, Any],
    status: str,
    classification: str,
    sidecar: Path | None,
    total_tokens: int | None,
    *,
    empty_sidecar: bool = False,
) -> dict[str, Any]:
    return {
        "task": row.get("task"),
        "mode": row.get("mode"),
        "status": status,
        "classification": classification,
        "empty_sidecar": empty_sidecar,
        "worker_usage_source_path": str(sidecar) if sidecar else row.get("worker_usage_source_path"),
        "worker_total_tokens": total_tokens,
    }


def preflight_blockers(
    payload: dict[str, Any],
    rows: list[dict[str, Any]],
    row_results: list[dict[str, Any]],
) -> list[str]:
    blockers: list[str] = []
    if not rows:
        blockers.append("canary_artifact:no_delegated_rows")
    if payload.get("provider_overload") is True or any(row.get("provider_error_kind") == "provider_overload" for row in rows):
        blockers.append("provider_overload")
    if payload.get("provider_auth_failure") is True:
        blockers.append("provider_auth_failure")
    if payload.get("timed_out") is True or any(row.get("timed_out") is True for row in rows):
        blockers.append("timed_out")
    if payload.get("production_green_path_enabled") is True:
        blockers.append("production_green_path_enabled:true")
    if payload.get("direct_mode_default") is True:
        blockers.append("direct_mode_default:true")
    for result in row_results:
        if result["status"] != "measured_tokens":
            task = result.get("task") or "unknown_task"
            blockers.append(f"worker_token_usage:{task}:{result['classification']}")
    return ordered_unique(blockers)


def worker_usage_status(row_results: list[dict[str, Any]]) -> str:
    if row_results and all(row["status"] == "measured_tokens" for row in row_results):
        return "measured_tokens"
    for row in row_results:
        if row.get("empty_sidecar") is True or row["classification"] == WORKER_USAGE_EMPTY_SIDECAR_REASON:
            return WORKER_USAGE_EMPTY_SIDECAR_REASON
    return "unavailable"


def ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = canary_preflight(args.artifact)
    print(json.dumps(payload, indent=2, sort_keys=True))
    if args.allow_blocked_exit_zero:
        return 0
    return 0 if payload["eligible_for_live_20_retry"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
