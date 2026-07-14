#!/usr/bin/env python3
"""Small harness for evaluating ZCode against Claude Code GLM workers.

The CLI deliberately starts with inspection and measurement. Direct ZCode GUI
automation should only be added after the local app exposes a stable control
surface such as a CLI, URL scheme, or documented IPC.
"""

from __future__ import annotations

import argparse
import json
import math
import plistlib
import statistics
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .codex_context_intake import add_context_intake_parser
    from .codex_usage import add_codex_usage_parsers
    from .direct_launcher import add_direct_launcher_parser
    from .duel_import import import_duel_results
    from .metrics import COMPARISON_METRIC_FIELDS, compute_usage_metrics, token_metric_metadata, unavailable_usage_metrics
    from .strict_contract_cli import add_strict_contract_parsers
    from .token_reduction import add_token_reduction_parsers
except ImportError:
    from codex_context_intake import add_context_intake_parser
    from codex_usage import add_codex_usage_parsers
    from direct_launcher import add_direct_launcher_parser
    from duel_import import import_duel_results
    from metrics import COMPARISON_METRIC_FIELDS, compute_usage_metrics, token_metric_metadata, unavailable_usage_metrics
    from strict_contract_cli import add_strict_contract_parsers
    from token_reduction import add_token_reduction_parsers

DEFAULT_APP_DIRS = (Path("/Applications"), Path.home() / "Applications")
DEFAULT_CLI_PATHS = (Path("/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs"),)
DEFAULT_LEDGER = Path("artifacts/evals/zcode-vs-claude.jsonl")
SUPPORTED_TOOLS = ("zcode", "claude-code-glm52")
CODEX_USAGE_METRIC_FIELDS = COMPARISON_METRIC_FIELDS
CODEX_EXEC_USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)
ACCEPTABLE_CODEX_REPAIR_SIZES = {"none", "small polish"}
ZCODE_SCOPE_VIOLATION_TYPES = {
    "outside_allowed_files",
    "forbidden_files_changed",
    "max_changed_files_exceeded",
}
STAT_FIELDS = (
    "duration_seconds",
    "manual_interventions",
    "tokens_total",
    "tokens_before",
    "tokens_after",
    "tokens_used",
    "quota_units",
    "quota_percent_before",
    "quota_percent_after",
    "quota_percent_used",
    "files_changed",
    "lines_added",
    "lines_deleted",
    "tests_passed",
    "tests_failed",
)
PERCENT_FIELDS = {"quota_percent_before", "quota_percent_after", "quota_percent_used"}
PROVIDER_META_FIELDS = (
    "supervisor_state",
    "provider_code",
    "provider_message",
    "provider_request_id",
    "provider_error_line",
    "provider_id",
    "provider_kind",
    "attempt_count",
    "attempts",
    "retry_count",
    "retry_delays_ms",
    "no_usage_reason",
    "quota_percent_status",
    "quota_percent_unavailable_reason",
    "source_run_dir",
    "source_result_path",
    "preview",
    "task_kind",
    "artifact_quality",
    "scope_safety",
    "validation_result",
    "codex_repair_size",
    "codex_direct_fallback_reason",
    "codex_token_usage_status",
    "codex_token_usage_reason",
)
PROVIDER_BOOL_FIELDS = (
    "provider_error",
    "retryable_provider_error",
    "partial_artifacts_possible",
    "safe_to_retry_later",
    "usage_available",
)


@dataclass(frozen=True)
class ZCodeAppInfo:
    path: str
    bundle_id: str | None
    version: str | None
    executable: str | None
    url_schemes: list[str]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def find_zcode_apps(search_dirs: tuple[Path, ...] = DEFAULT_APP_DIRS) -> list[Path]:
    apps: list[Path] = []
    for base in search_dirs:
        if not base.exists():
            continue
        for path in base.glob("*.app"):
            if "zcode" in path.name.lower():
                apps.append(path)
    return sorted(apps)


def read_app_info(app_path: Path) -> ZCodeAppInfo:
    info_plist = app_path / "Contents" / "Info.plist"
    data: dict[str, Any] = {}
    if info_plist.exists():
        with info_plist.open("rb") as handle:
            data = plistlib.load(handle)

    schemes: list[str] = []
    for entry in data.get("CFBundleURLTypes", []) or []:
        schemes.extend(entry.get("CFBundleURLSchemes", []) or [])

    return ZCodeAppInfo(
        path=str(app_path),
        bundle_id=data.get("CFBundleIdentifier"),
        version=data.get("CFBundleShortVersionString") or data.get("CFBundleVersion"),
        executable=data.get("CFBundleExecutable"),
        url_schemes=schemes,
    )


def infer_control_surface(apps: list[ZCodeAppInfo]) -> str:
    if not apps:
        return "install_required"
    if any(app.url_schemes for app in apps):
        return "url_scheme_candidate"
    return "gui_or_bundle_only"


def print_doctor(as_json: bool) -> int:
    apps = [read_app_info(path) for path in find_zcode_apps()]
    payload = {
        "status": "found" if apps else "not_found",
        "apps": [app.__dict__ for app in apps],
        "cli": find_cli_info(),
        "control_surface": infer_control_surface(apps),
    }
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"ZCode app: {payload['status']}")
        for app in apps:
            print(f"- path: {app.path}")
            print(f"  bundle_id: {app.bundle_id or 'unknown'}")
            print(f"  version: {app.version or 'unknown'}")
            print(f"  executable: {app.executable or 'unknown'}")
            print(f"  url_schemes: {', '.join(app.url_schemes) or 'none'}")
        cli = payload["cli"]
        print(f"cli: {cli['status']}")
        if cli.get("path"):
            print(f"  path: {cli['path']}")
            print(f"  version: {cli.get('version') or 'unknown'}")
        print(f"control_surface: {payload['control_surface']}")
    return 0


def find_cli_info() -> dict[str, Any]:
    for path in DEFAULT_CLI_PATHS:
        if not path.exists():
            continue
        version = None
        try:
            result = subprocess.run(
                ["node", str(path), "--version"],
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
            )
            if result.returncode == 0:
                version = result.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            version = None
        return {"status": "found", "path": str(path), "version": version}
    return {"status": "not_found"}


def load_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSONL: {exc}") from exc
    return records


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def non_negative_float(raw: str) -> float:
    value = float(raw)
    if value < 0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return value


def bounded_percent(raw: str) -> float:
    value = non_negative_float(raw)
    if value > 100:
        raise argparse.ArgumentTypeError("percent must be between 0 and 100")
    return value


def non_negative_int(raw: str) -> int:
    value = int(raw)
    if value < 0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return value


def parse_retry_delays_ms(raw: str) -> list[int]:
    text = raw.strip()
    if not text:
        return []
    if text.startswith("["):
        value = json.loads(text)
        if not isinstance(value, list):
            raise argparse.ArgumentTypeError("retry delays must be a JSON list or comma list")
        delays = value
    else:
        delays = [item.strip() for item in text.split(",") if item.strip()]
    parsed: list[int] = []
    for item in delays:
        delay = int(item)
        if delay < 0:
            raise argparse.ArgumentTypeError("retry delays must be non-negative")
        parsed.append(delay)
    return parsed


def stat_type(field: str):
    return bounded_percent if field in PERCENT_FIELDS else non_negative_float


def finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def normalize_codexbar_usage_snapshot(payload: Any) -> dict[str, Any]:
    rows = payload if isinstance(payload, list) else [payload]
    row = next((item for item in rows if isinstance(item, dict) and item.get("provider") == "zai"), None)
    row = row if row is not None else next((item for item in rows if isinstance(item, dict)), {})
    usage = row.get("usage", {}) if isinstance(row, dict) else {}
    windows: dict[str, dict[str, Any]] = {}
    quota_candidates: list[dict[str, Any]] = []
    if isinstance(usage, dict):
        for name in ("primary", "secondary", "tertiary"):
            window = usage.get(name)
            if not isinstance(window, dict):
                continue
            used_percent = window.get("usedPercent", window.get("used_percent"))
            normalized = {
                "name": name,
                "used_percent": used_percent if isinstance(used_percent, (int, float)) else None,
                "reset_description": window.get("resetDescription"),
                "resets_at": window.get("resetsAt"),
            }
            windows[name] = normalized
            if isinstance(normalized["used_percent"], (int, float)):
                quota_candidates.append(
                    {
                        "name": name,
                        "value": float(normalized["used_percent"]),
                        "line": f"{name}.usedPercent ({normalized['reset_description'] or 'quota window'})",
                    }
                )
    best = quota_candidates[0] if quota_candidates else None
    return {
        "source": "codexbar",
        "provider": row.get("provider") if isinstance(row, dict) else None,
        "windows": windows,
        "best": {
            "tokens_total": None,
            "quota_percent": best["value"] if best else None,
            "quota_percent_line": best["line"] if best else None,
        },
        "token_candidates": [],
        "quota_percent_candidates": quota_candidates,
    }


def normalize_zai_api_usage_snapshot(payload: Any) -> dict[str, Any]:
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    limits = data.get("limits", []) if isinstance(data, dict) else []
    windows: dict[str, dict[str, Any]] = {}
    quota_candidates: list[dict[str, Any]] = []
    raw_quota_candidates: list[dict[str, Any]] = []
    names_by_type = {"TOKENS_LIMIT": "primary", "TIME_LIMIT": "secondary"}
    iterable_limits = limits if isinstance(limits, list) else []
    for limit in iterable_limits:
        if not isinstance(limit, dict):
            continue
        limit_type = limit.get("type")
        name = names_by_type.get(limit_type)
        if name is None:
            continue
        raw_used_percent = finite_number(limit.get("percentage", limit.get("usedPercent", limit.get("used_percent"))))
        usage = finite_number(limit.get("usage"))
        remaining = finite_number(limit.get("remaining"))
        token_counts_available = usage is not None and remaining is not None
        authoritative = raw_used_percent is not None
        unavailable_reason = (
            "zai_limit_missing_percentage"
            if raw_used_percent is None
            else None
        )
        normalized = {
            "name": name,
            "type": limit_type,
            "used_percent": raw_used_percent,
            "raw_used_percent": raw_used_percent,
            "non_authoritative_used_percent": None,
            "authoritative": authoritative,
            "token_counts_available": token_counts_available,
            "quota_percent_unavailable_reason": unavailable_reason,
            "reset_description": "Tokens limit" if name == "primary" else "Time limit",
            "resets_at": limit.get("nextResetTime"),
            "usage": usage,
            "remaining": remaining,
        }
        windows[name] = normalized
        if name == "primary" and isinstance(normalized["used_percent"], (int, float)):
            quota_candidates.append(
                {
                    "name": name,
                    "value": float(normalized["used_percent"]),
                    "line": f"{limit_type}.percentage ({normalized['reset_description']})",
                }
            )
        if raw_used_percent is not None:
            raw_quota_candidates.append(
                {
                    "name": name,
                    "value": raw_used_percent,
                    "line": f"{limit_type}.percentage ({normalized['reset_description']})",
                    "authoritative": authoritative,
                    "unavailable_reason": unavailable_reason,
                }
            )
    best = quota_candidates[0] if quota_candidates else None
    raw_best = raw_quota_candidates[0] if raw_quota_candidates else None
    primary = windows.get("primary")
    return {
        "source": "zai-api",
        "provider": "zai",
        "plan": data.get("level") if isinstance(data, dict) else None,
        "windows": windows,
        "best": {
            "tokens_total": None,
            "quota_percent": best["value"] if best else None,
            "quota_percent_line": best["line"] if best else None,
            "raw_quota_percent": raw_best["value"] if raw_best else None,
            "quota_percent_authoritative": bool(best),
            "quota_percent_unavailable_reason": None
            if best
            else primary.get("quota_percent_unavailable_reason")
            if isinstance(primary, dict)
            else None,
        },
        "token_candidates": [],
        "quota_percent_candidates": quota_candidates,
        "quota_percent_raw_candidates": raw_quota_candidates,
    }


def unwrap_usage_snapshot(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        return normalize_codexbar_usage_snapshot(payload)
    if not isinstance(payload, dict):
        return {}
    if isinstance(payload.get("value"), dict):
        return payload["value"]
    if isinstance(payload.get("data"), dict) and isinstance(payload["data"].get("limits"), list):
        return normalize_zai_api_usage_snapshot(payload)
    if payload.get("source") == "codexbar" and isinstance(payload.get("windows"), dict):
        return payload
    if payload.get("source") == "zai-api" and isinstance(payload.get("windows"), dict):
        return payload
    if payload.get("provider") == "zai" and isinstance(payload.get("usage"), dict):
        return normalize_codexbar_usage_snapshot(payload)
    return payload


def load_usage_snapshot(path: Path) -> dict[str, Any]:
    return unwrap_usage_snapshot(read_json(path))


def best_usage_value(snapshot: dict[str, Any], key: str) -> float | None:
    best = snapshot.get("best")
    if isinstance(best, dict) and isinstance(best.get(key), (int, float)):
        return float(best[key])
    candidates_key = "token_candidates" if key == "tokens_total" else "quota_percent_candidates"
    candidates = snapshot.get(candidates_key, [])
    if isinstance(candidates, list):
        for candidate in candidates:
            if isinstance(candidate, dict) and isinstance(candidate.get("value"), (int, float)):
                return float(candidate["value"])
    return None


def snapshot_quota_unavailable_reason(snapshot: dict[str, Any]) -> str | None:
    best = snapshot.get("best")
    if isinstance(best, dict) and isinstance(best.get("quota_percent_unavailable_reason"), str):
        return best["quota_percent_unavailable_reason"]
    windows = snapshot.get("windows")
    if isinstance(windows, dict):
        primary = windows.get("primary")
        if isinstance(primary, dict) and isinstance(primary.get("quota_percent_unavailable_reason"), str):
            return primary["quota_percent_unavailable_reason"]
    if snapshot.get("ok") is False:
        reason = snapshot.get("error_type") or snapshot.get("reason") or snapshot.get("message")
        if isinstance(reason, str):
            return reason
    return None


def usage_snapshot_quota_unavailable_reason(args: argparse.Namespace) -> str:
    for path in (args.usage_after, args.usage_before):
        if path:
            reason = snapshot_quota_unavailable_reason(load_usage_snapshot(path))
            if reason:
                return reason
    return "quota_percent_unavailable"


def apply_usage_snapshot_defaults(args: argparse.Namespace, stats: dict[str, float | None]) -> None:
    if args.usage_before:
        before = load_usage_snapshot(args.usage_before)
        if stats["tokens_before"] is None:
            stats["tokens_before"] = best_usage_value(before, "tokens_total")
        stats["quota_percent_before"] = (
            stats["quota_percent_before"]
            if stats["quota_percent_before"] is not None
            else best_usage_value(before, "quota_percent")
        )
    if args.usage_after:
        after = load_usage_snapshot(args.usage_after)
        if stats["tokens_after"] is None:
            stats["tokens_after"] = best_usage_value(after, "tokens_total")
        stats["quota_percent_after"] = (
            stats["quota_percent_after"]
            if stats["quota_percent_after"] is not None
            else best_usage_value(after, "quota_percent")
        )


def derive_usage_stats(args: argparse.Namespace, stats: dict[str, float | None]) -> None:
    tokens_before = stats.get("tokens_before")
    tokens_after = stats.get("tokens_after")
    if stats.get("tokens_used") is None and tokens_before is not None and tokens_after is not None:
        if tokens_after >= tokens_before:
            stats["tokens_used"] = round(tokens_after - tokens_before, 4)

    quota_before = stats.get("quota_percent_before")
    quota_after = stats.get("quota_percent_after")
    if stats.get("quota_percent_used") is None and quota_before is not None and quota_after is not None:
        if args.quota_percent_direction == "remaining":
            delta = quota_before - quota_after
        else:
            delta = quota_after - quota_before
        if delta >= 0:
            stats["quota_percent_used"] = round(delta, 4)


def init_ledger(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    print(str(path))
    return 0


def append_result(args: argparse.Namespace) -> int:
    path = args.path
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "recorded_at": utc_now(),
        "run_id": args.run_id,
        "tool": args.tool,
        "task_id": args.task_id,
        "task_name": args.task_name,
        "status": args.status,
        "validation": args.validation,
        "notes": args.notes,
    }
    stats = {field: getattr(args, field) for field in STAT_FIELDS}
    apply_usage_snapshot_defaults(args, stats)
    derive_usage_stats(args, stats)
    if (
        args.usage_before
        or args.usage_after
        or stats.get("quota_percent_before") is not None
        or stats.get("quota_percent_after") is not None
    ):
        record["quota_percent_direction"] = args.quota_percent_direction
    quota_percent_status = args.quota_percent_status
    quota_percent_unavailable_reason = args.quota_percent_unavailable_reason
    if quota_percent_status is None:
        if stats.get("quota_percent_used") is not None:
            quota_percent_status = "measured"
        elif args.usage_before or args.usage_after:
            quota_percent_status = "unavailable"
    if quota_percent_status == "unavailable" and quota_percent_unavailable_reason is None:
        quota_percent_unavailable_reason = usage_snapshot_quota_unavailable_reason(args)
    if quota_percent_status is not None:
        record["quota_percent_status"] = quota_percent_status
    if quota_percent_unavailable_reason is not None:
        record["quota_percent_unavailable_reason"] = quota_percent_unavailable_reason
    if args.usage_available is False and args.no_usage_reason is None:
        record["no_usage_reason"] = "usage_marked_unavailable"
    for field in STAT_FIELDS:
        value = stats[field]
        if value is not None:
            record[field] = value
    for field in PROVIDER_META_FIELDS:
        value = getattr(args, field)
        if value is not None:
            record[field] = value
    for field in PROVIDER_BOOL_FIELDS:
        value = getattr(args, field)
        if value is not None:
            record[field] = value
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0


def build_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_tool: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_tool.setdefault(record.get("tool", "unknown"), []).append(record)

    return {
        "records": len(records),
        "tools": {
            tool: summarize_tool(tool_records)
            for tool, tool_records in sorted(by_tool.items())
        },
    }


def summarize_tool(records: list[dict[str, Any]]) -> dict[str, Any]:
    passed = sum(1 for record in records if record.get("status") == "pass")
    artifact_quality_counts = count_values(records, "artifact_quality")
    repair_size_counts = count_values(records, "codex_repair_size")
    result: dict[str, Any] = {
        "runs": len(records),
        "pass_rate": round(passed / len(records), 3),
        "provider_errors": sum(1 for record in records if record.get("provider_error") is True),
        "retryable_provider_errors": sum(1 for record in records if record.get("safe_to_retry_later") is True),
        "partial_successes": sum(1 for record in records if record.get("supervisor_state") == "partial_success"),
        "run_timeouts": sum(1 for record in records if record.get("supervisor_state") == "run_timeout"),
        "codex_direct_fallbacks": sum(1 for record in records if record.get("codex_direct_fallback_reason")),
    }
    if artifact_quality_counts:
        result["artifact_quality"] = artifact_quality_counts
    if repair_size_counts:
        result["codex_repair_size"] = repair_size_counts
    for field in STAT_FIELDS:
        values = [
            record[field]
            for record in records
            if isinstance(record.get(field), (int, float))
        ]
        if values:
            result[f"avg_{field}"] = round(statistics.fmean(values), 2)
            result[f"total_{field}"] = round(sum(values), 2)
    return result


def count_values(records: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        value = record.get(field)
        if isinstance(value, str) and value:
            counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def summarize(path: Path) -> int:
    records = load_records(path)
    if not records:
        print("No records.")
        return 0
    print(json.dumps(build_summary(records), indent=2, sort_keys=True))
    return 0


def show_log(path: Path, limit: int, as_json: bool) -> int:
    records = load_records(path)
    selected = records[-limit:] if limit else records
    if as_json:
        print(json.dumps(selected, indent=2, sort_keys=True))
        return 0
    for record in selected:
        tokens = record.get("tokens_used", record.get("tokens_total", "unknown"))
        quota = record.get("quota_percent_used", "unknown")
        print(
            f"{record.get('recorded_at', 'unknown')} "
            f"{record.get('tool', 'unknown')} "
            f"{record.get('task_id', 'unknown')} "
            f"status={record.get('status', 'unknown')} "
            f"tokens_used={tokens} quota_percent_used={quota}"
        )
    return 0


def codex_usage_field_value(path: Path, line_no: int, field: str, value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{path}:{line_no}: invalid {field} in turn.completed usage")
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"{path}:{line_no}: invalid {field} in turn.completed usage")
        return value
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    raise ValueError(f"{path}:{line_no}: invalid {field} in turn.completed usage")


def load_codex_exec_usage(path: Path) -> dict[str, int]:
    usage = {field: 0 for field in CODEX_EXEC_USAGE_FIELDS}
    found_usage = False
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid Codex JSONL event: {exc}") from exc
            if event.get("type") != "turn.completed":
                continue
            raw_usage = event.get("usage")
            if not isinstance(raw_usage, dict):
                continue
            missing = [field for field in CODEX_EXEC_USAGE_FIELDS if field not in raw_usage]
            if missing:
                raise ValueError(f"{path}:{line_no}: incomplete turn.completed usage ({', '.join(missing)})")
            found_usage = True
            for field in CODEX_EXEC_USAGE_FIELDS:
                usage[field] += codex_usage_field_value(path, line_no, field, raw_usage.get(field))
    if not found_usage:
        raise ValueError(f"{path}: no turn.completed usage found")
    return usage


def first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def extract_zcode_acceptance(path: Path) -> dict[str, Any]:
    payload = read_json(path)
    attempts = payload.get("attempt_results")
    last_attempt = attempts[-1] if isinstance(attempts, list) and attempts else {}
    audit = payload.get("audit") if isinstance(payload.get("audit"), dict) else {}
    if not audit and any(key in payload for key in ("artifact_quality", "scope_safety", "validation_result")):
        audit = payload
    summary = payload.get("run_result_summary") if isinstance(payload.get("run_result_summary"), dict) else {}
    validation = audit.get("validation") if isinstance(audit.get("validation"), dict) else {}
    usage = payload.get("usage_accounting") if isinstance(payload.get("usage_accounting"), dict) else {}
    violations = audit.get("violations") if isinstance(audit.get("violations"), list) else []
    violation_types = sorted(
        item.get("type")
        for item in violations
        if isinstance(item, dict) and isinstance(item.get("type"), str)
    )
    supervisor_state = first_present(
        payload.get("supervisor_state"),
        last_attempt.get("supervisor_state"),
        summary.get("supervisor_state"),
    )
    status = first_present(payload.get("status"), supervisor_state)
    terminal_timeout_states = {"run_timeout", "aborted", "running"}
    return {
        "run_json": str(path),
        "ok": payload.get("ok"),
        "status": status,
        "supervisor_state": supervisor_state,
        "timed_out": bool(payload.get("timed_out")) or status in terminal_timeout_states,
        "audit_ok": first_present(payload.get("audit_ok"), last_attempt.get("audit_ok"), audit.get("ok"), summary.get("audit_ok")),
        "validation_ok": first_present(
            payload.get("validation_ok"),
            last_attempt.get("validation_ok"),
            validation.get("ok"),
            summary.get("validation_ok"),
        ),
        "changed_count": first_present(
            payload.get("changed_count"),
            payload.get("zcode_changed_count"),
            last_attempt.get("changed_count"),
            audit.get("changed_count"),
            summary.get("changed_count"),
        ),
        "artifact_quality": first_present(
            payload.get("artifact_quality"),
            last_attempt.get("artifact_quality"),
            audit.get("artifact_quality"),
            summary.get("artifact_quality"),
        ),
        "scope_safety": first_present(
            payload.get("scope_safety"),
            last_attempt.get("scope_safety"),
            audit.get("scope_safety"),
            summary.get("scope_safety"),
        ),
        "validation_result": first_present(
            payload.get("validation_result"),
            last_attempt.get("validation_result"),
            audit.get("validation_result"),
            summary.get("validation_result"),
        ),
        "codex_repair_size": first_present(
            payload.get("codex_repair_size"),
            payload.get("codex_repair_size_recommendation"),
            last_attempt.get("codex_repair_size"),
            last_attempt.get("codex_repair_size_recommendation"),
            audit.get("codex_repair_size"),
            audit.get("codex_repair_size_recommendation"),
            summary.get("codex_repair_size"),
            summary.get("codex_repair_size_recommendation"),
        ),
        "scope_violation_types": [item for item in violation_types if item in ZCODE_SCOPE_VIOLATION_TYPES],
        "codex_review_required": first_present(audit.get("codex_review_required"), summary.get("codex_review_required")),
        "usage_available": first_present(payload.get("usage_available"), usage.get("usage_available")),
        "no_usage_reason": first_present(payload.get("no_usage_reason"), usage.get("no_usage_reason")),
    }


def codex_usage_metrics(usage: dict[str, int]) -> dict[str, int]:
    return compute_usage_metrics(
        input_tokens=usage["input_tokens"],
        cached_input_tokens=usage["cached_input_tokens"],
        output_tokens=usage["output_tokens"],
        reasoning_output_tokens=usage["reasoning_output_tokens"],
    )


def unavailable_codex_usage_metrics() -> dict[str, None]:
    return unavailable_usage_metrics()


def metric_value(row: dict[str, Any], field: str) -> int | float | None:
    metrics = row.get("metrics")
    if not isinstance(metrics, dict):
        return None
    value = metrics.get(field)
    return value if isinstance(value, (int, float)) else None


def build_codex_run_comparison(
    runs: list[dict[str, Any]],
    *,
    baseline_label: str,
    reduction_metric: str = "total_in_out",
) -> dict[str, Any]:
    if not runs:
        raise ValueError("at least one run is required")
    baseline = next((run for run in runs if run["label"] == baseline_label), None)
    if baseline is None:
        raise ValueError(f"baseline label not found: {baseline_label}")
    baseline_total = metric_value(baseline, reduction_metric)
    if baseline_total is not None and baseline_total <= 0:
        raise ValueError(f"baseline {reduction_metric} must be positive")
    baseline_duration = baseline.get("duration_seconds")

    rows = []
    for run in runs:
        total = metric_value(run, reduction_metric)
        reduction = None
        if baseline_total is not None and total is not None:
            reduction = round((1 - (total / baseline_total)) * 100, 2)
        duration = run.get("duration_seconds")
        duration_ratio = None
        if isinstance(duration, (int, float)) and isinstance(baseline_duration, (int, float)) and baseline_duration > 0:
            duration_ratio = round(duration / baseline_duration, 3)
        row = {
            **run,
            "reduction_vs_baseline_percent": reduction,
            "duration_ratio_vs_baseline": duration_ratio,
        }
        rows.append(row)
    return {
        **token_metric_metadata(),
        "baseline_label": baseline_label,
        "reduction_metric": reduction_metric,
        "baseline_metric_value": baseline_total,
        "baseline_total_in_out": metric_value(baseline, "total_in_out"),
        "baseline_duration_seconds": baseline_duration,
        "rows": rows,
    }


def parse_labeled_path(raw: str, *, option: str) -> tuple[str, Path]:
    if "=" not in raw:
        raise argparse.ArgumentTypeError(f"{option} must look like label=/path/to/file")
    label, path = raw.split("=", 1)
    label = label.strip()
    if not label:
        raise argparse.ArgumentTypeError(f"{option} label cannot be empty")
    return label, Path(path)


def parse_quality(raw: str) -> tuple[str, str]:
    label, value = parse_labeled_path(raw, option="--quality")
    quality = str(value)
    if quality not in {"pass", "fail", "partial", "blocked", "unknown"}:
        raise argparse.ArgumentTypeError("--quality value must be pass, fail, partial, blocked, or unknown")
    return label, quality


def parse_labeled_float(raw: str, *, option: str) -> tuple[str, float]:
    label, value_path = parse_labeled_path(raw, option=option)
    try:
        value = float(str(value_path))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{option} value must be a number") from exc
    if value < 0:
        raise argparse.ArgumentTypeError(f"{option} value must be non-negative")
    return label, value


def parse_labeled_bool(raw: str, *, option: str) -> tuple[str, bool]:
    label, value_path = parse_labeled_path(raw, option=option)
    value = str(value_path).strip().lower()
    if value in {"1", "true", "yes", "y"}:
        return label, True
    if value in {"0", "false", "no", "n"}:
        return label, False
    raise argparse.ArgumentTypeError(f"{option} value must be true or false")


def row_usage_unavailable(row: dict[str, Any]) -> bool:
    return row.get("usage_status") != "measured"


def row_timed_out(row: dict[str, Any]) -> bool:
    if row.get("timed_out") is True:
        return True
    zcode = row.get("zcode_acceptance") if isinstance(row.get("zcode_acceptance"), dict) else {}
    return bool(zcode.get("timed_out"))


def usage_unavailable_violation(row: dict[str, Any]) -> str:
    reason = row.get("no_usage_reason") or "no turn.completed usage found"
    return f"{row['label']}: usage_unavailable ({reason})"


def zcode_acceptance_violations(label: str, zcode: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    if not zcode:
        return [f"{label}: ZCode run JSON missing"]
    if zcode.get("audit_ok") is not True:
        violations.append(f"{label}: ZCode audit did not pass")
    if zcode.get("validation_ok") is not True:
        violations.append(f"{label}: ZCode validation did not pass")
    changed_count = zcode.get("changed_count")
    if not isinstance(changed_count, (int, float)) or changed_count <= 0:
        violations.append(f"{label}: ZCode changed_count is not positive")
    if zcode.get("artifact_quality") != "pass":
        violations.append(f"{label}: artifact_quality is {zcode.get('artifact_quality')}, expected pass")
    if zcode.get("scope_safety") != "pass":
        violations.append(f"{label}: scope_safety is {zcode.get('scope_safety')}, expected pass")
    if zcode.get("validation_result") != "pass":
        violations.append(f"{label}: validation_result is {zcode.get('validation_result')}, expected pass")
    repair_size = zcode.get("codex_repair_size")
    if repair_size not in ACCEPTABLE_CODEX_REPAIR_SIZES:
        violations.append(
            f"{label}: codex_repair_size is {repair_size}, expected none or small polish"
        )
    scope_violations = zcode.get("scope_violation_types")
    if scope_violations:
        violations.append(f"{label}: scope violations present ({', '.join(scope_violations)})")
    return violations


def markdown_zcode_artifact_acceptance(payload: dict[str, Any]) -> str:
    acceptance = payload["acceptance"]
    lines = [
        "# ZCode Artifact Acceptance",
        "",
        f"- label: `{payload['label']}`",
        f"- ok: `{payload['ok']}`",
        f"- run_json: `{acceptance.get('run_json')}`",
        "",
        "| artifact_quality | scope_safety | validation_result | codex_repair_size | changed_count | timed_out |",
        "| --- | --- | --- | --- | ---: | --- |",
        "| {artifact} | {scope} | {validation} | {repair} | {changed} | {timed_out} |".format(
            artifact=acceptance.get("artifact_quality"),
            scope=acceptance.get("scope_safety"),
            validation=acceptance.get("validation_result"),
            repair=acceptance.get("codex_repair_size"),
            changed=acceptance.get("changed_count", ""),
            timed_out=acceptance.get("timed_out"),
        ),
        "",
    ]
    if payload["violations"]:
        lines.extend(["## Violations", ""])
        lines.extend(f"- {item}" for item in payload["violations"])
        lines.append("")
    return "\n".join(lines)


def accept_zcode_artifact(args: argparse.Namespace) -> int:
    acceptance = extract_zcode_acceptance(args.zcode_run_json)
    violations = zcode_acceptance_violations(args.label, acceptance)
    if acceptance.get("timed_out") is True:
        violations.append(f"{args.label}: timed out")
    payload = {
        "ok": not violations,
        "label": args.label,
        "acceptance": acceptance,
        "violations": violations,
        "required_acceptance": {
            "artifact_quality": "pass",
            "scope_safety": "pass",
            "validation_result": "pass",
            "codex_repair_size": sorted(ACCEPTABLE_CODEX_REPAIR_SIZES),
            "changed_count": "positive",
            "timed_out": False,
        },
    }
    if args.markdown_out:
        args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_out.write_text(markdown_zcode_artifact_acceptance(payload), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["ok"] else 1


def markdown_zcode_acceptance_comparison(payload: dict[str, Any]) -> str:
    baseline = payload["baseline"]
    candidate = payload["candidate"]
    token = payload["token_comparison"]
    lines = [
        "# Direct Codex vs ZCode Thin Acceptance",
        "",
        f"- ok: `{payload['ok']}`",
        f"- primary_metric: `{payload['primary_metric']}`",
        f"- primary_metric_formula: `{payload['primary_metric_formula']}`",
        f"- reduction_metric: `{token['metric']}`",
        f"- token_saving_claim_status: `{token['claim_status']}`",
        "",
        "| path | quality | codex_token_usage_status | metric_value | artifact_quality | scope_safety | validation_result | codex_repair_size |",
        "| --- | --- | --- | ---: | --- | --- | --- | --- |",
        "| {label} | {quality} | {status} | {metric} |  |  |  |  |".format(
            label=baseline["label"],
            quality=baseline["quality"],
            status=baseline["usage_status"],
            metric=format_optional_number(baseline["metrics"].get(token["metric"])),
        ),
        "| {label} | pass | {status} | {metric} | {artifact} | {scope} | {validation} | {repair} |".format(
            label=candidate["label"],
            status=candidate["codex_token_usage_status"],
            metric=format_optional_number(token.get("candidate_metric_value")),
            artifact=candidate["zcode_acceptance"].get("artifact_quality"),
            scope=candidate["zcode_acceptance"].get("scope_safety"),
            validation=candidate["zcode_acceptance"].get("validation_result"),
            repair=candidate["zcode_acceptance"].get("codex_repair_size"),
        ),
        "",
        f"- reduction_vs_direct_percent: `{token.get('reduction_vs_direct_percent')}`",
        f"- delegated_artifact_accepted: `{candidate['delegated_artifact_accepted']}`",
        "",
    ]
    if payload["violations"]:
        lines.extend(["## Violations", ""])
        lines.extend(f"- {item}" for item in payload["violations"])
        lines.append("")
    if token.get("caveat"):
        lines.extend(["## Caveat", "", token["caveat"], ""])
    return "\n".join(lines)


def compare_zcode_acceptance(args: argparse.Namespace) -> int:
    try:
        baseline_metrics = codex_usage_metrics(load_codex_exec_usage(args.direct_run_events))
        baseline_usage_status = "measured"
        baseline_no_usage_reason = None
    except ValueError as exc:
        baseline_metrics = unavailable_codex_usage_metrics()
        baseline_usage_status = "unavailable"
        baseline_no_usage_reason = str(exc)

    acceptance = extract_zcode_acceptance(args.zcode_run_json)
    acceptance_violations = zcode_acceptance_violations(args.candidate_label, acceptance)
    if acceptance.get("timed_out") is True:
        acceptance_violations.append(f"{args.candidate_label}: timed out")

    direct_value = baseline_metrics.get(args.reduction_metric)
    candidate_usage = build_candidate_codex_usage(args, direct_value)
    candidate_value = candidate_usage["metric_value"]
    reduction = candidate_usage["reduction_vs_direct_percent"]
    token_violations = list(candidate_usage["violations"])

    if (
        args.min_estimated_reduction_percent is not None
        and reduction is not None
        and reduction < args.min_estimated_reduction_percent
    ):
        token_violations.append(
            f"{args.candidate_label}: estimated reduction {reduction:.2f}% < "
            f"{args.min_estimated_reduction_percent:.2f}%"
        )

    violations: list[str] = []
    if args.direct_quality != "pass":
        violations.append(f"{args.direct_label}: quality is {args.direct_quality}, expected pass")
    if baseline_usage_status != "measured":
        violations.append(f"{args.direct_label}: usage_unavailable ({baseline_no_usage_reason})")
    violations.extend(acceptance_violations)
    violations.extend(token_violations)

    claim_status = "unavailable"
    if (
        candidate_usage["status"] in {"estimated", "measured"}
        and candidate_value is not None
        and baseline_usage_status == "measured"
        and not acceptance_violations
    ):
        claim_status = candidate_usage["status"]

    payload = {
        **token_metric_metadata(),
        "ok": not violations,
        "baseline": {
            "label": args.direct_label,
            "events": str(args.direct_run_events),
            "quality": args.direct_quality,
            "usage_status": baseline_usage_status,
            "no_usage_reason": baseline_no_usage_reason,
            "metrics": baseline_metrics,
        },
        "candidate": {
            "label": args.candidate_label,
            "zcode_run_json": str(args.zcode_run_json),
            "zcode_acceptance": acceptance,
            "delegated_artifact_accepted": not acceptance_violations,
            "codex_token_usage_status": candidate_usage["status"],
            "codex_token_usage_reason": candidate_usage["reason"],
            "codex_token_usage_scope": candidate_usage["scope"],
            "events": candidate_usage["events"],
            "metrics": candidate_usage["metrics"],
            "no_usage_reason": candidate_usage["no_usage_reason"],
        },
        "token_comparison": {
            "metric": args.reduction_metric,
            "baseline_metric_value": direct_value,
            "candidate_metric_value": candidate_value,
            "candidate_metric_status": candidate_usage["status"],
            "reduction_vs_direct_percent": reduction,
            "estimated_reduction_vs_direct_percent": (
                reduction if candidate_usage["status"] == "estimated" else None
            ),
            "measured_reduction_vs_direct_percent": (
                reduction if candidate_usage["status"] == "measured" else None
            ),
            "min_reduction_percent": args.min_estimated_reduction_percent,
            "min_estimated_reduction_percent": args.min_estimated_reduction_percent,
            "claim_status": claim_status,
            "caveat": zcode_acceptance_token_caveat(claim_status),
        },
        "violations": violations,
    }
    if args.markdown_out:
        args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_out.write_text(markdown_zcode_acceptance_comparison(payload), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["ok"] else 1


def compute_reduction(candidate_value: float, direct_value: Any) -> float | None:
    if isinstance(direct_value, (int, float)) and direct_value > 0:
        return round((1 - (candidate_value / direct_value)) * 100, 2)
    return None


def build_candidate_codex_usage(args: argparse.Namespace, direct_value: Any) -> dict[str, Any]:
    requested_status = args.candidate_codex_token_usage_status
    metrics = unavailable_codex_usage_metrics()
    value: float | None = None
    reduction: float | None = None
    violations: list[str] = []
    no_usage_reason = None
    reason = args.candidate_codex_token_usage_reason
    events = str(args.candidate_run_events) if args.candidate_run_events else None
    scope = "codex_launcher_orchestration"

    if requested_status == "measured":
        if args.candidate_run_events is None:
            no_usage_reason = "--candidate-run-events is required for measured candidate usage"
            violations.append(f"{args.candidate_label}: usage_unavailable ({no_usage_reason})")
            return {
                "status": "unavailable",
                "reason": reason,
                "scope": scope,
                "events": events,
                "metrics": metrics,
                "metric_value": value,
                "reduction_vs_direct_percent": reduction,
                "violations": violations,
                "no_usage_reason": no_usage_reason,
            }
        try:
            metrics = codex_usage_metrics(load_codex_exec_usage(args.candidate_run_events))
            raw_value = metrics.get(args.reduction_metric)
            if isinstance(raw_value, (int, float)):
                value = float(raw_value)
                reduction = compute_reduction(value, direct_value)
        except ValueError as exc:
            no_usage_reason = str(exc)
            violations.append(f"{args.candidate_label}: usage_unavailable ({no_usage_reason})")
            requested_status = "unavailable"
    elif requested_status == "estimated":
        if args.candidate_codex_token_estimate is None:
            violations.append(f"{args.candidate_label}: token estimate missing")
        else:
            value = float(args.candidate_codex_token_estimate)
            reduction = compute_reduction(value, direct_value)
    elif args.require_token_estimate or args.min_estimated_reduction_percent is not None:
        violations.append(f"{args.candidate_label}: token usage estimate unavailable")

    return {
        "status": requested_status,
        "reason": reason,
        "scope": scope,
        "events": events,
        "metrics": metrics,
        "metric_value": value,
        "reduction_vs_direct_percent": reduction,
        "violations": violations,
        "no_usage_reason": no_usage_reason,
    }


def zcode_acceptance_token_caveat(claim_status: str) -> str | None:
    if claim_status == "estimated":
        return (
            "Candidate token value is an estimate for the deterministic local acceptance command; "
            "it is not measured Codex exec usage."
        )
    if claim_status == "measured":
        return (
            "Candidate token value is measured only from Codex launcher/orchestration event JSONL; "
            "delegated worker or ZCode task-level usage is excluded and must be reported separately."
        )
    return None


def build_codex_comparison_gate(
    payload: dict[str, Any],
    *,
    candidate_labels: list[str],
    min_reduction_percent: float | None,
    require_quality_pass: bool,
    require_zcode_acceptance: bool,
    max_duration_ratio: float | None,
) -> dict[str, Any]:
    rows_by_label = {row["label"]: row for row in payload["rows"]}
    baseline = rows_by_label.get(payload["baseline_label"])
    candidates = candidate_labels or [
        row["label"] for row in payload["rows"] if row["label"] != payload["baseline_label"]
    ]
    violations: list[str] = []
    if baseline is not None and row_usage_unavailable(baseline):
        violations.append(usage_unavailable_violation(baseline))
    for label in candidates:
        row = rows_by_label.get(label)
        if row is None:
            violations.append(f"{label}: candidate row missing")
            continue
        if row_usage_unavailable(row):
            violations.append(usage_unavailable_violation(row))
        if row_timed_out(row):
            violations.append(f"{label}: timed out")
        reduction = row.get("reduction_vs_baseline_percent")
        if min_reduction_percent is not None and not isinstance(reduction, (int, float)):
            violations.append(f"{label}: reduction unavailable")
        elif min_reduction_percent is not None and reduction < min_reduction_percent:
            violations.append(
                f"{label}: reduction {reduction:.2f}% < {min_reduction_percent:.2f}%"
            )
        if require_quality_pass and row.get("quality") != "pass":
            violations.append(f"{label}: quality is {row.get('quality')}, expected pass")
        if require_zcode_acceptance:
            zcode = row.get("zcode_acceptance") if isinstance(row.get("zcode_acceptance"), dict) else {}
            violations.extend(zcode_acceptance_violations(label, zcode))
        if max_duration_ratio is not None:
            duration_ratio = row.get("duration_ratio_vs_baseline")
            if not isinstance(duration_ratio, (int, float)):
                violations.append(f"{label}: duration ratio unavailable")
            elif duration_ratio > max_duration_ratio:
                violations.append(f"{label}: duration ratio {duration_ratio:.3f} > {max_duration_ratio:.3f}")
    return {
        "ok": not violations,
        "candidate_labels": candidates,
        "min_reduction_percent": min_reduction_percent,
        "require_quality_pass": require_quality_pass,
        "require_zcode_acceptance": require_zcode_acceptance,
        "max_duration_ratio": max_duration_ratio,
        "violations": violations,
    }


def markdown_codex_run_comparison(payload: dict[str, Any]) -> str:
    lines = [
        "# Codex Run Token Comparison",
        "",
        f"- baseline: `{payload['baseline_label']}`",
        f"- primary_metric: `{payload['primary_metric']}`",
        f"- primary_metric_formula: `{payload['primary_metric_formula']}`",
        f"- reduction_metric: `{payload.get('reduction_metric', 'total_in_out')}`",
        f"- baseline_metric_value: `{payload.get('baseline_metric_value', payload['baseline_total_in_out'])}`",
        f"- baseline_duration_seconds: `{payload.get('baseline_duration_seconds') or ''}`",
        "",
        "| mode | quality | duration_s | time_ratio | input | cached | output | reasoning | total_in_out | uncached_plus_reasoning | effective_codex_work | reduction_vs_baseline | artifact | scope | validation | repair | changed |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | ---: |",
    ]
    for row in payload["rows"]:
        metrics = row["metrics"]
        zcode = row.get("zcode_acceptance") or {}
        reduction = row.get("reduction_vs_baseline_percent")
        lines.append(
            "| {label} | {quality} | {duration} | {time_ratio} | {input} | {cached} | {output} | {reasoning} | {total} | {marginal} | {effective} | {reduction} | {artifact} | {scope} | {validation} | {repair} | {changed} |".format(
                label=row["label"],
                quality=row["quality"],
                duration="" if row.get("duration_seconds") is None else row["duration_seconds"],
                time_ratio="" if row.get("duration_ratio_vs_baseline") is None else row["duration_ratio_vs_baseline"],
                input=format_optional_number(metrics["input_tokens"]),
                cached=format_optional_number(metrics["cached_input_tokens"]),
                output=format_optional_number(metrics["output_tokens"]),
                reasoning=format_optional_number(metrics["reasoning_output_tokens"]),
                total=format_optional_number(metrics["total_in_out"]),
                marginal=format_optional_number(metrics["uncached_plus_reasoning"]),
                effective=format_optional_number(metrics["effective_codex_work"]),
                reduction="" if reduction is None else f"{reduction:.2f}%",
                artifact=zcode.get("artifact_quality", ""),
                scope=zcode.get("scope_safety", ""),
                validation=zcode.get("validation_result", ""),
                repair=zcode.get("codex_repair_size", ""),
                changed=zcode.get("changed_count", ""),
            )
        )
    return "\n".join(lines) + "\n"


def format_optional_number(value: Any) -> str:
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return ""


def compare_codex_runs(args: argparse.Namespace) -> int:
    quality_by_label = dict(args.quality or [])
    zcode_json_by_label = dict(args.zcode_run_json or [])
    duration_by_label = dict(args.duration or [])
    timeout_by_label = dict(args.timeout or [])
    runs: list[dict[str, Any]] = []
    for label, events_path in args.run:
        try:
            metrics = codex_usage_metrics(load_codex_exec_usage(events_path))
            usage_status = "measured"
            no_usage_reason = None
        except ValueError as exc:
            metrics = unavailable_codex_usage_metrics()
            usage_status = "unavailable"
            no_usage_reason = str(exc)
        row = {
            "label": label,
            "events": str(events_path),
            "quality": quality_by_label.get(label, "unknown"),
            "usage_status": usage_status,
            "no_usage_reason": no_usage_reason,
            "metrics": metrics,
        }
        if label in duration_by_label:
            row["duration_seconds"] = duration_by_label[label]
        if label in timeout_by_label:
            row["timed_out"] = timeout_by_label[label]
        if label in zcode_json_by_label:
            zcode_acceptance = extract_zcode_acceptance(zcode_json_by_label[label])
            row["zcode_acceptance"] = zcode_acceptance
            row["timed_out"] = bool(row.get("timed_out")) or zcode_acceptance["timed_out"]
        runs.append(row)
    payload = build_codex_run_comparison(
        runs,
        baseline_label=args.baseline_label,
        reduction_metric=args.reduction_metric,
    )
    if (
        args.candidate_label
        or args.min_reduction_percent is not None
        or args.require_quality_pass
        or args.require_zcode_acceptance
        or args.max_duration_ratio is not None
    ):
        payload["gate"] = build_codex_comparison_gate(
            payload,
            candidate_labels=args.candidate_label,
            min_reduction_percent=args.min_reduction_percent,
            require_quality_pass=args.require_quality_pass,
            require_zcode_acceptance=args.require_zcode_acceptance,
            max_duration_ratio=args.max_duration_ratio,
        )
    if args.markdown_out:
        args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_out.write_text(markdown_codex_run_comparison(payload), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    if isinstance(payload.get("gate"), dict) and not payload["gate"]["ok"]:
        return 1
    if any(row_usage_unavailable(row) for row in payload["rows"]):
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate ZCode vs Claude Code GLM workers.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Inspect whether ZCode.app is installed.")
    doctor.add_argument("--json", action="store_true", help="Print machine-readable app info.")
    doctor.set_defaults(func=lambda args: print_doctor(args.json))

    init = subparsers.add_parser("init-ledger", help="Create the JSONL evaluation ledger.")
    init.add_argument("--path", type=Path, default=DEFAULT_LEDGER)
    init.set_defaults(func=lambda args: init_ledger(args.path))

    append = subparsers.add_parser("append-result", help="Append one benchmark result.")
    append.add_argument("--path", type=Path, default=DEFAULT_LEDGER)
    append.add_argument("--run-id", required=True)
    append.add_argument("--tool", choices=SUPPORTED_TOOLS, required=True)
    append.add_argument("--task-id", required=True)
    append.add_argument("--task-name", required=True)
    append.add_argument("--status", choices=("pass", "fail", "partial", "blocked"), required=True)
    append.add_argument("--validation", default="")
    append.add_argument("--notes", default="")
    append.add_argument(
        "--supervisor-state",
        choices=(
            "success",
            "partial_success",
            "retryable_provider_error",
            "unsafe_partial",
            "cli_error",
            "run_timeout",
            "aborted",
        ),
    )
    append.add_argument("--provider-code")
    append.add_argument("--provider-message")
    append.add_argument("--provider-request-id")
    append.add_argument("--provider-error-line")
    append.add_argument("--provider-id")
    append.add_argument("--provider-kind")
    append.add_argument("--attempt-count", type=non_negative_int)
    append.add_argument("--attempts", type=non_negative_int)
    append.add_argument("--retry-count", type=non_negative_int)
    append.add_argument("--retry-delays-ms", type=parse_retry_delays_ms)
    append.add_argument("--no-usage-reason")
    append.add_argument("--quota-percent-status", choices=("measured", "estimated", "unavailable"))
    append.add_argument("--quota-percent-unavailable-reason")
    append.add_argument("--source-run-dir")
    append.add_argument("--source-result-path")
    append.add_argument("--preview")
    append.add_argument("--task-kind")
    append.add_argument("--artifact-quality", choices=("pass", "partial", "fail"))
    append.add_argument("--scope-safety", choices=("pass", "fail"))
    append.add_argument("--validation-result", choices=("pass", "fail"))
    append.add_argument(
        "--codex-repair-size",
        choices=("none", "small polish", "moderate fix", "rewrite needed"),
    )
    append.add_argument("--codex-direct-fallback-reason")
    append.add_argument("--codex-token-usage-status", choices=("measured", "estimated", "unavailable"))
    append.add_argument("--codex-token-usage-reason")
    for field in PROVIDER_BOOL_FIELDS:
        append.add_argument(f"--{field.replace('_', '-')}", action=argparse.BooleanOptionalAction, default=None)
    append.add_argument("--usage-before", type=Path)
    append.add_argument("--usage-after", type=Path)
    append.add_argument(
        "--quota-percent-direction",
        choices=("remaining", "used"),
        default="remaining",
        help="How to derive quota-percent-used from before/after snapshots.",
    )
    for field in STAT_FIELDS:
        append.add_argument(f"--{field.replace('_', '-')}", type=stat_type(field))
    append.set_defaults(func=append_result)

    import_duel = subparsers.add_parser("import-duel-results", help="Append rows from an external supervisor duel results.json.")
    import_duel.add_argument("--source", type=Path, required=True)
    import_duel.add_argument("--path", type=Path, default=DEFAULT_LEDGER)
    import_duel.add_argument("--tool", choices=("zcode", "claude-code-glm52", "all"), default="zcode")
    import_duel.add_argument("--allow-duplicates", action="store_true")
    import_duel.set_defaults(func=import_duel_results)

    report = subparsers.add_parser("summarize", help="Summarize recorded benchmark results.")
    report.add_argument("--path", type=Path, default=DEFAULT_LEDGER)
    report.set_defaults(func=lambda args: summarize(args.path))

    show = subparsers.add_parser("show-log", help="Show recent JSONL result records.")
    show.add_argument("--path", type=Path, default=DEFAULT_LEDGER)
    show.add_argument("--limit", type=int, default=10)
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=lambda args: show_log(args.path, args.limit, args.json))

    compare = subparsers.add_parser(
        "compare-codex-runs",
        help="Compare Codex exec JSONL token usage across direct and ZCode-delegated modes.",
    )
    compare.add_argument("--baseline-label", required=True)
    compare.add_argument(
        "--run",
        action="append",
        type=lambda raw: parse_labeled_path(raw, option="--run"),
        required=True,
        help="Run event JSONL as label=/path/to/events.jsonl. Repeat for each mode.",
    )
    compare.add_argument(
        "--quality",
        action="append",
        type=parse_quality,
        default=[],
        help="Quality status as label=pass|fail|partial|blocked|unknown.",
    )
    compare.add_argument(
        "--zcode-run-json",
        action="append",
        type=lambda raw: parse_labeled_path(raw, option="--zcode-run-json"),
        default=[],
        help="Optional ZCode run JSON as label=/path/to/run.zcode.json.",
    )
    compare.add_argument(
        "--duration",
        action="append",
        type=lambda raw: parse_labeled_float(raw, option="--duration"),
        default=[],
        help="Elapsed seconds as label=seconds. Repeat for each mode.",
    )
    compare.add_argument(
        "--timeout",
        action="append",
        type=lambda raw: parse_labeled_bool(raw, option="--timeout"),
        default=[],
        help="Timeout status as label=true|false. Repeat for each mode.",
    )
    compare.add_argument(
        "--candidate-label",
        action="append",
        default=[],
        help="Label to gate against the baseline. Defaults to every non-baseline run when any gate is enabled.",
    )
    compare.add_argument("--min-reduction-percent", type=bounded_percent)
    compare.add_argument(
        "--reduction-metric",
        choices=CODEX_USAGE_METRIC_FIELDS,
        default="effective_codex_work",
        help="Metric used for reduction and gate calculations.",
    )
    compare.add_argument("--require-quality-pass", action="store_true")
    compare.add_argument("--require-zcode-acceptance", action="store_true")
    compare.add_argument("--max-duration-ratio", type=non_negative_float)
    compare.add_argument("--markdown-out", type=Path)
    compare.set_defaults(func=compare_codex_runs)

    accept = subparsers.add_parser(
        "accept-zcode-artifact",
        help="Evaluate a ZCode run or audit JSON against the delegated artifact acceptance gate.",
    )
    accept.add_argument("--zcode-run-json", type=Path, required=True)
    accept.add_argument("--label", default="zcode")
    accept.add_argument("--markdown-out", type=Path)
    accept.set_defaults(func=accept_zcode_artifact)

    compare_acceptance = subparsers.add_parser(
        "compare-zcode-acceptance",
        help="Compare measured direct Codex usage with a ZCode artifact accepted by the thin local gate.",
    )
    compare_acceptance.add_argument("--direct-label", default="direct")
    compare_acceptance.add_argument("--direct-run-events", type=Path, required=True)
    compare_acceptance.add_argument(
        "--direct-quality",
        choices=("pass", "fail", "partial", "blocked", "unknown"),
        default="pass",
    )
    compare_acceptance.add_argument("--candidate-label", default="zcode_thin_acceptance")
    compare_acceptance.add_argument("--zcode-run-json", type=Path, required=True)
    compare_acceptance.add_argument(
        "--candidate-codex-token-usage-status",
        choices=("measured", "estimated", "unavailable"),
        default="unavailable",
    )
    compare_acceptance.add_argument(
        "--candidate-run-events",
        type=Path,
        help=(
            "Codex exec event JSONL for the delegated launcher/orchestration turn only. "
            "Required when candidate token usage status is measured."
        ),
    )
    compare_acceptance.add_argument("--candidate-codex-token-estimate", type=non_negative_int)
    compare_acceptance.add_argument("--candidate-codex-token-usage-reason")
    compare_acceptance.add_argument(
        "--reduction-metric",
        choices=CODEX_USAGE_METRIC_FIELDS,
        default="effective_codex_work",
    )
    compare_acceptance.add_argument(
        "--min-estimated-reduction-percent",
        "--min-reduction-percent",
        dest="min_estimated_reduction_percent",
        type=bounded_percent,
    )
    compare_acceptance.add_argument("--require-token-estimate", action="store_true")
    compare_acceptance.add_argument("--markdown-out", type=Path)
    compare_acceptance.set_defaults(func=compare_zcode_acceptance)

    add_codex_usage_parsers(subparsers)
    add_context_intake_parser(subparsers)
    add_direct_launcher_parser(subparsers)
    add_token_reduction_parsers(subparsers)
    add_strict_contract_parsers(subparsers)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
