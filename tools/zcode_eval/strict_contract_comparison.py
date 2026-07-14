"""Strict Contract V3 Codex-only vs ZCode-delegated comparison runner."""

from __future__ import annotations

import argparse
import copy
import difflib
import hashlib
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

try:
    from .codex_usage import parse_codex_exec_jsonl
    from .strict_contract_breakdown import (
        apply_report_schema,
        build_codex_exec_row,
        compact_policy_contract,
        ensure_codex_output_schema,
        fixed_json_instruction,
        sha256_text,
        summarize,
        unavailable_usage_metrics,
        write_json,
        write_markdown,
        zero_usage_metrics,
    )
    from .strict_contract_tasks import task_specs
except ImportError:  # pragma: no cover - direct script execution
    from codex_usage import parse_codex_exec_jsonl
    from strict_contract_breakdown import (
        apply_report_schema,
        build_codex_exec_row,
        compact_policy_contract,
        ensure_codex_output_schema,
        fixed_json_instruction,
        sha256_text,
        summarize,
        unavailable_usage_metrics,
        write_json,
        write_markdown,
        zero_usage_metrics,
    )
    from strict_contract_tasks import task_specs


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_ROOT = REPO_ROOT / "artifacts/reports"
HARNESS_VERSION = "strict_contract_comparison.v4"
WORKER_TOKEN_SOURCE_TYPES = {
    "artifact_sidecar",
    "provider_usage_ledger",
    "worker_usage_sidecar",
    "wrapper_json_usage",
    "zcode_cli_model_usage_db_delta",
    "zcode_cli_json_usage",
    "zcode_worker_usage_ledger",
}
WORKER_TOKEN_SOURCE_PATH_KEYS = (
    "worker_usage_source_path",
    "token_usage_source_path",
    "tokens_source_path",
    "provider_usage_ledger_path",
    "usage_ledger_path",
    "usage_source_path",
)
WORKER_USAGE_SIDECAR_NAMES = (
    "worker-usage.json",
    "worker-usage.jsonl",
    "worker-usage-ledger.json",
    "worker-usage-ledger.jsonl",
    "usage-ledger.json",
    "usage-ledger.jsonl",
)
ZCODE_MODEL_USAGE_DB_DELTA_BEFORE = "zcode-model-usage-before.json"
ZCODE_MODEL_USAGE_DB_DELTA_AFTER = "zcode-model-usage-delta.json"
GIT_CONTROL_ENV_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
    "GIT_CEILING_DIRECTORIES",
)
WORKER_USAGE_EXTERNAL_PATH_ENV_KEYS = (
    "ZCODE_WORKER_USAGE_SIDECAR",
    "ZCODE_WORKER_USAGE_LEDGER",
    "ZCODE_PROVIDER_USAGE_LEDGER",
    "ZCODE_USAGE_LEDGER",
)
WORKER_USAGE_PROVIDER_ENV_KEYS = (
    "ZCODE_WORKER_USAGE_PROVIDER",
    "ZCODE_PROVIDER_ID",
)
WORKER_USAGE_MODEL_ENV_KEYS = (
    "ZCODE_WORKER_USAGE_MODEL",
    "ZCODE_MODEL_ID",
)
PROVIDER_OVERLOAD_CODES = {"1305"}
PROVIDER_RATE_LIMIT_CODES = {"1302"}
WORKER_USAGE_EMPTY_SIDECAR_REASON = "worker_usage_empty_sidecar"
PROVIDER_SUCCESS_WITHOUT_USAGE_PAYLOAD_REASON = "provider_success_without_usage_payload"
WORKER_FINALIZATION_MODES = ("zcode_owned", "supervisor_owned")


@dataclass(frozen=True)
class CommandResult:
    rc: int
    seconds: float
    timed_out: bool


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def default_report_dir() -> Path:
    return DEFAULT_REPORT_ROOT / f"{timestamp()}__codex__strict-contract-v3-comparison"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def first_present_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def finite_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def token_int(value: Any) -> int | None:
    number = finite_number(value)
    if isinstance(number, int) and number >= 0:
        return number
    if isinstance(number, float) and number.is_integer() and number >= 0:
        return int(number)
    return None


def positive_token_int(value: Any) -> int | None:
    tokens = token_int(value)
    return tokens if tokens is not None and tokens > 0 else None


def usage_source_type(usage: dict[str, Any], default: str | None = None) -> str | None:
    return first_present_string(
        usage.get("tokens_source"),
        usage.get("source_type"),
        usage.get("usage_source"),
        usage.get("source"),
        default,
    )


def zcode_worker_identity(run_payload: dict[str, Any], usage: dict[str, Any]) -> tuple[str | None, str | None]:
    provider = first_present_string(
        run_payload.get("worker_provider"),
        run_payload.get("provider_id"),
        run_payload.get("provider"),
        usage.get("provider"),
        usage.get("quota_provider"),
    )
    model = first_present_string(
        run_payload.get("worker_model"),
        run_payload.get("model_id"),
        run_payload.get("model"),
        usage.get("model_id"),
        usage.get("model"),
    )
    return provider, model


def zcode_worker_base_fields(
    run_payload: dict[str, Any],
    usage: dict[str, Any],
    source_path: Path | None,
) -> dict[str, Any]:
    provider, model = zcode_worker_identity(run_payload, usage)
    quota_before = finite_number(usage.get("quota_percent_before"))
    quota_after = finite_number(usage.get("quota_percent_after"))
    quota_used = finite_number(usage.get("quota_percent_used"))
    credits_used = finite_number(usage.get("credits_used") or usage.get("credit_used"))
    return {
        "worker_kind": "zcode",
        "worker_provider": provider,
        "worker_model": model,
        "worker_usage_source": "usage_accounting",
        "worker_usage_source_path": str(source_path) if source_path is not None else None,
        "worker_input_tokens": token_int(usage.get("input_tokens")),
        "worker_output_tokens": token_int(usage.get("output_tokens")),
        "worker_reasoning_tokens": token_int(usage.get("reasoning_tokens") or usage.get("reasoning_output_tokens")),
        "worker_cache_read_tokens": token_int(usage.get("cache_read_tokens")),
        "worker_cache_write_tokens": token_int(usage.get("cache_write_tokens")),
        "worker_quota_percent_before": quota_before,
        "worker_quota_percent_after": quota_after,
        "worker_quota_percent_used": quota_used,
        "worker_credits_used": credits_used,
        "worker_non_token_usage": {
            "quota_percent_before": quota_before,
            "quota_percent_after": quota_after,
            "quota_percent_used": quota_used,
            "credits_used": credits_used,
            "quota_source": usage.get("quota_source"),
            "quota_provider": usage.get("quota_provider"),
        },
        "zcode_worker_usage_scope": "excluded_from_codex_effective_work",
    }


def measured_zcode_worker_fields(base: dict[str, Any], tokens: int, *, method: str = "zcode_cli_json_usage") -> dict[str, Any]:
    return {
        **base,
        "worker_usage_status": "measured",
        "worker_usage_unit": "tokens",
        "worker_usage_capture_method": method,
        "worker_total_tokens": tokens,
        "worker_usage_no_usage_reason": None,
        "zcode_worker_usage_status": "measured",
        "zcode_worker_tokens": tokens,
    }


def partial_zcode_worker_fields(base: dict[str, Any], *, unit: str, method: str, reason: str) -> dict[str, Any]:
    return {
        **base,
        "worker_usage_status": "partial",
        "worker_usage_unit": unit,
        "worker_usage_capture_method": method,
        "worker_total_tokens": None,
        "worker_usage_no_usage_reason": reason,
        "zcode_worker_usage_status": "unavailable",
        "zcode_worker_tokens": None,
        "no_usage_reason": reason,
    }


def unavailable_zcode_worker_fields(base: dict[str, Any], reason: str, *, method: str = "usage_accounting") -> dict[str, Any]:
    return {
        **base,
        "worker_usage_status": "unavailable",
        "worker_usage_unit": "unknown",
        "worker_usage_capture_method": method,
        "worker_total_tokens": None,
        "worker_usage_no_usage_reason": reason,
        "zcode_worker_usage_status": "unavailable",
        "zcode_worker_tokens": None,
        "no_usage_reason": reason,
    }


def provider_error_kind(run_payload: dict[str, Any]) -> str | None:
    explicit = first_present_string(run_payload.get("provider_error_kind"))
    if explicit:
        return explicit
    provider_code = first_present_string(run_payload.get("provider_code"))
    if provider_code in PROVIDER_OVERLOAD_CODES:
        return "provider_overload"
    if provider_code in PROVIDER_RATE_LIMIT_CODES:
        return "provider_rate_limit_1302"
    if run_payload.get("provider_error_temporary") is True and run_payload.get("provider_error") is True:
        return "provider_overload"
    if run_payload.get("provider_error") is True:
        return "provider_error"
    return None


def provider_blocker_fields(run_payload: dict[str, Any], *, quality: bool) -> dict[str, Any]:
    kind = provider_error_kind(run_payload)
    blocker = first_present_string(run_payload.get("blocker_kind"))
    if kind in {"provider_overload", "provider_rate_limit_1302"}:
        blocker = "infrastructure_blocker"
    infrastructure_blocker = blocker == "infrastructure_blocker"
    return {
        "supervisor_state": first_present_string(run_payload.get("supervisor_state"), run_payload.get("status")),
        "provider_error": run_payload.get("provider_error"),
        "provider_code": first_present_string(run_payload.get("provider_code")),
        "provider_error_kind": kind,
        "provider_rate_limit_1302": run_payload.get("provider_rate_limit_1302") is True or kind == "provider_rate_limit_1302",
        "provider_rate_limit_1302_count": run_payload.get("provider_rate_limit_1302_count"),
        "provider_fail_fast": run_payload.get("provider_fail_fast"),
        "provider_fail_fast_reason": first_present_string(run_payload.get("provider_fail_fast_reason")),
        "blocker_kind": blocker,
        "infrastructure_blocker": infrastructure_blocker,
        "benchmark_evidence_qualified": False if infrastructure_blocker else None,
        "live_evidence_manifest_allowed": False if infrastructure_blocker else None,
        "quality_failure_kind": "infrastructure_blocker" if infrastructure_blocker else (None if quality else "strict_quality_failure"),
    }


def payload_has_empty_usage_object(payload: dict[str, Any]) -> bool:
    usage = payload.get("usage")
    return isinstance(usage, dict) and not usage


def merge_usage_payload(payload: dict[str, Any], *, default_source: str | None = None) -> dict[str, Any]:
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    accounting = payload.get("usage_accounting") if isinstance(payload.get("usage_accounting"), dict) else {}
    merged = {**usage, **accounting, **{key: value for key, value in payload.items() if key != "usage"}}
    if default_source and not usage_source_type(merged):
        merged["tokens_source"] = default_source
    if not payload or payload_has_empty_usage_object(payload):
        merged["worker_usage_empty_payload"] = True
    return merged


def usage_total_tokens(usage: dict[str, Any]) -> int | None:
    total = positive_token_int(
        usage.get("tokens_used")
        or usage.get("tokens_total")
        or usage.get("total_tokens")
        or usage.get("totalTokens")
        or usage.get("tokensUsed")
    )
    if total is not None:
        return total
    parts = [
        token_int(usage.get("input_tokens") or usage.get("inputTokens")),
        token_int(usage.get("output_tokens") or usage.get("outputTokens")),
        token_int(usage.get("reasoning_tokens") or usage.get("reasoning_output_tokens")),
    ]
    if not any(part is not None for part in parts):
        return None
    total_from_parts = sum(part for part in parts if part is not None)
    return total_from_parts if total_from_parts > 0 else None


def usage_explicit_total_tokens(usage: dict[str, Any]) -> int | None:
    return positive_token_int(first_existing(usage, ("total_tokens", "totalTokens")))


def usage_empty_sidecar_payload(usage: dict[str, Any]) -> bool:
    return usage.get("worker_usage_empty_payload") is True


def empty_sidecar_no_usage_reason(usage: dict[str, Any]) -> str:
    reason = first_present_string(usage.get("no_usage_reason"))
    if reason == PROVIDER_SUCCESS_WITHOUT_USAGE_PAYLOAD_REASON:
        return reason
    return WORKER_USAGE_EMPTY_SIDECAR_REASON


def usage_sidecar_has_measured_tokens(usage: dict[str, Any], *, require_source_type: bool = True) -> bool:
    source = usage_source_type(usage)
    if require_source_type and source not in WORKER_TOKEN_SOURCE_TYPES:
        return False
    if usage_empty_sidecar_payload(usage):
        return False
    if usage.get("unit") != "tokens":
        return False
    return usage_explicit_total_tokens(usage) is not None


def usage_is_measured_tokens(usage: dict[str, Any], *, require_source_type: bool = True) -> bool:
    source = usage_source_type(usage)
    if require_source_type and source not in WORKER_TOKEN_SOURCE_TYPES:
        return False
    if usage_empty_sidecar_payload(usage):
        return False
    return usage_total_tokens(usage) is not None


def usage_has_non_token_evidence(usage: dict[str, Any]) -> str | None:
    if finite_number(usage.get("quota_percent_used")) is not None:
        return "quota_percent"
    if finite_number(usage.get("credits_used") or usage.get("credit_used")) is not None:
        return "credits"
    if finite_number(usage.get("percent_used") or usage.get("percentage_used") or usage.get("used_percent")) is not None:
        return "percent"
    return None


def safe_worker_usage_source_path(raw: Any, run_json_path: Path | None) -> Path | None:
    if not isinstance(raw, str) or not raw.strip() or run_json_path is None:
        return None
    candidate = Path(raw.strip())
    if not candidate.is_absolute():
        candidate = run_json_path.parent / candidate
    try:
        resolved = candidate.resolve()
        root = run_json_path.parent.resolve()
    except OSError:
        return None
    if resolved != root and root not in resolved.parents:
        return None
    return resolved if resolved.is_file() else None


def read_worker_usage_payload(path: Path) -> dict[str, Any] | None:
    try:
        if path.suffix == ".jsonl":
            records = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            return aggregate_worker_usage_records([item for item in records if isinstance(item, dict)], default_source="zcode_worker_usage_ledger")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(payload, dict):
        if isinstance(payload.get("records"), list):
            records = [item for item in payload["records"] if isinstance(item, dict)]
            return aggregate_worker_usage_records(records, default_source=usage_source_type(payload, "zcode_worker_usage_ledger"))
        return merge_usage_payload(payload, default_source="worker_usage_sidecar")
    if isinstance(payload, list):
        return aggregate_worker_usage_records([item for item in payload if isinstance(item, dict)], default_source="zcode_worker_usage_ledger")
    return None


def aggregate_worker_usage_records(records: list[dict[str, Any]], *, default_source: str | None = None) -> dict[str, Any] | None:
    if not records:
        return None
    merged_records = [merge_usage_payload(record, default_source=default_source) for record in records]
    token_records = [record for record in merged_records if usage_sidecar_has_measured_tokens(record, require_source_type=False)]
    if token_records:
        result: dict[str, Any] = {
            "tokens_source": usage_source_type(token_records[0], default_source),
            "unit": "tokens",
        }
        provider = first_present_string(*(record.get("provider") or record.get("provider_id") for record in token_records))
        model = first_present_string(*(record.get("model") or record.get("model_id") for record in token_records))
        if provider:
            result["provider"] = provider
        if model:
            result["model"] = model
        for target, keys in {
            "total_tokens": ("total_tokens", "totalTokens"),
            "input_tokens": ("input_tokens", "inputTokens"),
            "output_tokens": ("output_tokens", "outputTokens"),
            "reasoning_tokens": ("reasoning_tokens", "reasoning_output_tokens"),
            "cache_read_tokens": ("cache_read_tokens", "cacheReadTokens"),
            "cache_write_tokens": ("cache_write_tokens", "cacheWriteTokens"),
        }.items():
            values = [token_int(first_existing(record, keys)) for record in token_records]
            if any(value is not None for value in values):
                result[target] = sum(value or 0 for value in values)
        return result
    for record in merged_records:
        if usage_has_non_token_evidence(record):
            return record
    return merged_records[-1]


def first_existing(record: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in record:
            return record[key]
    return None


def worker_usage_source_candidates(run_payload: dict[str, Any], usage: dict[str, Any], source_path: Path | None) -> list[Path]:
    candidates: list[Path] = []
    for key in WORKER_TOKEN_SOURCE_PATH_KEYS:
        for raw in (usage.get(key), run_payload.get(key)):
            path = safe_worker_usage_source_path(raw, source_path)
            if path is not None and path not in candidates:
                candidates.append(path)
    if source_path is not None:
        for name in WORKER_USAGE_SIDECAR_NAMES:
            path = source_path.parent / name
            if path.is_file() and path not in candidates:
                candidates.append(path)
    return candidates


def external_worker_usage(run_payload: dict[str, Any], usage: dict[str, Any], source_path: Path | None) -> tuple[dict[str, Any], Path] | None:
    for path in worker_usage_source_candidates(run_payload, usage, source_path):
        payload = read_worker_usage_payload(path)
        if payload is not None:
            return payload, path
    return None


def env_first_present(env: Mapping[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = env.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def env_worker_usage_source(env: Mapping[str, str]) -> tuple[Path | None, str | None]:
    for key in WORKER_USAGE_EXTERNAL_PATH_ENV_KEYS:
        raw = env.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        path = Path(raw.strip()).expanduser()
        if path.is_file():
            return path, None
        return None, f"{key.lower()}_missing"
    return None, None


def worker_usage_source_is_task_scoped(path: Path, run_json_path: Path) -> bool:
    try:
        resolved = path.resolve()
        task_root = run_json_path.parent.resolve()
    except OSError:
        return False
    return resolved == task_root or task_root in resolved.parents


def worker_usage_sidecar_target(source: Path | None, run_json_path: Path) -> Path:
    suffix = ".jsonl" if source is not None and source.suffix == ".jsonl" else ".json"
    return run_json_path.parent / f"worker-usage{suffix}"


def worker_usage_relative_path(path: Path, run_json_path: Path) -> str:
    try:
        return path.resolve().relative_to(run_json_path.parent.resolve()).as_posix()
    except (OSError, ValueError):
        return path.name


def measured_worker_usage_sidecar_payload(
    run_payload: dict[str, Any],
    usage: dict[str, Any],
    env: Mapping[str, str],
) -> dict[str, Any] | None:
    source = usage_source_type(usage)
    tokens = usage_total_tokens(usage)
    if source not in WORKER_TOKEN_SOURCE_TYPES or tokens is None:
        return None
    provider, model = zcode_worker_identity(run_payload, usage)
    usage_payload: dict[str, Any] = {"total_tokens": tokens}
    for target, keys in {
        "input_tokens": ("input_tokens", "inputTokens"),
        "output_tokens": ("output_tokens", "outputTokens"),
        "reasoning_tokens": ("reasoning_tokens", "reasoning_output_tokens"),
        "cache_read_tokens": ("cache_read_tokens", "cacheReadTokens"),
        "cache_write_tokens": ("cache_write_tokens", "cacheWriteTokens"),
    }.items():
        value = token_int(first_existing(usage, keys))
        if value is not None:
            usage_payload[target] = value
    return {
        "source_type": source,
        "provider": env_first_present(env, WORKER_USAGE_PROVIDER_ENV_KEYS) or provider,
        "model": env_first_present(env, WORKER_USAGE_MODEL_ENV_KEYS) or model,
        "unit": "tokens",
        "usage": usage_payload,
    }


def unavailable_worker_usage_sidecar_payload(
    run_payload: dict[str, Any],
    usage: dict[str, Any],
    env: Mapping[str, str],
    reason: str,
) -> dict[str, Any]:
    provider, model = zcode_worker_identity(run_payload, usage)
    return {
        "source_type": "worker_usage_sidecar",
        "status": "unavailable",
        "provider": env_first_present(env, WORKER_USAGE_PROVIDER_ENV_KEYS) or provider,
        "model": env_first_present(env, WORKER_USAGE_MODEL_ENV_KEYS) or model,
        "unit": "unknown",
        "usage": {},
        "no_usage_reason": reason,
    }


def preserve_worker_usage_sidecar(run_json_path: Path, env: Mapping[str, str] | None = None) -> Path | None:
    if not run_json_path.is_file():
        return None
    env = os.environ if env is None else env
    run_payload = read_json(run_json_path)
    usage = run_payload.get("usage_accounting") if isinstance(run_payload.get("usage_accounting"), dict) else {}
    source, source_reason = env_worker_usage_source(env)
    if source is not None and not worker_usage_source_is_task_scoped(source, run_json_path):
        source = None
        source_reason = "worker_usage_source_out_of_scope"
    existing = next((run_json_path.parent / name for name in WORKER_USAGE_SIDECAR_NAMES if (run_json_path.parent / name).is_file()), None)

    if source is not None:
        sidecar = worker_usage_sidecar_target(source, run_json_path)
        if source.resolve() != sidecar.resolve():
            shutil.copy2(source, sidecar)
    elif existing is not None:
        sidecar = existing
    else:
        sidecar = worker_usage_sidecar_target(None, run_json_path)
        payload = measured_worker_usage_sidecar_payload(run_payload, usage, env)
        if payload is None:
            reason = source_reason or usage.get("no_usage_reason") or PROVIDER_SUCCESS_WITHOUT_USAGE_PAYLOAD_REASON
            payload = unavailable_worker_usage_sidecar_payload(run_payload, usage, env, reason)
        write_json(sidecar, payload)

    usage = run_payload.get("usage_accounting") if isinstance(run_payload.get("usage_accounting"), dict) else {}
    run_payload["usage_accounting"] = usage
    usage["worker_usage_source_path"] = worker_usage_relative_path(sidecar, run_json_path)
    write_json(run_json_path, run_payload)
    return sidecar


def stable_json_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def hash_tree(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_file():
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        return digest.hexdigest()
    for item in sorted(path.rglob("*")):
        if not item.is_file() or ignored_workspace_path(item):
            continue
        rel = item.relative_to(path).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def command_text(args: list[str], cwd: Path) -> str:
    try:
        result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"unavailable:{type(exc).__name__}"
    text = (result.stdout or result.stderr or "").strip()
    return text or f"rc={result.returncode}"


def dirty_diff_hash(report_dir: Path) -> str:
    text = (report_dir / "dirty-diff.sha256").read_text(encoding="utf-8").strip()
    tracked_diff_sha = text.split()[0] if text.split() else ""
    status_short = (report_dir / "dirty-status.txt").read_text(encoding="utf-8").splitlines()
    return stable_json_hash({"tracked_diff_sha256": tracked_diff_sha, "status_short": status_short})


def environment_fingerprint() -> str:
    payload = {
        "platform": platform.platform(),
        "python": sys.version,
        "node": command_text(["node", "--version"], REPO_ROOT),
        "npm": command_text(["npm", "--version"], REPO_ROOT),
    }
    return stable_json_hash(payload)


def baseline_runtime_components(args: argparse.Namespace, report_dir: Path) -> dict[str, Any]:
    return {
        "repo_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip(),
        "dirty_tree_sha256": dirty_diff_hash(report_dir),
        "strict_acceptance_version": hashlib.sha256((REPO_ROOT / "tools/zcode_eval/strict_contract.py").read_bytes()).hexdigest(),
        "model_id": args.model_id or os.environ.get("CODEX_MODEL") or "codex-cli-default",
        "codex_cli_version": command_text(["codex", "--version"], REPO_ROOT),
        "harness_version": HARNESS_VERSION,
        "environment_fingerprint": environment_fingerprint(),
    }


def baseline_cache_components(task: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    return {
        **runtime,
        "task_id": task["slug"],
        "fixture_hash": hash_tree(Path(task["source"])),
        "direct_prompt_hash": sha256_text(task["direct_prompt"]),
        "system_prompt_hash": sha256_text("codex-cli-default-system-prompt-unavailable"),
        "allowed_files_hash": stable_json_hash([task["allowed"]]),
        "validation_command_hash": sha256_text(task["validation"]),
    }


def baseline_cache_key(components: dict[str, Any]) -> str:
    return stable_json_hash(components)


def clean_git_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    child_env = dict(os.environ if env is None else env)
    for name in GIT_CONTROL_ENV_VARS:
        child_env.pop(name, None)
    return child_env


def run_command(
    args: list[str],
    *,
    cwd: Path,
    stdout: Path | None = None,
    stderr: Path | None = None,
    stdin: Path | None = None,
    timeout: int | None = None,
) -> CommandResult:
    start = time.monotonic()
    same_log = stdout is not None and stderr is not None and stdout == stderr
    out_handle = stdout.open("w", encoding="utf-8") if stdout else subprocess.DEVNULL
    in_handle = stdin.open("r", encoding="utf-8") if stdin else None
    err_handle = subprocess.STDOUT if same_log else (
        stderr.open("w", encoding="utf-8") if stderr else subprocess.DEVNULL
    )
    proc = subprocess.Popen(
        args,
        cwd=cwd,
        stdin=in_handle,
        stdout=out_handle,
        stderr=err_handle,
        env=clean_git_env(os.environ),
        text=True,
        start_new_session=True,
    )
    timed_out = False
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            rc = proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            rc = proc.wait()
    finally:
        if stdout:
            out_handle.close()
        if stdin:
            in_handle.close()
        if stderr and not same_log:
            err_handle.close()
    return CommandResult(rc=rc, seconds=round(time.monotonic() - start, 3), timed_out=timed_out)


def copy_fixture(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("node_modules", ".git", ".codex"))


def selected_tasks(tasks: list[dict[str, Any]], only: str | None) -> list[dict[str, Any]]:
    if not only:
        return tasks
    wanted = {item.strip() for item in only.split(",") if item.strip()}
    selected = [task for task in tasks if task["slug"] in wanted]
    missing = sorted(wanted - {task["slug"] for task in selected})
    if missing:
        raise ValueError(f"unknown task slug(s): {', '.join(missing)}")
    return selected


def launcher_script(
    task: dict[str, Any],
    workspace: Path,
    task_dir: Path,
    *,
    worker_finalization: str = "zcode_owned",
) -> Path:
    task_dir.mkdir(parents=True, exist_ok=True)
    if worker_finalization not in WORKER_FINALIZATION_MODES:
        raise ValueError(f"unknown worker_finalization: {worker_finalization}")
    script = task_dir / "run_zcode_launcher.sh"
    packet_args = []
    for expected in task["expected_outputs"]:
        packet_args.append(f"  --expected-output {json.dumps(expected)} \\")
    for criterion in task["acceptance"]:
        packet_args.append(f"  --acceptance-criterion {json.dumps(criterion)} \\")

    vision_args = ""
    run_vision = ""
    if task.get("image"):
        vision_args = "  --vision-image screenshots/target-card.png \\\n  --vision-required \\\n"
        for sample in task.get("vision_color_samples", []):
            vision_args += f"  --vision-color-sample {json.dumps(sample)} \\\n"
        run_vision = "  --vision-preflight off \\\n"

    contract_mode = task.get("contract_mode", "expanded_rubric")
    if contract_mode == "compact_capsule":
        compact_contract = task_dir / "compact-contract-capsule.json"
        write_json(compact_contract, compact_policy_contract(task))
        strict_args = (
            f"  --task-contract {compact_contract} \\\n"
            f"  --task-contract-out {task_dir}/task_contract.json \\"
        )
    else:
        strict_args = (
            f"  --strict-contract-rubric-id {task['strict_rubric']} \\\n"
            f"  --strict-contract-task-id {task['slug']} \\\n"
            f"  --strict-contract-risk-level {task['strict_risk']} \\\n"
            f"  --task-contract-out {task_dir}/task_contract.json \\"
        )
    accept_validated_artifact_after_ms = 1000 if worker_finalization == "supervisor_owned" else 60000

    content = f"""#!/usr/bin/env bash
set -uo pipefail
mkdir -p {task_dir}
printf '%s\\n' {json.dumps(contract_mode)} > {task_dir}/contract-mode.txt
python3 {REPO_ROOT}/tools/zcode_supervisor/zcode_supervisor.py install-repo --repo {workspace} --skip-vision-mcp > {task_dir}/install.json 2> {task_dir}/install.stderr.log
printf '%s\\n' "$?" > {task_dir}/install.rc

python3 {REPO_ROOT}/tools/zcode_supervisor/zcode_supervisor.py packet \\
  --workspace {workspace} \\
  --objective {json.dumps(task["objective"])} \\
  --allowed {task["allowed"]} \\
  --forbidden test \\
  --forbidden README.md \\
  --forbidden package.json \\
  --forbidden validate.mjs \\
  --validation {json.dumps(task["validation"])} \\
  --mode "Auto Edit" \\
  --workspace-kind fixture \\
  --effort max \\
  --task-class {task["task_class"]} \\
  --risk-budget low \\
  --max-changed-files 1 \\
  --worker-finalization {worker_finalization} \\
{strict_args}
{vision_args}{os.linesep.join(packet_args)}
  --what-not-to-do {json.dumps(task["what_not_to_do"])} \\
  --final-report-line "Changed files" \\
  --final-report-line "Validation result" \\
  --out {task_dir}/packet.json \\
  --prompt-out {task_dir}/packet.prompt.txt \\
  > {task_dir}/packet.stdout.log 2> {task_dir}/packet.stderr.log
printf '%s\\n' "$?" > {task_dir}/packet.rc

unset ZCODE_WORKER_USAGE_SIDECAR ZCODE_WORKER_USAGE_LEDGER ZCODE_USAGE_LEDGER
export ZCODE_PROVIDER_USAGE_LEDGER={task_dir}/worker-usage.jsonl

python3 {REPO_ROOT}/tools/zcode_eval/zcode_model_usage_db_delta.py before \\
  --out {task_dir}/{ZCODE_MODEL_USAGE_DB_DELTA_BEFORE} \\
  > {task_dir}/zcode-model-usage-before.stdout.log 2> {task_dir}/zcode-model-usage-before.stderr.log
printf '%s\\n' "$?" > {task_dir}/zcode-model-usage-before.rc

node {REPO_ROOT}/tools/zcode_control/zcodectl.mjs run-packet \\
  --packet {task_dir}/packet.json \\
  --mode edit \\
  --max-attempts 1 \\
  --retry-delay-ms 60000 \\
  --timeout-ms {int(task["zcode_timeout_ms"])} \\
  --validation-timeout 60 \\
  --usage-snapshot-source none \\
  --no-repair-validation \\
  --accept-validated-artifact-after-ms {accept_validated_artifact_after_ms} \\
{run_vision}  --json \\
  --out {task_dir}/zcode-run.json \\
  > {task_dir}/route.json 2> {task_dir}/route.stderr.log
printf '%s\\n' "$?" > {task_dir}/route.rc
printf '%s\\n' {task_dir}/zcode-run.json > {task_dir}/run-json-path.txt

python3 {REPO_ROOT}/tools/zcode_eval/zcode_model_usage_db_delta.py after \\
  --before {task_dir}/{ZCODE_MODEL_USAGE_DB_DELTA_BEFORE} \\
  --ledger {task_dir}/worker-usage.jsonl \\
  --row-dir {task_dir} \\
  --out {task_dir}/{ZCODE_MODEL_USAGE_DB_DELTA_AFTER} \\
  > {task_dir}/zcode-model-usage-delta.stdout.log 2> {task_dir}/zcode-model-usage-delta.stderr.log
printf '%s\\n' "$?" > {task_dir}/zcode-model-usage-delta.rc

# Worker usage sidecar hook:
# - This launcher exports ZCODE_PROVIDER_USAGE_LEDGER to a row-scoped
#   worker-usage.jsonl path below this delegated task directory. Providers or
#   wrappers that support the hook should append compact token JSONL there.
# - The non-live zcode_cli_model_usage_db_delta helper captures max(rowid)
#   before the row and appends only attributable positive token DB deltas. A
#   single delegated row may aggregate multiple compatible model_usage rows.
# - preserve_worker_usage_sidecar also honors ZCODE_WORKER_USAGE_SIDECAR,
#   ZCODE_WORKER_USAGE_LEDGER, and ZCODE_USAGE_LEDGER when callers set them to
#   task-scoped paths. Out-of-scope/global ledgers are recorded as
#   unavailable, not copied.
# - If zcode-run.json already has measured zcode_cli_json_usage, this writes a
#   worker-usage.json sidecar from it.
# - zcode-run.json records usage_accounting.worker_usage_source_path as a
#   stable relative path when worker-usage.json or worker-usage.jsonl exists.
python3 - <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, {json.dumps(str(REPO_ROOT))})
from tools.zcode_eval.strict_contract_comparison import preserve_worker_usage_sidecar
preserve_worker_usage_sidecar(Path({json.dumps(str(task_dir / "zcode-run.json"))}))
PY
printf '%s\\n' "$?" > {task_dir}/worker-usage-sidecar.rc

python3 {REPO_ROOT}/tools/zcode_eval/zcode_eval.py accept-zcode-artifact \\
  --zcode-run-json {task_dir}/zcode-run.json \\
  --label {task["slug"]} \\
  > {task_dir}/acceptance.json 2> {task_dir}/acceptance.stderr.log
printf '%s\\n' "$?" > {task_dir}/acceptance.rc

(cd {workspace} && {task["validation"]}) > {task_dir}/final-validation.log 2>&1
printf '%s\\n' "$?" > {task_dir}/final-validation.rc
"""
    write_text(script, content)
    script.chmod(0o755)
    return script


def prepare_workspace(report_dir: Path, task: dict[str, Any], mode: str) -> tuple[Path, Path]:
    workspace = report_dir / "workspaces" / task["slug"] / mode
    base = report_dir / "workspaces" / task["slug"] / f"{mode}-base"
    copy_fixture(Path(task["source"]), workspace)
    copy_fixture(Path(task["source"]), base)
    return workspace, base


def changed_files(base: Path, workspace: Path) -> list[str]:
    files: set[str] = set()
    for path in workspace.rglob("*"):
        if path.is_file() and not ignored_workspace_path(path):
            files.add(path.relative_to(workspace).as_posix())
    for path in base.rglob("*"):
        if path.is_file() and not ignored_workspace_path(path):
            files.add(path.relative_to(base).as_posix())
    changed = []
    for rel in sorted(files):
        left = base / rel
        right = workspace / rel
        if not left.exists() or not right.exists() or left.read_bytes() != right.read_bytes():
            changed.append(rel)
    return changed


def ignored_workspace_path(path: Path) -> bool:
    return any(part in {".git", ".codex", "node_modules"} for part in path.parts)


def line_delta(base: Path, workspace: Path, rel: str) -> dict[str, Any]:
    left = (base / rel).read_text(encoding="utf-8", errors="replace").splitlines() if (base / rel).exists() else []
    right = (workspace / rel).read_text(encoding="utf-8", errors="replace").splitlines() if (workspace / rel).exists() else []
    added = deleted = 0
    for line in difflib.unified_diff(left, right, lineterm=""):
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            deleted += 1
    return {"file": rel, "added": added, "deleted": deleted}


def usage_from_events(events: Path) -> dict[str, Any] | None:
    if not events.exists() or events.stat().st_size == 0:
        return None
    parsed = parse_codex_exec_jsonl([events])
    return None if parsed.usage.get("usage_missing") else parsed.usage


def zcode_worker_usage_fields(run_payload: dict[str, Any], source_path: Path | None = None) -> dict[str, Any]:
    usage = run_payload.get("usage_accounting") if isinstance(run_payload.get("usage_accounting"), dict) else {}
    tokens = usage_total_tokens(usage)
    base = zcode_worker_base_fields(run_payload, usage, source_path)
    source = usage_source_type(usage)
    if tokens is not None and source in WORKER_TOKEN_SOURCE_TYPES:
        return measured_zcode_worker_fields(base, tokens, method=source or "zcode_cli_json_usage")

    external = external_worker_usage(run_payload, usage, source_path)
    if external is not None:
        external_usage, external_path = external
        external_base = zcode_worker_base_fields(run_payload, external_usage, external_path)
        external_source = usage_source_type(external_usage, "worker_usage_sidecar")
        if usage_empty_sidecar_payload(external_usage):
            fields = unavailable_zcode_worker_fields(
                external_base,
                empty_sidecar_no_usage_reason(external_usage),
                method=external_source or "worker_usage_sidecar",
            )
            fields["worker_usage_empty_sidecar"] = True
            return fields
        external_tokens = usage_explicit_total_tokens(external_usage)
        if usage_sidecar_has_measured_tokens(external_usage) and external_source in WORKER_TOKEN_SOURCE_TYPES and external_tokens is not None:
            return measured_zcode_worker_fields(external_base, external_tokens, method=external_source or "worker_usage_sidecar")
        non_token_unit = usage_has_non_token_evidence(external_usage)
        if non_token_unit is not None:
            reason = external_usage.get("no_usage_reason") or f"worker_token_usage_unavailable_{non_token_unit}_only"
            method = "usage_snapshot_delta" if non_token_unit == "quota_percent" else "provider_credit_delta"
            return partial_zcode_worker_fields(external_base, unit=non_token_unit, method=method, reason=reason)

    if base["worker_quota_percent_used"] is not None:
        reason = usage.get("no_usage_reason") or "worker_token_usage_unavailable_quota_percent_only"
        return partial_zcode_worker_fields(base, unit="quota_percent", method="usage_snapshot_delta", reason=reason)
    if base["worker_credits_used"] is not None:
        reason = usage.get("no_usage_reason") or "worker_token_usage_unavailable_credits_only"
        return partial_zcode_worker_fields(base, unit="credits", method="provider_credit_delta", reason=reason)
    reason = usage.get("no_usage_reason") or PROVIDER_SUCCESS_WITHOUT_USAGE_PAYLOAD_REASON
    return unavailable_zcode_worker_fields(base, reason)


def run_validation(workspace: Path, command: str, log: Path) -> CommandResult:
    return run_command(["bash", "-lc", command], cwd=workspace, stdout=log, stderr=log, timeout=120)


def codex_exec_args(
    *,
    workspace: Path,
    task_dir: Path,
    schema: Path,
    image: Path | None = None,
    sandbox: str = "danger-full-access",
    writable_dirs: list[Path] | None = None,
) -> list[str]:
    args = [
        "codex",
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--sandbox",
        sandbox,
        "-C",
        str(workspace),
        "--output-schema",
        str(schema),
        "-o",
        str(task_dir / "codex-final.txt"),
    ]
    for directory in writable_dirs or []:
        args.extend(["--add-dir", str(directory)])
    if image:
        args.extend(["--image", str(image)])
    return args


def run_codex(
    prompt: str,
    workspace: Path,
    task_dir: Path,
    *,
    task: str,
    arm: str,
    phase: str,
    component: str,
    schema: Path,
    image: Path | None = None,
    timeout: int = 900,
    sandbox: str = "danger-full-access",
    writable_dirs: list[Path] | None = None,
) -> tuple[CommandResult, dict[str, Any]]:
    prompt_file = task_dir / "prompt.txt"
    events = task_dir / "codex-events.jsonl"
    final_prompt = f"{prompt.rstrip()}\n\n{fixed_json_instruction(task, arm, phase)}\n"
    write_text(prompt_file, final_prompt)
    args = codex_exec_args(
        workspace=workspace,
        task_dir=task_dir,
        schema=schema,
        image=image,
        sandbox=sandbox,
        writable_dirs=writable_dirs,
    )
    result = run_command(args, cwd=workspace, stdout=events, stderr=task_dir / "codex-stderr.log", stdin=prompt_file, timeout=timeout)
    write_text(task_dir / "codex.rc", f"{result.rc}\n")
    write_text(task_dir / "timing.json", json.dumps({"duration_seconds": result.seconds, "timed_out": result.timed_out}) + "\n")
    usage = usage_from_events(events)
    exec_row = build_codex_exec_row(
        task=task,
        arm=arm,
        phase=phase,
        component=component,
        prompt=final_prompt,
        events_path=events,
        output_path=task_dir / "codex-final.txt",
        result=result,
        usage=usage,
    )
    write_json(task_dir / "codex-exec-row.json", exec_row)
    return result, exec_row


def codex_only(report_dir: Path, task: dict[str, Any]) -> dict[str, Any]:
    workspace, base = prepare_workspace(report_dir, task, "codex-only")
    task_dir = report_dir / "tasks" / task["slug"] / "codex-only"
    task_dir.mkdir(parents=True, exist_ok=True)
    result, exec_row = run_codex(
        task["direct_prompt"],
        workspace,
        task_dir,
        task=task["slug"],
        arm="codex_only",
        phase="implementation",
        component="codex_implementation",
        schema=ensure_codex_output_schema(report_dir),
        image=workspace / task["image"] if task.get("image") else None,
    )
    validation = run_validation(workspace, task["validation"], task_dir / "validation.log")
    write_text(task_dir / "validation.rc", f"{validation.rc}\n")
    changed = changed_files(base, workspace)
    usage = usage_from_events(task_dir / "codex-events.jsonl")
    scope_ok = changed == [task["allowed"]]
    quality = result.rc == 0 and validation.rc == 0 and scope_ok and not result.timed_out and not validation.timed_out
    return {
        "task": task["slug"],
        "kind": task["kind"],
        "mode": "codex_only",
        "quality": "pass" if quality else "fail",
        "allowed_file": task["allowed"],
        "codex_rc": result.rc,
        "duration_seconds": result.seconds,
        "timed_out": result.timed_out,
        "validation_rc": validation.rc,
        "validation_seconds": validation.seconds,
        "changed_files": changed,
        "scope_ok": scope_ok,
        "diff": [line_delta(base, workspace, rel) for rel in changed],
        "usage_status": "measured" if usage else "unavailable",
        "usage": usage or unavailable_usage_metrics("codex_exec_phase_usage_missing"),
        "codex_exec_rows": [exec_row],
        "workspace": str(workspace),
    }


def zcode_delegated(
    report_dir: Path,
    task: dict[str, Any],
    *,
    worker_finalization: str = "zcode_owned",
) -> dict[str, Any]:
    workspace, base = prepare_workspace(report_dir, task, "zcode-delegated")
    task_dir = report_dir / "tasks" / task["slug"] / "zcode-delegated"
    task_dir.mkdir(parents=True, exist_ok=True)
    script = launcher_script(task, workspace, task_dir, worker_finalization=worker_finalization)
    prompt = (
        "Benchmark launcher task. Run the provided ZCode launcher script exactly once, "
        "then inspect only the generated .rc files and report route.rc, acceptance.rc, "
        "and final-validation.rc. Do not edit implementation files yourself.\n\n"
        f"Script: {script}\n"
    )
    result, exec_row = run_codex(
        prompt,
        workspace,
        task_dir,
        task=task["slug"],
        arm="zcode_delegated",
        phase="launcher_orchestration",
        component="codex_launcher_orchestration",
        schema=ensure_codex_output_schema(report_dir),
    )
    changed = changed_files(base, workspace)
    usage = usage_from_events(task_dir / "codex-events.jsonl")
    run_payload = read_json(task_dir / "zcode-run.json")
    audit = run_payload.get("audit") if isinstance(run_payload.get("audit"), dict) else {}
    strict = audit.get("strict_contract") if isinstance(audit.get("strict_contract"), dict) else {}
    acceptance = read_json(task_dir / "acceptance.json").get("acceptance", {})
    task_contract = read_json(task_dir / "task_contract.json")
    task_contract_allowed = task_contract.get("allowed_files") if isinstance(task_contract.get("allowed_files"), list) else []
    task_contract_forbidden = task_contract.get("forbidden_files") if isinstance(task_contract.get("forbidden_files"), list) else []
    route_rc = read_rc(task_dir / "route.rc")
    acceptance_rc = read_rc(task_dir / "acceptance.rc")
    final_rc = read_rc(task_dir / "final-validation.rc")
    scope_ok = changed == [task["allowed"]]
    accepted = bool(acceptance.get("ok"))
    quality = (
        result.rc == 0
        and route_rc == 0
        and acceptance_rc == 0
        and final_rc == 0
        and scope_ok
        and accepted
        and strict.get("accepted") is True
        and not result.timed_out
    )
    row = {
        "task": task["slug"],
        "kind": task["kind"],
        "mode": "zcode_delegated",
        "worker_finalization": worker_finalization,
        "quality": "pass" if quality else "fail",
        "allowed_file": task["allowed"],
        "codex_rc": result.rc,
        "duration_seconds": result.seconds,
        "timed_out": result.timed_out,
        "route_rc": route_rc,
        "acceptance_rc": acceptance_rc,
        "final_validation_rc": final_rc,
        "changed_files": changed,
        "scope_ok": scope_ok,
        "diff": [line_delta(base, workspace, rel) for rel in changed],
        "usage_status": "measured" if usage else "unavailable",
        "usage": usage or unavailable_usage_metrics("codex_exec_phase_usage_missing"),
        "codex_token_usage_scope": "codex_launcher_orchestration",
        "codex_exec_rows": [exec_row],
        "zcode_acceptance": acceptance,
        "strict_contract": strict,
        "task_contract_allowed_files": task_contract_allowed,
        "task_contract_forbidden_files": task_contract_forbidden,
        "contract_visible_bytes": len(json.dumps(task_contract, sort_keys=True, separators=(",", ":")).encode("utf-8")) if task_contract else None,
        "self_audit_coverage": strict.get("trace_coverage", {}),
        "strict_accepted": strict.get("accepted"),
        "strict_violations": strict.get("violations", []),
        "workspace": str(workspace),
    }
    row.update(provider_blocker_fields(run_payload, quality=quality))
    row.update(zcode_worker_usage_fields(run_payload, task_dir / "zcode-run.json"))
    return row


def zcode_direct_launcher(
    report_dir: Path,
    task: dict[str, Any],
    *,
    worker_finalization: str = "zcode_owned",
) -> dict[str, Any]:
    workspace, base = prepare_workspace(report_dir, task, "zcode-direct-launcher")
    task_dir = report_dir / "tasks" / task["slug"] / "zcode-direct-launcher"
    task_dir.mkdir(parents=True, exist_ok=True)
    script = launcher_script(task, workspace, task_dir, worker_finalization=worker_finalization)
    result = run_command(
        ["bash", str(script)],
        cwd=workspace,
        stdout=task_dir / "direct-launcher.stdout.log",
        stderr=task_dir / "direct-launcher.stderr.log",
        timeout=max(int(task["zcode_timeout_ms"] / 1000) + 180, 900),
    )
    write_text(task_dir / "direct-launcher.rc", f"{result.rc}\n")
    write_text(task_dir / "timing.json", json.dumps({"duration_seconds": result.seconds, "timed_out": result.timed_out}) + "\n")
    changed = changed_files(base, workspace)
    run_payload = read_json(task_dir / "zcode-run.json")
    audit = run_payload.get("audit") if isinstance(run_payload.get("audit"), dict) else {}
    strict = audit.get("strict_contract") if isinstance(audit.get("strict_contract"), dict) else {}
    acceptance = read_json(task_dir / "acceptance.json").get("acceptance", {})
    task_contract = read_json(task_dir / "task_contract.json")
    task_contract_allowed = task_contract.get("allowed_files") if isinstance(task_contract.get("allowed_files"), list) else []
    task_contract_forbidden = task_contract.get("forbidden_files") if isinstance(task_contract.get("forbidden_files"), list) else []
    route_rc = read_rc(task_dir / "route.rc")
    acceptance_rc = read_rc(task_dir / "acceptance.rc")
    final_rc = read_rc(task_dir / "final-validation.rc")
    scope_ok = changed == [task["allowed"]]
    accepted = bool(acceptance.get("ok"))
    quality = (
        result.rc == 0
        and route_rc == 0
        and acceptance_rc == 0
        and final_rc == 0
        and scope_ok
        and accepted
        and strict.get("accepted") is True
        and not result.timed_out
    )
    row = {
        "task": task["slug"],
        "kind": task["kind"],
        "mode": "zcode_direct_launcher",
        "worker_finalization": worker_finalization,
        "quality": "pass" if quality else "fail",
        "allowed_file": task["allowed"],
        "launcher_rc": result.rc,
        "duration_seconds": result.seconds,
        "timed_out": result.timed_out,
        "route_rc": route_rc,
        "acceptance_rc": acceptance_rc,
        "final_validation_rc": final_rc,
        "changed_files": changed,
        "scope_ok": scope_ok,
        "diff": [line_delta(base, workspace, rel) for rel in changed],
        "usage_status": "not_applicable",
        "usage_zero_reason": "no_codex_exec_in_non_llm_direct_launcher",
        "usage": zero_usage_metrics(),
        "codex_token_usage_scope": "non_llm_direct_launcher",
        "codex_exec_rows": [],
        "zcode_acceptance": acceptance,
        "strict_contract": strict,
        "task_contract_allowed_files": task_contract_allowed,
        "task_contract_forbidden_files": task_contract_forbidden,
        "contract_visible_bytes": len(json.dumps(task_contract, sort_keys=True, separators=(",", ":")).encode("utf-8")) if task_contract else None,
        "self_audit_coverage": strict.get("trace_coverage", {}),
        "strict_accepted": strict.get("accepted"),
        "strict_violations": strict.get("violations", []),
        "workspace": str(workspace),
    }
    row.update(provider_blocker_fields(run_payload, quality=quality))
    row.update(zcode_worker_usage_fields(run_payload, task_dir / "zcode-run.json"))
    return row


def read_rc(path: Path) -> int:
    if not path.exists():
        return 999
    text = path.read_text(encoding="utf-8").strip()
    return int(text or "999")


def reusable_codex_rows(path: Path | None, task_slugs: set[str]) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    rows = read_json(path).get("rows", [])
    result = {row["task"]: row for row in rows if row.get("mode") == "codex_only" and row.get("task") in task_slugs}
    missing = sorted(task_slugs - set(result))
    if missing:
        raise ValueError(f"missing reusable codex_only rows for: {', '.join(missing)}")
    return result


def auto_reusable_codex_rows(report_dir: Path, key_by_task: dict[str, str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    summaries = sorted(report_dir.parent.glob("*/summary.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for summary_path in summaries:
        if report_dir in summary_path.parents:
            continue
        rows = read_json(summary_path).get("rows", [])
        if not isinstance(rows, list):
            continue
        for row in rows:
            task = row.get("task")
            if row.get("mode") != "codex_only" or task not in key_by_task or task in result:
                continue
            if row.get("baseline_cache_key") == key_by_task[task]:
                result[task] = row
        if len(result) == len(key_by_task):
            break
    return result


def prepare_reused_baseline_row(
    row: dict[str, Any],
    *,
    measurement_mode: str,
    cache_key: str | None,
    verified_cache_hit: bool,
    reuse_mode: str,
) -> dict[str, Any]:
    copy_row = copy.deepcopy(row)
    return apply_report_schema(
        copy_row,
        measurement_mode=measurement_mode,
        fresh_run=False,
        baseline_cache_hit=verified_cache_hit,
        baseline_cache_key=cache_key or copy_row.get("baseline_cache_key"),
        baseline_reuse_mode=reuse_mode,
    )


def run_comparison(args: argparse.Namespace) -> int:
    report_dir = args.report_dir.resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    run_command(["git", "status", "--short"], cwd=REPO_ROOT, stdout=report_dir / "dirty-status.txt", stderr=report_dir / "dirty-status.stderr.log", timeout=30)
    run_command(["bash", "-lc", "git diff --binary | shasum -a 256"], cwd=REPO_ROOT, stdout=report_dir / "dirty-diff.sha256", stderr=report_dir / "dirty-diff.stderr.log", timeout=60)

    policy_mode = args.policy_contract_mode.replace("-", "_")
    tasks = selected_tasks(task_specs(report_dir, policy_contract_mode=policy_mode), args.only)
    runtime_components = baseline_runtime_components(args, report_dir)
    cache_components_by_task = {task["slug"]: baseline_cache_components(task, runtime_components) for task in tasks}
    cache_key_by_task = {task: baseline_cache_key(components) for task, components in cache_components_by_task.items()}
    ensure_codex_output_schema(report_dir)
    write_text(report_dir / "task-plan.json", json.dumps(tasks, indent=2, sort_keys=True) + "\n")
    if args.dry_run:
        launcher_mode = "zcode-direct-launcher" if args.delegation_execution == "direct" else "zcode-delegated"
        for task in tasks:
            workspace = report_dir / "workspaces" / task["slug"] / launcher_mode
            task_dir = report_dir / "tasks" / task["slug"] / launcher_mode
            copy_fixture(Path(task["source"]), workspace)
            launcher_script(task, workspace, task_dir, worker_finalization=args.worker_finalization)
        print(report_dir)
        return 0

    if args.reuse_codex_baseline_from and args.reuse_codex_baseline_auto:
        raise ValueError("--reuse-codex-baseline-from and --reuse-codex-baseline-auto are mutually exclusive")
    manual_baseline_rows = reusable_codex_rows(args.reuse_codex_baseline_from, {task["slug"] for task in tasks})
    auto_baseline_rows = auto_reusable_codex_rows(report_dir, cache_key_by_task) if args.reuse_codex_baseline_auto else {}
    rows: list[dict[str, Any]] = []
    for task in tasks:
        task_id = task["slug"]
        cache_key = cache_key_by_task[task_id]
        if task_id in manual_baseline_rows:
            print(f"[codex-only:reused] {task['slug']}", flush=True)
            rows.append(
                prepare_reused_baseline_row(
                    manual_baseline_rows[task_id],
                    measurement_mode=args.delegation_execution,
                    cache_key=None,
                    verified_cache_hit=False,
                    reuse_mode="manual",
                )
            )
        elif task_id in auto_baseline_rows:
            print(f"[codex-only:auto-cache-hit] {task['slug']}", flush=True)
            rows.append(
                prepare_reused_baseline_row(
                    auto_baseline_rows[task_id],
                    measurement_mode=args.delegation_execution,
                    cache_key=cache_key,
                    verified_cache_hit=True,
                    reuse_mode="auto",
                )
            )
        else:
            print(f"[codex-only] {task['slug']}", flush=True)
            row = codex_only(report_dir, task)
            row["baseline_cache_key_components"] = cache_components_by_task[task_id]
            rows.append(
                apply_report_schema(
                    row,
                    measurement_mode=args.delegation_execution,
                    fresh_run=True,
                    baseline_cache_hit=False,
                    baseline_cache_key=cache_key,
                )
            )
        write_text(report_dir / "summary.json", json.dumps({"rows": rows}, indent=2, sort_keys=True) + "\n")
        if args.delegation_execution == "direct":
            print(f"[zcode-direct-launcher] {task['slug']}", flush=True)
            delegated_row = zcode_direct_launcher(
                report_dir,
                task,
                worker_finalization=args.worker_finalization,
            )
        else:
            print(f"[zcode-delegated] {task['slug']}", flush=True)
            delegated_row = zcode_delegated(
                report_dir,
                task,
                worker_finalization=args.worker_finalization,
            )
        rows.append(
            apply_report_schema(
                delegated_row,
                measurement_mode=args.delegation_execution,
                fresh_run=True,
                baseline_cache_hit=False,
                baseline_cache_key=None,
            )
        )
        summary = summarize(report_dir, rows, REPO_ROOT)
        write_text(report_dir / "summary.json", json.dumps(summary, indent=2, sort_keys=True) + "\n")
        write_markdown(report_dir, summary)
    print(report_dir / "summary.md", flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    epilog = """\
Direct delegated examples:
  python3 tools/zcode_eval/strict_contract_comparison.py \\
    --delegation-execution direct \\
    --only billing-credit-contract,vision-card-latest \\
    --dry-run

  python3 tools/zcode_eval/strict_contract_comparison.py \\
    --delegation-execution direct \\
    --only billing-credit-contract,ledger-summary-contract,policy-reason-contract,vision-card-latest

Operational wrapper:
  bash scripts/run_direct_delegated_strict_contract.sh \\
    --only billing-credit-contract,vision-card-latest \\
    --dry-run

Direct mode claim family: direct_orchestrated_delegation_savings.
Codex-mediated claim family: codex_mediated_delegation_savings.
Do not mix direct and codex-mediated rows into one deployable claim.
In direct mode, 0 effective_codex_work means Codex-side launcher work only,
not total workflow cost and not ZCode worker/model usage.
Unavailable ZCode worker usage remains unavailable/null with no_usage_reason.
Production green-path skip remains disabled.
"""
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--report-dir", type=Path, default=default_report_dir())
    parser.add_argument("--only", help="Comma-separated task slugs. Defaults to all tasks.")
    parser.add_argument("--reuse-codex-baseline-from", type=Path, help="Reuse codex_only rows from a previous summary.json.")
    parser.add_argument("--reuse-codex-baseline-auto", action="store_true", help="Opt in to verified Codex-only baseline reuse by cache key.")
    parser.add_argument(
        "--delegation-execution",
        choices=("codex-mediated", "direct"),
        default="codex-mediated",
        help="Delegated arm execution mode. Default remains codex-mediated; direct is explicit opt-in.",
    )
    parser.add_argument("--model-id", help="Model identity used for baseline cache keys. Does not override Codex CLI config.")
    parser.add_argument("--policy-contract-mode", choices=("expanded-rubric", "compact-capsule"), default="compact-capsule")
    parser.add_argument(
        "--worker-finalization",
        choices=WORKER_FINALIZATION_MODES,
        default="zcode_owned",
        help="Opt-in worker finalization owner. Default zcode_owned preserves current packet behavior.",
    )
    parser.add_argument("--skip-direct-launcher", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true", help="Write task plan and launcher scripts without running Codex/ZCode.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_comparison(args)


if __name__ == "__main__":
    sys.exit(main())
