"""Codex exec usage parsing, ledger capture, and summary reporting."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .metrics import (
        COMPARISON_METRIC_FIELDS,
        USAGE_FIELDS,
        compute_usage_metrics,
        metric_invariant_violations,
        token_metric_metadata,
    )
    from .mode_taxonomy import CANONICAL_MODES, normalize_mode
except ImportError:  # pragma: no cover - direct script execution
    from metrics import (
        COMPARISON_METRIC_FIELDS,
        USAGE_FIELDS,
        compute_usage_metrics,
        metric_invariant_violations,
        token_metric_metadata,
    )
    from mode_taxonomy import CANONICAL_MODES, normalize_mode

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None  # type: ignore[assignment]

SCHEMA_VERSION = "codex_usage_ledger.v1"
COLLECTOR_VERSION = "0.2.0"
DEFAULT_CODEX_LEDGER = Path("artifacts/evals/codex-usage-ledger.jsonl")
KNOWN_EVENT_TYPES = {
    "thread.started",
    "turn.started",
    "turn.completed",
    "turn.failed",
    "error",
}
MODE_CHOICES = CANONICAL_MODES
RUN_KIND_CHOICES = ("measurement", "production", "noop_baseline")
PHASE_CHOICES = (
    "plan",
    "implementation",
    "acceptance",
    "validation",
    "validation_audit",
    "report",
    "contract_generation",
    "zcode_launch",
    "zcode_self_audit_parse",
    "deterministic_acceptance",
    "codex_acceptance_audit",
    "shadow_codex_audit",
    "summary_render",
    "noop_baseline",
)
QUALITY_CHOICES = ("pass", "partial", "fail", "blocked", "unknown")
SAFETY_CHOICES = ("pass", "fail", "skipped", "unknown")
SECRET_PATH_NEEDLES = (".env", "id_rsa", "id_ed25519", ".ssh", "credential", "credentials")


@dataclass(frozen=True)
class CodexUsageParseResult:
    usage: dict[str, Any]
    events: dict[str, Any]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def non_negative_int(raw: str) -> int:
    value = int(raw)
    if value < 0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return value


def positive_int(raw: str) -> int:
    value = int(raw)
    if value < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return value


def non_negative_float(raw: str) -> float:
    value = float(raw)
    if value < 0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return value


def optional_bool(raw: str) -> bool:
    lowered = raw.strip().lower()
    if lowered in {"1", "true", "yes", "y", "pass"}:
        return True
    if lowered in {"0", "false", "no", "n", "fail"}:
        return False
    raise argparse.ArgumentTypeError("value must be true or false")


def safe_int(value: Any) -> tuple[int, bool]:
    if isinstance(value, bool):
        return 0, True
    if isinstance(value, int):
        return max(value, 0), value < 0
    if isinstance(value, float) and value.is_integer():
        return max(int(value), 0), value < 0
    if value is None:
        return 0, False
    return 0, True


def is_known_event_type(event_type: Any) -> bool:
    return isinstance(event_type, str) and (
        event_type in KNOWN_EVENT_TYPES or event_type.startswith("item.")
    )


def parse_codex_exec_jsonl(paths: list[Path]) -> CodexUsageParseResult:
    totals = {field: 0 for field in USAGE_FIELDS}
    event_counts: dict[str, int] = {}
    malformed_count = 0
    unknown_count = 0
    turn_failed_count = 0
    error_count = 0
    usage_event_count = 0
    usage_incomplete = False
    anomaly = False

    for path in paths:
        with path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    event = json.loads(text)
                except json.JSONDecodeError:
                    malformed_count += 1
                    continue
                event_type = event.get("type") if isinstance(event, dict) else None
                event_key = event_type if isinstance(event_type, str) else "unknown"
                event_counts[event_key] = event_counts.get(event_key, 0) + 1
                if not is_known_event_type(event_type):
                    unknown_count += 1
                if event_type == "turn.failed":
                    turn_failed_count += 1
                if event_type == "error":
                    error_count += 1
                if event_type != "turn.completed" or not isinstance(event, dict):
                    continue
                raw_usage = event.get("usage")
                if not isinstance(raw_usage, dict):
                    continue
                if not any(field in raw_usage for field in USAGE_FIELDS):
                    anomaly = True
                    continue
                usage_event_count += 1
                if not all(field in raw_usage for field in USAGE_FIELDS):
                    usage_incomplete = True
                    anomaly = True
                    continue
                event_totals: dict[str, int] = {}
                event_anomaly = False
                for field in USAGE_FIELDS:
                    amount, field_anomaly = safe_int(raw_usage.get(field))
                    event_totals[field] = amount
                    event_anomaly = event_anomaly or field_anomaly
                if event_anomaly:
                    usage_incomplete = True
                    anomaly = True
                    continue
                for field, amount in event_totals.items():
                    totals[field] += amount

    if usage_event_count == 0 or usage_incomplete:
        usage = {
            **{field: None for field in COMPARISON_METRIC_FIELDS},
            "usage_missing": True,
            "usage_anomaly": anomaly,
            "turn_completed_usage_count": usage_event_count,
        }
    else:
        input_tokens = totals["input_tokens"]
        cached = totals["cached_input_tokens"]
        anomaly = anomaly or cached > input_tokens
        computed = compute_usage_metrics(
            input_tokens=input_tokens,
            cached_input_tokens=cached,
            output_tokens=totals["output_tokens"],
            reasoning_output_tokens=totals["reasoning_output_tokens"],
        )
        usage = {
            **computed,
            "usage_missing": False,
            "usage_anomaly": anomaly or bool(metric_invariant_violations(computed)),
            "turn_completed_usage_count": usage_event_count,
        }
    events = {
        "event_counts": dict(sorted(event_counts.items())),
        "malformed_jsonl_line_count": malformed_count,
        "unknown_event_count": unknown_count,
        "turn_failed_count": turn_failed_count,
        "error_count": error_count,
    }
    return CodexUsageParseResult(usage=usage, events=events)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def byte_count(path: Path | None) -> int:
    if path is None or not path.exists() or not path.is_file():
        return 0
    return path.stat().st_size


def secret_like_path(path: Path) -> bool:
    lowered = str(path).lower()
    return any(needle in lowered for needle in SECRET_PATH_NEEDLES)


def hash_file(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists() or not path.is_file() or secret_like_path(path):
        return None
    data = path.read_bytes()
    return {"path": str(path), "sha256": sha256_bytes(data), "bytes": len(data)}


def prompt_fingerprint(args: argparse.Namespace) -> tuple[str | None, int]:
    if args.prompt_file:
        data = args.prompt_file.read_bytes()
        return sha256_bytes(data), len(data)
    if args.prompt_text is not None:
        data = args.prompt_text.encode("utf-8")
        return sha256_bytes(data), len(data)
    return None, 0


def git_output(args: list[str], cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def current_base_commit(workspace: Path) -> str:
    return git_output(["rev-parse", "HEAD"], workspace) or "unknown"


def gitignored(path: Path, cwd: Path) -> bool:
    if not path.exists():
        return False
    try:
        rel = path.resolve().relative_to(cwd.resolve())
    except ValueError:
        return False
    result = git_output(["check-ignore", "-q", str(rel)], cwd)
    return result == ""


def file_mode_string(path: Path | None) -> str:
    if path is None or not path.exists():
        return "unavailable"
    return format(stat.S_IMODE(path.stat().st_mode), "04o")


def empty_usage_marker() -> dict[str, Any]:
    return {
        **{field: None for field in COMPARISON_METRIC_FIELDS},
        "usage_missing": True,
        "usage_anomaly": False,
    }


def usage_for_benchmark_bucket(record_usage: dict[str, Any], include: bool) -> dict[str, Any] | None:
    if not include:
        return None
    if record_usage.get("usage_missing") is True:
        return empty_usage_marker()
    return {key: record_usage.get(key) for key in (*COMPARISON_METRIC_FIELDS, "usage_missing", "usage_anomaly")}


def config_fingerprints(workspace: Path, extra_paths: list[Path]) -> dict[str, Any]:
    candidates = [
        workspace / "AGENTS.md",
        workspace / "HANDOFF.md",
        workspace / ".codex" / "ZCODE_DELEGATION.md",
        workspace / ".codex" / "zcode-routing.json",
        *extra_paths,
    ]
    fingerprints: dict[str, Any] = {}
    for path in candidates:
        fingerprint = hash_file(path)
        if fingerprint:
            fingerprints[path.name] = fingerprint
    return fingerprints


def build_io_visibility(args: argparse.Namespace, prompt_sha: str | None, prompt_bytes: int) -> dict[str, Any]:
    raw_paths = [str(path) for path in args.raw_jsonl]
    return {
        "prompt_sha256": prompt_sha,
        "prompt_bytes": prompt_bytes,
        "raw_jsonl_path": raw_paths[0] if raw_paths else None,
        "raw_jsonl_paths": raw_paths,
        "raw_jsonl_bytes_total": sum(byte_count(path) for path in args.raw_jsonl),
        "launcher_stdout_bytes_total": args.launcher_stdout_bytes_total
        if args.launcher_stdout_bytes_total is not None
        else byte_count(args.launcher_stdout_log),
        "launcher_stderr_bytes_total": args.launcher_stderr_bytes_total
        if args.launcher_stderr_bytes_total is not None
        else byte_count(args.launcher_stderr_log),
        "codex_visible_launcher_bytes": args.codex_visible_launcher_bytes,
        "diff_bytes_total": args.diff_bytes_total if args.diff_bytes_total is not None else byte_count(args.diff_path),
        "diff_bytes_shown_to_codex": args.diff_bytes_shown_to_codex,
        "validation_stdout_bytes_total": args.validation_stdout_bytes_total
        if args.validation_stdout_bytes_total is not None
        else byte_count(args.validation_log),
        "validation_stdout_bytes_shown_to_codex": args.validation_stdout_bytes_shown_to_codex,
    }


def build_ledger_record(args: argparse.Namespace) -> dict[str, Any]:
    workspace = args.workspace.resolve()
    parsed = parse_codex_exec_jsonl(args.raw_jsonl)
    prompt_sha, prompt_bytes = prompt_fingerprint(args)
    event_failed = parsed.events["turn_failed_count"] > 0 or parsed.events["error_count"] > 0
    failed = args.failed if args.failed is not None else event_failed
    failure_reason = args.failure_reason
    if failed and failure_reason is None and event_failed:
        failure_reason = "codex_event_failure"
    raw_logs_gitignored = all(gitignored(path, workspace) for path in args.raw_jsonl if path.exists())
    metric_meta = token_metric_metadata()
    mode_info = normalize_mode(args.mode, args.mode_detail)
    run_id = args.run_id or str(uuid.uuid4())
    attempt_valid = args.attempt_valid_for_benchmark
    if attempt_valid is None:
        attempt_valid = not args.invalid_measurement and args.exclusion_reason is None
    excluded = not attempt_valid
    raw_jsonl_path = args.raw_jsonl[0] if args.raw_jsonl else None
    return {
        "schema_version": SCHEMA_VERSION,
        "collector_version": COLLECTOR_VERSION,
        "recorded_at": utc_now(),
        **metric_meta,
        "experiment_id": args.experiment_id,
        "task_id": args.task_id,
        "mode": mode_info["canonical_mode"],
        **mode_info,
        "run_kind": args.run_kind,
        "phase": args.phase,
        "attempt_index": args.attempt_index,
        "run_id": run_id,
        "parent_run_id": args.parent_run_id,
        "previous_run_id": args.previous_run_id,
        "base_commit": args.base_commit or current_base_commit(workspace),
        "worktree_mode": args.worktree_mode,
        "codex_cli_version": args.codex_cli_version,
        "model": args.model,
        "model_arg": args.model,
        "resolved_model": args.resolved_model,
        "reasoning_effort": args.reasoning_effort,
        "sandbox": args.sandbox,
        "approval_mode": args.approval_mode,
        "ignore_user_config": args.ignore_user_config,
        "ignore_rules": args.ignore_rules,
        "profile": args.profile,
        "web_search_mode": args.web_search_mode,
        "output_schema_path": str(args.output_schema_path) if args.output_schema_path else None,
        "output_schema_sha256": hash_file(args.output_schema_path)["sha256"] if hash_file(args.output_schema_path) else None,
        "image_arg_style": args.image_arg_style,
        "prompt_source": args.prompt_source,
        "run_order_index": args.run_order_index,
        "run_order_seed": args.run_order_seed,
        "cache_condition": args.cache_condition,
        "feature_flags": args.feature_flag,
        "routing_decision": {
            "selected": args.routing_selected or mode_info["canonical_mode"],
            "reason_codes": args.routing_reason_code,
            "expected_codex_tokens": args.routing_expected_codex_tokens,
            "expected_wall_clock_sec": args.routing_expected_wall_clock_sec,
            "fallback": args.routing_fallback,
            "urgent_task_penalty": args.routing_urgent_task_penalty,
            "background_safe": args.routing_background_safe,
        },
        "config_fingerprints": config_fingerprints(workspace, args.fingerprint_file),
        "usage": parsed.usage,
        "benchmark_clean_usage": usage_for_benchmark_bucket(parsed.usage, attempt_valid),
        "operator_total_usage": usage_for_benchmark_bucket(parsed.usage, True),
        "excluded_attempt_usage": usage_for_benchmark_bucket(parsed.usage, excluded),
        "attempt_valid_for_benchmark": attempt_valid,
        "invalid_measurement": args.invalid_measurement,
        "exclusion_reason": args.exclusion_reason,
        "rerun_reason": args.rerun_reason,
        "total_until_success": None,
        "events": parsed.events,
        "io_visibility": build_io_visibility(args, prompt_sha, prompt_bytes),
        "quality": {
            "allowed_files_only": args.allowed_files_only,
            "scope_safety": args.scope_safety,
            "validation_result": args.validation_result,
            "artifact_quality": args.artifact_quality,
            "codex_repair_size": args.codex_repair_size,
        },
        "safety": {
            "env_policy": args.env_policy,
            "secret_scan_result": args.secret_scan_result,
            "raw_logs_gitignored": raw_logs_gitignored,
            "raw_jsonl_sensitivity": "sensitive",
            "raw_jsonl_file_mode": file_mode_string(raw_jsonl_path),
            "raw_jsonl_gitignored": raw_logs_gitignored,
            "raw_jsonl_retention_days": args.raw_jsonl_retention_days,
            "raw_jsonl_secret_scan_result": args.raw_jsonl_secret_scan_result,
            "raw_jsonl_redacted_preview_path": None,
            "untrusted_artifact_boundary": raw_logs_gitignored,
            "budget_guard_hit": args.budget_guard_hit,
        },
        "artifacts": {
            "patch_path": str(args.patch_path) if args.patch_path else None,
            "validation_log": str(args.validation_log) if args.validation_log else None,
            "manifest_path": str(args.manifest_path) if args.manifest_path else None,
            "summary_path": str(args.summary_path) if args.summary_path else None,
            "launcher_stdout_log": str(args.launcher_stdout_log) if args.launcher_stdout_log else None,
            "launcher_stderr_log": str(args.launcher_stderr_log) if args.launcher_stderr_log else None,
        },
        "user_impact": {
            "ask_user_count": args.ask_user_count,
            "manual_intervention_required": args.manual_intervention_required,
            "manual_repair_required": args.manual_repair_required,
            "extra_confirmation_burden": args.extra_confirmation_burden,
        },
        "failure": {"failed": failed, "failure_reason": failure_reason},
        "pricing": None,
    }


def append_jsonl_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def load_codex_usage_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                records.append(json.loads(text))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid ledger JSONL: {exc}") from exc
    return records


def sum_usage(records: list[dict[str, Any]]) -> dict[str, int]:
    totals = {field: 0 for field in COMPARISON_METRIC_FIELDS}
    for record in records:
        usage = record.get("usage") if isinstance(record.get("usage"), dict) else {}
        if usage.get("usage_missing") is True:
            continue
        for field in totals:
            value = usage.get(field)
            if isinstance(value, int):
                totals[field] += value
    return totals


def is_clean_benchmark_attempt(record: dict[str, Any]) -> bool:
    if record.get("attempt_valid_for_benchmark") is False:
        return False
    if record.get("invalid_measurement") is True:
        return False
    return record.get("exclusion_reason") in (None, "")


def clean_benchmark_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [record for record in records if is_clean_benchmark_attempt(record)]


def excluded_attempt_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [record for record in records if not is_clean_benchmark_attempt(record)]


def count_by(records: list[dict[str, Any]], path: tuple[str, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        value: Any = record
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        if isinstance(value, str):
            counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def total_until_success(records: list[dict[str, Any]]) -> tuple[int, int | None]:
    total = 0
    success_attempt: int | None = None
    for record in sorted(records, key=lambda item: item.get("attempt_index", 0)):
        usage = record.get("usage") if isinstance(record.get("usage"), dict) else {}
        if usage.get("usage_missing") is not True:
            total += int(usage.get("effective_codex_work") or 0)
        failure = record.get("failure") if isinstance(record.get("failure"), dict) else {}
        if failure.get("failed") is not True:
            success_attempt = record.get("attempt_index")
            break
    return total, success_attempt


def group_records(records: list[dict[str, Any]], keys: tuple[str, ...]) -> dict[tuple[Any, ...], list[dict[str, Any]]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for record in records:
        key = tuple(record.get(item, "unknown") for item in keys)
        grouped.setdefault(key, []).append(record)
    return grouped


def markdown_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(item) for item in row) + " |")
    return lines


def build_codex_usage_summary(records: list[dict[str, Any]]) -> str:
    usage_missing = sum(1 for item in records if item.get("usage", {}).get("usage_missing") is True)
    modes = sorted({str(item.get("mode", "unknown")) for item in records})
    metric_meta = token_metric_metadata()
    clean_records = clean_benchmark_records(records)
    excluded_records = excluded_attempt_records(records)
    operator_usage = sum_usage(records)
    clean_usage = sum_usage(clean_records)
    excluded_usage = sum_usage(excluded_records)
    lines = [
        "# Codex Usage Summary",
        "",
        "## Executive Summary",
        "",
        f"- records: `{len(records)}`",
        f"- modes: `{', '.join(modes) or 'none'}`",
        f"- primary_metric: `{metric_meta['primary_metric']}`",
        f"- primary_metric_formula: `{metric_meta['primary_metric_formula']}`",
        f"- metric_formula_version: `{metric_meta['metric_formula_version']}`",
        f"- usage_missing_attempts: `{usage_missing}`",
        f"- budget_guard_hits: `{sum(1 for item in records if item.get('safety', {}).get('budget_guard_hit') is True)}`",
        f"- invalid_measurement_attempts: `{sum(1 for item in records if item.get('invalid_measurement') is True)}`",
        "",
        "## Clean Benchmark Vs Operator Total",
        "",
    ]
    lines.extend(markdown_table(
        ["bucket", "attempts", "input", "cached", "uncached", "output", "reasoning", "effective_codex_work", "usage_missing"],
        [
            [
                "benchmark_clean_usage",
                len(clean_records),
                clean_usage["input_tokens"],
                clean_usage["cached_input_tokens"],
                clean_usage["uncached_input_tokens"],
                clean_usage["output_tokens"],
                clean_usage["reasoning_output_tokens"],
                clean_usage["effective_codex_work"],
                sum(1 for item in clean_records if item.get("usage", {}).get("usage_missing") is True),
            ],
            [
                "operator_total_usage",
                len(records),
                operator_usage["input_tokens"],
                operator_usage["cached_input_tokens"],
                operator_usage["uncached_input_tokens"],
                operator_usage["output_tokens"],
                operator_usage["reasoning_output_tokens"],
                operator_usage["effective_codex_work"],
                usage_missing,
            ],
            [
                "excluded_attempt_usage",
                len(excluded_records),
                excluded_usage["input_tokens"],
                excluded_usage["cached_input_tokens"],
                excluded_usage["uncached_input_tokens"],
                excluded_usage["output_tokens"],
                excluded_usage["reasoning_output_tokens"],
                excluded_usage["effective_codex_work"],
                sum(1 for item in excluded_records if item.get("usage", {}).get("usage_missing") is True),
            ],
        ],
    ))
    lines.extend([
        "",
        "## Token By Task / Mode / Phase",
        "",
    ])
    phase_rows = []
    for key, group in sorted(group_records(records, ("task_id", "mode", "phase")).items()):
        usage = sum_usage(group)
        until_success, success_attempt = total_until_success(group)
        phase_rows.append([
            key[0],
            key[1],
            key[2],
            len(group),
            usage["uncached_input_tokens"],
            usage["output_tokens"],
            usage["reasoning_output_tokens"],
            usage["effective_codex_work"],
            until_success,
            success_attempt or "",
            sum(1 for item in group if item.get("usage", {}).get("usage_missing") is True),
        ])
    lines.extend(markdown_table(
        ["task", "mode", "phase", "attempts", "uncached_input", "output", "reasoning", "effective", "total_until_success", "success_attempt", "usage_missing"],
        phase_rows,
    ))
    lines.extend(["", "## Mode Totals", ""])
    mode_rows = []
    for key, group in sorted(group_records(records, ("mode",)).items()):
        usage = sum_usage(group)
        mode_rows.append([
            key[0],
            len(group),
            usage["input_tokens"],
            usage["cached_input_tokens"],
            usage["uncached_input_tokens"],
            usage["output_tokens"],
            usage["reasoning_output_tokens"],
            usage["effective_codex_work"],
        ])
    lines.extend(markdown_table(["mode", "attempts", "input", "cached", "uncached", "output", "reasoning", "effective_codex_work"], mode_rows))
    lines.extend(["", "## Quality Gates", ""])
    quality_rows = []
    for key, group in sorted(group_records(records, ("mode",)).items()):
        quality_rows.append([
            key[0],
            count_by(group, ("quality", "allowed_files_only")),
            count_by(group, ("quality", "scope_safety")),
            count_by(group, ("quality", "validation_result")),
            count_by(group, ("quality", "artifact_quality")),
            count_by(group, ("quality", "codex_repair_size")),
        ])
    lines.extend(markdown_table(["mode", "allowed_files_only", "scope_safety", "validation", "artifact", "repair"], quality_rows))
    lines.extend(["", "## Visibility And User Impact", ""])
    visibility_rows = []
    for key, group in sorted(group_records(records, ("mode",)).items()):
        visibility_rows.append([
            key[0],
            sum(int(item.get("io_visibility", {}).get("codex_visible_launcher_bytes") or 0) for item in group),
            sum(int(item.get("io_visibility", {}).get("diff_bytes_shown_to_codex") or 0) for item in group),
            sum(int(item.get("io_visibility", {}).get("validation_stdout_bytes_shown_to_codex") or 0) for item in group),
            sum(int(item.get("user_impact", {}).get("ask_user_count") or 0) for item in group),
            sum(1 for item in group if item.get("user_impact", {}).get("manual_intervention_required") is True),
        ])
    lines.extend(markdown_table(["mode", "launcher_visible_bytes", "diff_visible_bytes", "validation_visible_bytes", "user_prompts", "manual_interventions"], visibility_rows))
    next_target = max(
        group_records(records, ("mode", "phase")).items(),
        key=lambda item: sum_usage(item[1])["effective_codex_work"],
        default=((None, None), []),
    )
    target_usage = sum_usage(next_target[1])["effective_codex_work"] if next_target[1] else 0
    lines.extend([
        "",
        "## Next Optimization Target",
        "",
        f"- `{next_target[0][0]}/{next_target[0][1]}` has the largest effective Codex work: `{target_usage}`.",
        "",
    ])
    return "\n".join(lines)


def capture_codex_exec(args: argparse.Namespace) -> int:
    record = build_ledger_record(args)
    append_jsonl_record(args.ledger, record)
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0


def summarize_codex_usage(args: argparse.Namespace) -> int:
    records = load_codex_usage_records(args.ledger)
    markdown = build_codex_usage_summary(records)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(markdown, encoding="utf-8")
    print(str(args.out))
    return 0


def add_codex_usage_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    capture = subparsers.add_parser("capture-codex-exec", help="Append one Codex usage ledger phase-attempt record.")
    capture.add_argument("--ledger", type=Path, default=DEFAULT_CODEX_LEDGER)
    capture.add_argument("--experiment-id", required=True)
    capture.add_argument("--task-id", required=True)
    capture.add_argument("--mode", choices=MODE_CHOICES, required=True)
    capture.add_argument("--mode-detail")
    capture.add_argument("--run-kind", choices=RUN_KIND_CHOICES, required=True)
    capture.add_argument("--phase", choices=PHASE_CHOICES, required=True)
    capture.add_argument("--attempt-index", type=positive_int, required=True)
    capture.add_argument("--raw-jsonl", type=Path, action="append", required=True)
    capture.add_argument("--workspace", type=Path, default=Path("."))
    capture.add_argument("--run-id")
    capture.add_argument("--parent-run-id")
    capture.add_argument("--previous-run-id")
    capture.add_argument("--base-commit")
    capture.add_argument("--worktree-mode", choices=("current", "disposable", "tmp_clone", "fixture", "unknown"), default="unknown")
    capture.add_argument("--codex-cli-version", default="unknown")
    capture.add_argument("--model", default="unknown")
    capture.add_argument("--resolved-model")
    capture.add_argument("--reasoning-effort", default="unknown")
    capture.add_argument("--sandbox", default="unknown")
    capture.add_argument("--approval-mode", default="unknown")
    capture.add_argument("--ignore-user-config", action=argparse.BooleanOptionalAction, default=None)
    capture.add_argument("--ignore-rules", action=argparse.BooleanOptionalAction, default=None)
    capture.add_argument("--profile")
    capture.add_argument("--web-search-mode")
    capture.add_argument("--output-schema-path", type=Path)
    capture.add_argument("--image-arg-style", choices=("none", "repeated_flags", "comma_list", "mixed", "unknown"), default="unknown")
    capture.add_argument("--prompt-source", choices=("argument", "stdin", "file", "unknown"), default="unknown")
    capture.add_argument("--run-order-index", type=non_negative_int)
    capture.add_argument("--run-order-seed")
    capture.add_argument("--cache-condition")
    capture.add_argument("--feature-flag", action="append", default=[])
    capture.add_argument("--routing-selected", choices=MODE_CHOICES)
    capture.add_argument("--routing-reason-code", action="append", default=[])
    capture.add_argument("--routing-expected-codex-tokens", type=non_negative_int)
    capture.add_argument("--routing-expected-wall-clock-sec", type=non_negative_float)
    capture.add_argument("--routing-fallback", choices=(*MODE_CHOICES, "none"), default="codex_only")
    capture.add_argument("--routing-urgent-task-penalty", action="store_true")
    capture.add_argument("--routing-background-safe", action=argparse.BooleanOptionalAction, default=True)
    capture.add_argument("--fingerprint-file", type=Path, action="append", default=[])
    capture.add_argument("--prompt-file", type=Path)
    capture.add_argument("--prompt-text")
    capture.add_argument("--patch-path", type=Path)
    capture.add_argument("--diff-path", type=Path)
    capture.add_argument("--validation-log", type=Path)
    capture.add_argument("--manifest-path", type=Path)
    capture.add_argument("--summary-path", type=Path)
    capture.add_argument("--launcher-stdout-log", type=Path)
    capture.add_argument("--launcher-stderr-log", type=Path)
    capture.add_argument("--launcher-stdout-bytes-total", type=non_negative_int)
    capture.add_argument("--launcher-stderr-bytes-total", type=non_negative_int)
    capture.add_argument("--codex-visible-launcher-bytes", type=non_negative_int, default=0)
    capture.add_argument("--diff-bytes-total", type=non_negative_int)
    capture.add_argument("--diff-bytes-shown-to-codex", type=non_negative_int, default=0)
    capture.add_argument("--validation-stdout-bytes-total", type=non_negative_int)
    capture.add_argument("--validation-stdout-bytes-shown-to-codex", type=non_negative_int, default=0)
    capture.add_argument("--allowed-files-only", choices=QUALITY_CHOICES, default="unknown")
    capture.add_argument("--scope-safety", choices=QUALITY_CHOICES, default="unknown")
    capture.add_argument("--validation-result", choices=QUALITY_CHOICES, default="unknown")
    capture.add_argument("--artifact-quality", choices=QUALITY_CHOICES, default="unknown")
    capture.add_argument("--codex-repair-size", default="unknown")
    capture.add_argument("--env-policy", choices=("allowlist", "unknown"), default="unknown")
    capture.add_argument("--secret-scan-result", choices=SAFETY_CHOICES, default="unknown")
    capture.add_argument("--raw-jsonl-secret-scan-result", choices=SAFETY_CHOICES, default="unknown")
    capture.add_argument("--raw-jsonl-retention-days", type=non_negative_int, default=14)
    capture.add_argument("--budget-guard-hit", action="store_true")
    capture.add_argument("--attempt-valid-for-benchmark", type=optional_bool)
    capture.add_argument("--invalid-measurement", action="store_true")
    capture.add_argument("--exclusion-reason")
    capture.add_argument("--rerun-reason")
    capture.add_argument("--ask-user-count", type=non_negative_int, default=0)
    capture.add_argument("--manual-intervention-required", action="store_true")
    capture.add_argument("--manual-repair-required", action="store_true")
    capture.add_argument("--extra-confirmation-burden", action="store_true")
    capture.add_argument("--failed", type=optional_bool)
    capture.add_argument("--failure-reason")
    capture.set_defaults(func=capture_codex_exec)

    summary = subparsers.add_parser("summarize-codex-usage", help="Write a Markdown Codex usage summary.")
    summary.add_argument("--ledger", type=Path, default=DEFAULT_CODEX_LEDGER)
    summary.add_argument("--out", type=Path, required=True)
    summary.set_defaults(func=summarize_codex_usage)
