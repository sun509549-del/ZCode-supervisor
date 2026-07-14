"""Codex context-intake ledger capture and JSONL tool-output accounting."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import stat
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .codex_usage import append_jsonl_record, non_negative_int, positive_int, sha256_bytes
    from .mode_taxonomy import CANONICAL_MODES, normalize_mode
except ImportError:  # pragma: no cover - direct script execution
    from codex_usage import append_jsonl_record, non_negative_int, positive_int, sha256_bytes
    from mode_taxonomy import CANONICAL_MODES, normalize_mode

SCHEMA_VERSION = "codex_context_intake.v1"
DEFAULT_CONTEXT_LEDGER = Path("artifacts/evals/codex-context-intake-ledger.jsonl")
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
    "unknown",
)
RUN_KIND_CHOICES = ("production", "measurement", "noop_baseline")
IMAGE_ARG_STYLES = ("none", "repeated_flags", "comma_list", "mixed", "unknown")
PROMPT_SOURCES = ("argument", "stdin", "file", "unknown")
SECRET_PATH_NEEDLES = (".env", "id_rsa", "id_ed25519", ".ssh", "credential", "credentials")
ARGV_VALUE_FLAGS = {
    "--image",
    "--model",
    "-m",
    "--profile",
    "--sandbox",
    "--output-schema",
    "--config",
    "--cd",
    "--cwd",
    "--reasoning-effort",
    "--approval-mode",
}
GREEN_PATH_FORBIDDEN = ("git diff", "cat", "sed", "grep", "pytest", "validation-log", "validation.log")
DEFAULT_BUDGETS = {
    "prompt_max_bytes": 12000,
    "task_packet_max_bytes": 6000,
    "zcode_manifest_max_bytes": 4000,
    "diff_excerpt_max_bytes": 2048,
    "validation_excerpt_max_bytes": 4096,
    "command_output_max_bytes": 4096,
    "image_resend_allowed": False,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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


def path_file_mode(path: Path | None) -> str:
    if path is None or not path.exists():
        return "unavailable"
    return format(stat.S_IMODE(path.stat().st_mode), "04o")


def gitignored(path: Path, cwd: Path) -> bool:
    if not path.exists():
        return False
    try:
        rel = path.resolve().relative_to(cwd.resolve())
    except ValueError:
        return False
    try:
        result = subprocess.run(["git", "check-ignore", "-q", str(rel)], cwd=cwd, capture_output=True, check=False, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def prompt_hash_and_bytes(prompt_text: str | None, prompt_file: Path | None) -> tuple[str | None, int]:
    if prompt_file:
        fingerprint = hash_file(prompt_file)
        return (fingerprint["sha256"], fingerprint["bytes"]) if fingerprint else (None, 0)
    if prompt_text is None:
        return None, 0
    data = prompt_text.encode("utf-8")
    return hashlib.sha256(data).hexdigest(), len(data)


def stdin_hash_and_bytes(stdin_file: Path | None) -> tuple[str | None, int]:
    fingerprint = hash_file(stdin_file)
    return (fingerprint["sha256"], fingerprint["bytes"]) if fingerprint else (None, 0)


def parse_codex_argv(raw: str | None, argv_items: list[str]) -> list[str]:
    if raw:
        value = json.loads(raw)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise argparse.ArgumentTypeError("--codex-argv-json must be a JSON string array")
        return value
    return list(argv_items)


def infer_image_arg_style(argv: list[str]) -> str:
    image_values: list[str] = []
    repeated = 0
    for index, token in enumerate(argv):
        if token == "--image" and index + 1 < len(argv):
            repeated += 1
            image_values.append(argv[index + 1])
        elif token.startswith("--image="):
            repeated += 1
            image_values.append(token.split("=", 1)[1])
    if not image_values:
        return "none"
    comma = any("," in value for value in image_values)
    if comma and repeated > 1:
        return "mixed"
    return "comma_list" if comma else "repeated_flags"


def validate_image_arg_order(argv: list[str]) -> bool:
    positional_seen = False
    skip_next = False
    for token in argv:
        if skip_next:
            skip_next = False
            continue
        if token == "--":
            positional_seen = True
            continue
        if token == "--image":
            if positional_seen:
                return False
            skip_next = True
            continue
        if token.startswith("--image="):
            if positional_seen:
                return False
            continue
        if token in ARGV_VALUE_FLAGS:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        if token in {"codex", "exec"} and not positional_seen:
            continue
        positional_seen = True
    return infer_image_arg_style(argv) != "mixed"


def encoded_len(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bytes):
        return len(value)
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    return len(json.dumps(value, sort_keys=True).encode("utf-8"))


def walk_values(value: Any):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from walk_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk_values(item)


def command_from_event(event: dict[str, Any]) -> str | None:
    for node in walk_values(event):
        for key in ("command", "cmd", "argv"):
            raw = node.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw
            if isinstance(raw, list) and raw:
                return " ".join(str(item) for item in raw)
    return None


def command_output_bytes(event: dict[str, Any]) -> tuple[int, int]:
    stdout = 0
    stderr = 0
    for node in walk_values(event):
        for key, value in node.items():
            lowered = str(key).lower()
            if lowered in {"stdout", "stdout_text"}:
                stdout += encoded_len(value)
            elif lowered in {"stderr", "stderr_text"}:
                stderr += encoded_len(value)
    if stdout == 0 and stderr == 0:
        for node in walk_values(event):
            output = node.get("output")
            if output is not None:
                stdout += encoded_len(output)
    return stdout, stderr


def is_command_event(event_type: str, event: dict[str, Any]) -> bool:
    lowered = event_type.lower()
    return "command" in lowered or "exec" in lowered or command_from_event(event) is not None


def forbidden_green_path_command(command: str) -> bool:
    try:
        normalized = " ".join(shlex.split(command)) if command.strip() else ""
    except ValueError:
        normalized = " ".join(command.strip().split())
    return any(normalized == item or normalized.startswith(f"{item} ") for item in GREEN_PATH_FORBIDDEN)


def empty_tool_context_intake() -> dict[str, Any]:
    return {
        "command_count": 0,
        "command_stdout_bytes_total": 0,
        "command_stderr_bytes_total": 0,
        "command_stdout_bytes_visible_to_model": 0,
        "command_stderr_bytes_visible_to_model": 0,
        "large_command_output_count": 0,
        "largest_command_output_bytes": 0,
        "file_change_event_count": 0,
        "plan_update_event_count": 0,
        "web_search_event_count": 0,
        "mcp_tool_event_count": 0,
        "green_path_forbidden_command_count": 0,
        "green_path_forbidden_commands": [],
    }


def parse_tool_context_intake(paths: list[Path], *, green_path: bool = False, large_threshold: int = 4096) -> tuple[dict[str, Any], dict[str, int]]:
    intake = empty_tool_context_intake()
    malformed = 0
    unknown = 0
    for path in paths:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    event = json.loads(text)
                except json.JSONDecodeError:
                    malformed += 1
                    continue
                if not isinstance(event, dict):
                    unknown += 1
                    continue
                event_type = str(event.get("type") or "unknown")
                lowered = event_type.lower()
                if event_type == "unknown":
                    unknown += 1
                if is_command_event(event_type, event):
                    command = command_from_event(event) or ""
                    stdout, stderr = command_output_bytes(event)
                    total = stdout + stderr
                    intake["command_count"] += 1
                    intake["command_stdout_bytes_total"] += stdout
                    intake["command_stderr_bytes_total"] += stderr
                    intake["command_stdout_bytes_visible_to_model"] += stdout
                    intake["command_stderr_bytes_visible_to_model"] += stderr
                    intake["largest_command_output_bytes"] = max(intake["largest_command_output_bytes"], total)
                    if total > large_threshold:
                        intake["large_command_output_count"] += 1
                    if green_path and command and forbidden_green_path_command(command):
                        intake["green_path_forbidden_commands"].append(command)
                if "file" in lowered or "patch" in lowered:
                    intake["file_change_event_count"] += 1
                if "plan" in lowered:
                    intake["plan_update_event_count"] += 1
                if "web" in lowered and "search" in lowered:
                    intake["web_search_event_count"] += 1
                if "mcp" in lowered:
                    intake["mcp_tool_event_count"] += 1
    intake["green_path_forbidden_command_count"] = len(intake["green_path_forbidden_commands"])
    return intake, {"malformed_jsonl_line_count": malformed, "unknown_event_count": unknown}


def bounded_layer(captured: int, model_visible: int | None, *, cap: int, policy: str) -> dict[str, Any]:
    summarized = min(captured, cap) if captured else 0
    visible = min(summarized, cap) if model_visible is None else model_visible
    return {
        "captured_bytes_total": captured,
        "summarized_bytes": summarized,
        "model_visible_bytes": visible,
        "truncation_policy": policy,
    }


def validation_layer(captured: int, model_visible: int | None, *, cap: int, policy: str) -> dict[str, Any]:
    summarized = min(captured, cap) if captured else 0
    visible = min(summarized, cap) if model_visible is None else model_visible
    return {
        "captured_bytes_total": captured,
        "summarized_bytes": summarized,
        "model_visible_bytes": visible,
        "excerpt_policy": policy,
    }


def budget_violations(record: dict[str, Any], budgets: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    if record["prompt"]["prompt_bytes"] > budgets["prompt_max_bytes"]:
        violations.append("prompt_max_bytes")
    if record["artifacts"].get("task_packet_bytes", 0) > budgets["task_packet_max_bytes"]:
        violations.append("task_packet_max_bytes")
    if record["zcode_io"]["zcode_manifest_bytes_shown"] > budgets["zcode_manifest_max_bytes"]:
        violations.append("zcode_manifest_max_bytes")
    if record["diff"]["model_visible_bytes"] > budgets["diff_excerpt_max_bytes"]:
        violations.append("diff_excerpt_max_bytes")
    if record["validation"]["model_visible_bytes"] > budgets["validation_excerpt_max_bytes"]:
        violations.append("validation_excerpt_max_bytes")
    tool = record["tool_context_intake"]
    command_visible = tool["command_stdout_bytes_visible_to_model"] + tool["command_stderr_bytes_visible_to_model"]
    if command_visible > budgets["command_output_max_bytes"]:
        violations.append("command_output_max_bytes")
    if not budgets["image_resend_allowed"] and record["images"]["image_resent_to_acceptance"]:
        violations.append("image_resend_allowed")
    if tool["green_path_forbidden_command_count"]:
        violations.append("green_path_forbidden_command")
    return violations


def build_context_intake_record(args: argparse.Namespace) -> dict[str, Any]:
    workspace = args.workspace.resolve()
    mode_info = normalize_mode(args.canonical_mode, args.mode_detail)
    run_id = args.run_id or str(uuid.uuid4())
    prompt_sha, prompt_bytes = prompt_hash_and_bytes(args.prompt_text, args.prompt_file)
    stdin_sha, stdin_bytes = stdin_hash_and_bytes(args.stdin_file)
    argv = parse_codex_argv(args.codex_argv_json, args.codex_argv)
    image_style = args.image_arg_style or infer_image_arg_style(argv)
    image_manifest = [{"path": str(path), "bytes": byte_count(path)} for path in args.image]
    image_manifest_sha = sha256_bytes(json.dumps(image_manifest, sort_keys=True).encode("utf-8")) if image_manifest else None
    tool_intake, parse_events = parse_tool_context_intake(args.raw_jsonl, green_path=args.green_path)
    raw_jsonl_paths = [path for path in args.raw_jsonl if path.exists()]
    raw_jsonl_gitignored = bool(raw_jsonl_paths) and all(gitignored(path, workspace) for path in raw_jsonl_paths)
    diff_captured = args.diff_captured_bytes if args.diff_captured_bytes is not None else byte_count(args.diff_path)
    validation_captured = (
        args.validation_captured_bytes if args.validation_captured_bytes is not None else byte_count(args.validation_log)
    )
    diff = bounded_layer(
        diff_captured,
        args.diff_model_visible_bytes,
        cap=args.diff_excerpt_max_bytes,
        policy=args.diff_policy,
    )
    validation = validation_layer(
        validation_captured,
        args.validation_model_visible_bytes,
        cap=args.validation_excerpt_max_bytes,
        policy=args.validation_policy,
    )
    record = {
        "schema_version": SCHEMA_VERSION,
        "recorded_at": utc_now(),
        "experiment_id": args.experiment_id,
        "task_id": args.task_id,
        **mode_info,
        "run_kind": args.run_kind,
        "phase": args.phase,
        "attempt_index": args.attempt_index,
        "run_id": run_id,
        "usage_ledger_run_id": args.usage_ledger_run_id,
        "prompt": {
            "template_id": args.template_id,
            "template_sha256": hash_file(args.template_file)["sha256"] if hash_file(args.template_file) else None,
            "prompt_sha256": prompt_sha,
            "prompt_bytes": prompt_bytes,
            "raw_prompt_stored": False,
        },
        "stdin": {"stdin_sha256": stdin_sha, "stdin_bytes": stdin_bytes, "sections": []},
        "argv": {
            "codex_command_sha256": sha256_bytes(json.dumps(argv, separators=(",", ":")).encode("utf-8")) if argv else None,
            "image_arg_style": image_style,
            "prompt_source": args.prompt_source,
            "harness_arg_order_validated": validate_image_arg_order(argv),
        },
        "codex_invocation": {
            "codex_cli_version": args.codex_cli_version,
            "model_arg": args.model_arg,
            "resolved_model": args.resolved_model,
            "reasoning_effort": args.reasoning_effort,
            "sandbox": args.sandbox,
            "approval_mode": args.approval_mode,
            "profile": args.profile,
            "ignore_user_config": args.ignore_user_config,
            "ignore_rules": args.ignore_rules,
            "web_search_mode": args.web_search_mode,
            "output_schema_path": str(args.output_schema_path) if args.output_schema_path else None,
            "output_schema_sha256": hash_file(args.output_schema_path)["sha256"] if hash_file(args.output_schema_path) else None,
            "run_order_index": args.run_order_index,
            "run_order_seed": args.run_order_seed,
            "cache_condition": args.cache_condition,
            "previous_run_id": args.previous_run_id,
            "feature_flags": args.feature_flag,
        },
        "repo_context": {
            "agents_bytes_shown": args.agents_bytes_shown,
            "rules_bytes_shown": args.rules_bytes_shown,
            "config_bytes_shown": args.config_bytes_shown,
            "repo_hint_bytes_shown": args.repo_hint_bytes_shown,
            "full_repo_context_shown": args.full_repo_context_shown,
        },
        "zcode_io": {
            "zcode_manifest_bytes_shown": args.zcode_manifest_bytes_shown
            if args.zcode_manifest_bytes_shown is not None
            else byte_count(args.zcode_manifest_path),
            "zcode_stdout_bytes_captured": args.zcode_stdout_bytes_captured
            if args.zcode_stdout_bytes_captured is not None
            else byte_count(args.zcode_stdout_log),
            "zcode_stderr_bytes_captured": args.zcode_stderr_bytes_captured
            if args.zcode_stderr_bytes_captured is not None
            else byte_count(args.zcode_stderr_log),
            "zcode_stdout_bytes_model_visible": args.zcode_stdout_bytes_model_visible,
            "zcode_stderr_bytes_model_visible": args.zcode_stderr_bytes_model_visible,
        },
        "diff": diff,
        "validation": validation,
        "images": {
            "image_count": len(args.image),
            "image_bytes_total": sum(item["bytes"] for item in image_manifest),
            "image_manifest_sha256": image_manifest_sha,
            "image_resent_to_acceptance": args.image_resent_to_acceptance,
        },
        "tool_context_intake": tool_intake,
        "visible_context_budget": {
            "budgets": {
                "prompt_max_bytes": args.prompt_max_bytes,
                "task_packet_max_bytes": args.task_packet_max_bytes,
                "zcode_manifest_max_bytes": args.zcode_manifest_max_bytes,
                "diff_excerpt_max_bytes": args.diff_excerpt_max_bytes,
                "validation_excerpt_max_bytes": args.validation_excerpt_max_bytes,
                "command_output_max_bytes": args.command_output_max_bytes,
                "image_resend_allowed": args.image_resend_allowed,
            },
            "budget_violations": [],
        },
        "artifacts": {
            "raw_jsonl_paths": [str(path) for path in args.raw_jsonl],
            "raw_jsonl_hashes": [hash_file(path) for path in args.raw_jsonl if hash_file(path)],
            "task_packet_path": str(args.task_packet_path) if args.task_packet_path else None,
            "task_packet_bytes": byte_count(args.task_packet_path),
            "diff_path": str(args.diff_path) if args.diff_path else None,
            "validation_log": str(args.validation_log) if args.validation_log else None,
            "tool_context_parse": parse_events,
        },
        "safety": {
            "secret_scan_result": args.secret_scan_result,
            "raw_logs_gitignored": raw_jsonl_gitignored,
            "env_policy": args.env_policy,
            "raw_jsonl_sensitivity": "sensitive",
            "raw_jsonl_file_mode": path_file_mode(args.raw_jsonl[0] if args.raw_jsonl else None),
            "raw_jsonl_gitignored": raw_jsonl_gitignored,
            "raw_jsonl_retention_days": args.raw_jsonl_retention_days,
            "raw_jsonl_secret_scan_result": args.raw_jsonl_secret_scan_result,
            "raw_jsonl_redacted_preview_path": None,
        },
        "user_impact": {
            "ask_user_count": args.ask_user_count,
            "manual_intervention_required": args.manual_intervention_required,
            "manual_repair_required": args.manual_repair_required,
        },
    }
    record["visible_context_budget"]["budget_violations"] = budget_violations(
        record,
        record["visible_context_budget"]["budgets"],
    )
    return record


def capture_context_intake(args: argparse.Namespace) -> int:
    record = build_context_intake_record(args)
    append_jsonl_record(args.ledger, record)
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0


def load_context_intake_records(path: Path) -> list[dict[str, Any]]:
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
                raise ValueError(f"{path}:{line_no}: invalid context intake JSONL: {exc}") from exc
    return records


def add_context_intake_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("capture-codex-context-intake", help="Append one Codex context-intake ledger record.")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_CONTEXT_LEDGER)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--canonical-mode", choices=CANONICAL_MODES, required=True)
    parser.add_argument("--mode-detail")
    parser.add_argument("--run-kind", choices=RUN_KIND_CHOICES, default="measurement")
    parser.add_argument("--phase", choices=PHASE_CHOICES, required=True)
    parser.add_argument("--attempt-index", type=positive_int, required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--usage-ledger-run-id")
    parser.add_argument("--workspace", type=Path, default=Path("."))
    parser.add_argument("--raw-jsonl", type=Path, action="append", default=[])
    parser.add_argument("--green-path", action="store_true")
    parser.add_argument("--template-id")
    parser.add_argument("--template-file", type=Path)
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--prompt-text")
    parser.add_argument("--stdin-file", type=Path)
    parser.add_argument("--codex-argv-json")
    parser.add_argument("--codex-argv", action="append", default=[])
    parser.add_argument("--prompt-source", choices=PROMPT_SOURCES, default="unknown")
    parser.add_argument("--image-arg-style", choices=IMAGE_ARG_STYLES)
    parser.add_argument("--image", type=Path, action="append", default=[])
    parser.add_argument("--image-resent-to-acceptance", action="store_true")
    parser.add_argument("--codex-cli-version")
    parser.add_argument("--model-arg")
    parser.add_argument("--resolved-model")
    parser.add_argument("--reasoning-effort")
    parser.add_argument("--sandbox")
    parser.add_argument("--approval-mode")
    parser.add_argument("--profile")
    parser.add_argument("--ignore-user-config", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--ignore-rules", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--web-search-mode")
    parser.add_argument("--output-schema-path", type=Path)
    parser.add_argument("--run-order-index", type=non_negative_int)
    parser.add_argument("--run-order-seed")
    parser.add_argument("--cache-condition")
    parser.add_argument("--previous-run-id")
    parser.add_argument("--feature-flag", action="append", default=[])
    parser.add_argument("--agents-bytes-shown", type=non_negative_int, default=0)
    parser.add_argument("--rules-bytes-shown", type=non_negative_int, default=0)
    parser.add_argument("--config-bytes-shown", type=non_negative_int, default=0)
    parser.add_argument("--repo-hint-bytes-shown", type=non_negative_int, default=0)
    parser.add_argument("--full-repo-context-shown", action="store_true")
    parser.add_argument("--zcode-manifest-path", type=Path)
    parser.add_argument("--zcode-manifest-bytes-shown", type=non_negative_int)
    parser.add_argument("--zcode-stdout-log", type=Path)
    parser.add_argument("--zcode-stderr-log", type=Path)
    parser.add_argument("--zcode-stdout-bytes-captured", type=non_negative_int)
    parser.add_argument("--zcode-stderr-bytes-captured", type=non_negative_int)
    parser.add_argument("--zcode-stdout-bytes-model-visible", type=non_negative_int, default=0)
    parser.add_argument("--zcode-stderr-bytes-model-visible", type=non_negative_int, default=0)
    parser.add_argument("--task-packet-path", type=Path)
    parser.add_argument("--diff-path", type=Path)
    parser.add_argument("--diff-captured-bytes", type=non_negative_int)
    parser.add_argument("--diff-model-visible-bytes", type=non_negative_int)
    parser.add_argument("--diff-policy", choices=("manifest_only", "cap", "full", "none"), default="manifest_only")
    parser.add_argument("--validation-log", type=Path)
    parser.add_argument("--validation-captured-bytes", type=non_negative_int)
    parser.add_argument("--validation-model-visible-bytes", type=non_negative_int)
    parser.add_argument("--validation-policy", choices=("manifest_only", "error_blocks", "head_tail", "full", "none"), default="manifest_only")
    parser.add_argument("--prompt-max-bytes", type=non_negative_int, default=DEFAULT_BUDGETS["prompt_max_bytes"])
    parser.add_argument("--task-packet-max-bytes", type=non_negative_int, default=DEFAULT_BUDGETS["task_packet_max_bytes"])
    parser.add_argument("--zcode-manifest-max-bytes", type=non_negative_int, default=DEFAULT_BUDGETS["zcode_manifest_max_bytes"])
    parser.add_argument("--diff-excerpt-max-bytes", type=non_negative_int, default=DEFAULT_BUDGETS["diff_excerpt_max_bytes"])
    parser.add_argument("--validation-excerpt-max-bytes", type=non_negative_int, default=DEFAULT_BUDGETS["validation_excerpt_max_bytes"])
    parser.add_argument("--command-output-max-bytes", type=non_negative_int, default=DEFAULT_BUDGETS["command_output_max_bytes"])
    parser.add_argument("--image-resend-allowed", action="store_true")
    parser.add_argument("--secret-scan-result", choices=("pass", "fail", "skipped", "unknown"), default="unknown")
    parser.add_argument("--raw-jsonl-secret-scan-result", choices=("pass", "fail", "skipped", "unknown"), default="unknown")
    parser.add_argument("--raw-jsonl-retention-days", type=non_negative_int, default=14)
    parser.add_argument("--env-policy", choices=("allowlist", "unknown"), default="unknown")
    parser.add_argument("--ask-user-count", type=non_negative_int, default=0)
    parser.add_argument("--manual-intervention-required", action="store_true")
    parser.add_argument("--manual-repair-required", action="store_true")
    parser.set_defaults(func=capture_context_intake)
