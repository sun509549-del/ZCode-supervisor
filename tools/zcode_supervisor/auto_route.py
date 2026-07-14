"""Repo-local auto-routing for Codex-to-ZCode delegation."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .operational_gateway import operational_gateway_fields
except ImportError:  # pragma: no cover - direct script execution
    from operational_gateway import operational_gateway_fields

ROUTING_FILE = Path(".codex/zcode-routing.json")
DEFAULT_USAGE_SNAPSHOT_SOURCE = "auto"
DEFAULT_PROMPT_TIMEOUT_MS = 10 * 60 * 1000
DEFAULT_VALIDATION_TIMEOUT_SECONDS = 60
DEFAULT_USAGE_SNAPSHOT_TIMEOUT_MS = 20 * 1000
PROCESS_TERMINATION_GRACE_SECONDS = 2
IMPLEMENTATION_WORDS = (
    "add",
    "build",
    "change",
    "edit",
    "fix",
    "implement",
    "refactor",
    "test",
    "update",
    "write",
    "作って",
    "修正",
    "変更",
    "実装",
    "追加",
    "直して",
)
ZCODE_REQUEST_WORDS = (
    "use zcode",
    "using zcode",
    "have zcode",
    "zcodeで",
    "zcodeに",
    "zcodeを使",
    "zcode使",
)
READ_ONLY_WORDS = (
    "audit",
    "explain",
    "inspect",
    "plan",
    "review",
    "summarize",
    "調べ",
    "説明",
    "レビュー",
    "計画",
)
TRIVIAL_WORDS = ("typo", "comment", "one-line", "one line", "誤字", "一行")
HIGH_RISK_WORDS = (
    ".env",
    "api key",
    "credential",
    "delete",
    "deploy",
    "destructive",
    "migration",
    "password",
    "payment",
    "production",
    "secret",
    "token",
    "trading",
    "remove data",
    "本番",
    "秘密",
    "認証",
    "決済",
    "削除",
    "移行",
)
PROTECTIVE_BEFORE_WORDS = (
    "do not",
    "don't",
    "must not",
    "should not",
    "without",
    "avoid",
    "never",
    "not ",
    "禁止",
)
PROTECTIVE_AFTER_WORDS = (
    "しない",
    "しません",
    "読まない",
    "触らない",
    "含めない",
    "出力しない",
    "漏らさない",
    "変えない",
    "弱めない",
)
CLAUSE_BOUNDARIES = ".;:\n"
GUARDRAIL_VERB_PATTERN = re.compile(
    r"\b(read(?:ing)?|inspect(?:ing)?|print(?:ing)?|output(?:ting)?|expos(?:e|ing)|"
    r"leak(?:ing)?|touch(?:ing)?|edit(?:ing)?|chang(?:e|ing)|include|including|"
    r"use|using|store|storing|weaken(?:ing)?|deploy(?:ing)?|migrat(?:e|ing)|"
    r"charg(?:e|ing)|pay(?:ing)?|delete|deleting|remove|removing)\b"
)
LIST_CONTINUATION_PATTERN = re.compile(
    r"^(((and|or|nor)\s+)?(api\s+keys?|tokens?|credentials?|secrets?|production\s+config|\.env\b)|"
    r"(and|or|nor)\s+(payments?|delete|deleting|remove|removing|touch(?:ing)?\s+production))"
)


def utc_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")


def emit_json(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def routing_path(workspace: Path) -> Path:
    return workspace / ROUTING_FILE


def load_routing(workspace: Path) -> dict[str, Any] | None:
    path = routing_path(workspace)
    if not path.exists():
        return None
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"routing config must be a JSON object: {path}")
    return payload


def lowered_words(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def lowered_for_protective_scan(text: str) -> str:
    return re.sub(r"[ \t\r\f\v]+", " ", text.strip().lower())


def contains_any(text: str, needles: tuple[str, ...]) -> bool:
    lowered = lowered_words(text)
    return any(needle in lowered for needle in needles)


def is_protective_context(lowered: str, start_index: int, end_index: int) -> bool:
    previous_boundary = max((lowered.rfind(boundary, 0, start_index) for boundary in CLAUSE_BOUNDARIES), default=-1)
    next_boundaries = [lowered.find(boundary, end_index) for boundary in CLAUSE_BOUNDARIES]
    next_boundary = min((index for index in next_boundaries if index >= 0), default=len(lowered))
    start = max(previous_boundary + 1, start_index - 80)
    end = min(next_boundary, end_index + 80)
    before = lowered[start:start_index]
    after = lowered[end_index:end]
    if any(protective in after for protective in PROTECTIVE_AFTER_WORDS):
        return True
    protective_starts = [
        before.rfind(protective)
        for protective in PROTECTIVE_BEFORE_WORDS
        if before.rfind(protective) >= 0
    ]
    if not protective_starts:
        return False
    following_phrase = lowered[end_index:end].split(",", 1)[0]
    segment = (before[max(protective_starts):] + lowered[start_index:end_index] + following_phrase).strip()
    if not GUARDRAIL_VERB_PATTERN.search(segment):
        return False
    if "," in segment:
        tail = segment.rsplit(",", 1)[1].strip()
        if not LIST_CONTINUATION_PATTERN.search(tail):
            return False
    else:
        connector_matches = list(re.finditer(r"\b(and|or|nor)\s+", segment))
        if connector_matches:
            tail = segment[connector_matches[-1].start():].strip()
            if not LIST_CONTINUATION_PATTERN.search(tail):
                return False
    return True


def first_unprotected_match(text: str, needles: tuple[str, ...]) -> str | None:
    lowered = lowered_for_protective_scan(text)
    for needle in needles:
        for match in re.finditer(re.escape(needle), lowered):
            if is_protective_context(lowered, match.start(), match.end()):
                continue
            return needle
    return None


def high_risk_reason(text: str) -> str | None:
    return first_unprotected_match(text, HIGH_RISK_WORDS)


def classify_task(objective: str, task_kind: str) -> dict[str, Any]:
    if high_risk_reason(objective):
        return {"route": "ask_user", "reason": "high_risk_task"}
    if "no-zcode" in lowered_words(objective):
        return {"route": "codex_direct", "reason": "no_zcode_requested"}
    if task_kind == "read-only":
        return {"route": "codex_direct", "reason": "read_only_task"}
    if task_kind == "trivial":
        return {"route": "codex_direct", "reason": "trivial_task"}
    if task_kind in {"plan", "audit"}:
        return {"route": "codex_direct", "reason": f"{task_kind}_owned_by_codex"}
    if task_kind == "implementation":
        return {"route": "delegate_zcode", "reason": "implementation_task"}
    if contains_any(objective, ZCODE_REQUEST_WORDS) and not contains_any(objective, READ_ONLY_WORDS):
        return {"route": "delegate_zcode", "reason": "zcode_requested"}
    if contains_any(objective, TRIVIAL_WORDS) and len(objective) < 140:
        return {"route": "codex_direct", "reason": "trivial_task"}
    if first_unprotected_match(objective, IMPLEMENTATION_WORDS):
        return {"route": "delegate_zcode", "reason": "implementation_task"}
    if contains_any(objective, READ_ONLY_WORDS):
        return {"route": "codex_direct", "reason": "read_only_task"}
    return {"route": "codex_direct", "reason": "unclear_or_planning_task"}


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-").lower()
    return slug[:48] or "task"


def route_defaults(config: dict[str, Any]) -> dict[str, Any]:
    defaults = config.get("defaults") if isinstance(config.get("defaults"), dict) else {}
    return {
        "effort": defaults.get("effort", "max"),
        "task_class": defaults.get("task_class", "root-cause"),
        "risk_budget": defaults.get("risk_budget", "low"),
        "workspace_kind": defaults.get("workspace_kind", "regular"),
        "usage_snapshot_source": defaults.get("usage_snapshot_source", DEFAULT_USAGE_SNAPSHOT_SOURCE),
        "max_attempts": int(defaults.get("max_attempts", 2)),
        "repair_validation": bool(defaults.get("repair_validation", True)),
        "result_verbosity": defaults.get("result_verbosity", "full"),
        "retry_delay_ms": int(defaults.get("retry_delay_ms", 60000)),
        "prompt_timeout_ms": int(defaults.get("prompt_timeout_ms", DEFAULT_PROMPT_TIMEOUT_MS)),
        "validation_timeout_seconds": int(
            defaults.get("validation_timeout_seconds", DEFAULT_VALIDATION_TIMEOUT_SECONDS)
        ),
        "usage_snapshot_timeout_ms": int(
            defaults.get("usage_snapshot_timeout_ms", DEFAULT_USAGE_SNAPSHOT_TIMEOUT_MS)
        ),
        "accept_validated_artifact_after_ms": int(defaults.get("accept_validated_artifact_after_ms", 0)),
    }


def trusted_supervisor_path() -> str:
    return str(Path(__file__).resolve().with_name("zcode_supervisor.py"))


def trusted_controller_path(args: argparse.Namespace) -> str:
    if args.trusted_zcodectl:
        return str(args.trusted_zcodectl.resolve())
    return str(Path(__file__).resolve().parents[1] / "zcode_control" / "zcodectl.mjs")


def workspace_output_path(workspace: Path, raw: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("routing output path must be a non-empty string")
    raw_path = Path(raw)
    if raw_path.is_absolute():
        raise ValueError(f"routing output path must be relative: {raw}")
    visible = workspace / raw_path
    cursor = visible
    workspace_resolved = workspace.resolve()
    while True:
        if cursor.exists() or cursor.is_symlink():
            if cursor.is_symlink():
                raise ValueError(f"routing output path uses symlink: {cursor.relative_to(workspace)}")
        if cursor == workspace:
            break
        cursor = cursor.parent
    candidate = (workspace / raw_path).resolve()
    try:
        candidate.relative_to(workspace_resolved)
    except ValueError as exc:
        raise ValueError(f"routing output path escapes workspace: {raw}") from exc
    return candidate


def packet_command(args: argparse.Namespace, config: dict[str, Any], packet_path: Path, prompt_path: Path) -> list[str]:
    defaults = route_defaults(config)
    command = [
        sys.executable,
        trusted_supervisor_path(),
        "packet",
        "--workspace",
        str(args.workspace),
        "--objective",
        args.objective,
        "--validation",
        args.validation,
        "--effort",
        args.effort or defaults["effort"],
        "--task-class",
        args.task_class or defaults["task_class"],
        "--risk-budget",
        args.risk_budget or defaults["risk_budget"],
        "--workspace-kind",
        args.workspace_kind or defaults["workspace_kind"],
        "--out",
        str(packet_path),
        "--prompt-out",
        str(prompt_path),
    ]
    for item in args.allowed:
        command.extend(["--allowed", item])
    for item in args.forbidden:
        command.extend(["--forbidden", item])
    for item in args.expected_output:
        command.extend(["--expected-output", item])
    for item in args.acceptance_criterion:
        command.extend(["--acceptance-criterion", item])
    for item in args.what_not_to_do:
        command.extend(["--what-not-to-do", item])
    for item in args.final_report_line:
        command.extend(["--final-report-line", item])
    for item in args.vision_image:
        command.extend(["--vision-image", item])
    if args.vision_required:
        command.append("--vision-required")
    if args.vision_service:
        command.extend(["--vision-service", args.vision_service])
    append_strict_contract_packet_args(command, args)
    max_changed_files = effective_max_changed_files(args, config)
    if max_changed_files > 0:
        command.extend(["--max-changed-files", str(max_changed_files)])
    if args.goal:
        command.append("--goal")
    return command


def append_strict_contract_packet_args(command: list[str], args: argparse.Namespace) -> None:
    optional_paths = (
        ("--task-contract", args.task_contract),
        ("--task-contract-out", args.task_contract_out),
        ("--strict-contract-override-json", args.strict_contract_override_json),
        ("--strict-contract-rubric-dir", args.strict_contract_rubric_dir),
    )
    optional_values = (
        ("--strict-contract-rubric-id", args.strict_contract_rubric_id),
        ("--strict-contract-task-id", args.strict_contract_task_id),
        ("--strict-contract-id", args.strict_contract_id),
        ("--strict-contract-risk-level", args.strict_contract_risk_level),
        ("--strict-contract-goal", args.strict_contract_goal),
    )
    for flag, value in optional_paths:
        if value:
            command.extend([flag, str(value)])
    for flag, value in optional_values:
        if value:
            command.extend([flag, value])
    command.extend(["--strict-contract-ambiguity-score", str(args.strict_contract_ambiguity_score)])
    command.extend(["--strict-contract-self-audit-path", args.strict_contract_self_audit_path])
    command.extend(["--strict-contract-max-prompt-chars", str(args.strict_contract_max_prompt_chars)])


def effective_max_changed_files(args: argparse.Namespace, config: dict[str, Any]) -> int:
    if args.max_changed_files is not None:
        return args.max_changed_files
    defaults = config.get("defaults") if isinstance(config.get("defaults"), dict) else {}
    configured = defaults.get("max_changed_files")
    if isinstance(configured, int) and configured > 0:
        return configured
    if args.allowed:
        return len(set(args.allowed))
    return 0


def run_packet_command(args: argparse.Namespace, config: dict[str, Any], packet_path: Path, run_path: Path) -> list[str]:
    defaults = route_defaults(config)
    max_attempts = args.max_attempts if args.max_attempts is not None else defaults["max_attempts"]
    retry_delay_ms = args.retry_delay_ms if args.retry_delay_ms is not None else defaults["retry_delay_ms"]
    timeout_ms = args.timeout_ms if args.timeout_ms is not None else defaults["prompt_timeout_ms"]
    validation_timeout = (
        args.validation_timeout
        if args.validation_timeout is not None
        else defaults["validation_timeout_seconds"]
    )
    usage_snapshot_timeout_ms = (
        args.usage_snapshot_timeout_ms
        if args.usage_snapshot_timeout_ms is not None
        else defaults["usage_snapshot_timeout_ms"]
    )
    command = [
        "node",
        trusted_controller_path(args),
        "run-packet",
        "--packet",
        str(packet_path),
        "--mode",
        args.run_mode,
        "--max-attempts",
        str(max_attempts),
        "--retry-delay-ms",
        str(retry_delay_ms),
        "--validation-timeout",
        str(validation_timeout),
        "--timeout-ms",
        str(timeout_ms),
        "--usage-snapshot-source",
        args.usage_snapshot_source or defaults["usage_snapshot_source"],
        "--usage-snapshot-timeout-ms",
        str(usage_snapshot_timeout_ms),
        "--out",
        str(run_path),
    ]
    accept_after_ms = (
        args.accept_validated_artifact_after_ms
        if args.accept_validated_artifact_after_ms is not None
        else defaults["accept_validated_artifact_after_ms"]
    )
    if accept_after_ms and accept_after_ms > 0:
        command.extend(["--accept-validated-artifact-after-ms", str(accept_after_ms)])
    repair_validation = args.repair_validation
    if repair_validation is None:
        repair_validation = defaults["repair_validation"]
    command.append("--repair-validation" if repair_validation else "--no-repair-validation")
    return command


def run_command_timeout_seconds(args: argparse.Namespace, config: dict[str, Any]) -> int:
    defaults = route_defaults(config)
    max_attempts = args.max_attempts if args.max_attempts is not None else defaults["max_attempts"]
    retry_delay_ms = args.retry_delay_ms if args.retry_delay_ms is not None else defaults["retry_delay_ms"]
    timeout_ms = args.timeout_ms if args.timeout_ms is not None else defaults["prompt_timeout_ms"]
    validation_timeout = (
        args.validation_timeout
        if args.validation_timeout is not None
        else defaults["validation_timeout_seconds"]
    )
    usage_snapshot_source = args.usage_snapshot_source or defaults["usage_snapshot_source"]
    usage_snapshot_timeout_ms = (
        args.usage_snapshot_timeout_ms
        if args.usage_snapshot_timeout_ms is not None
        else defaults["usage_snapshot_timeout_ms"]
    )
    if usage_snapshot_source == "none":
        usage_snapshot_attempts = 0
    elif usage_snapshot_source == "auto":
        usage_snapshot_attempts = 4
    else:
        usage_snapshot_attempts = 2
    total_ms = (timeout_ms * max_attempts) + (retry_delay_ms * max(0, max_attempts - 1))
    usage_snapshot_seconds = (usage_snapshot_timeout_ms * usage_snapshot_attempts) // 1000
    return max(1, (total_ms // 1000) + (validation_timeout * max_attempts) + usage_snapshot_seconds + 30)


def run_json_command(
    command: list[str],
    cwd: Path,
    *,
    timeout_seconds: int | None = None,
) -> tuple[int, dict[str, Any] | None, str, str, bool]:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=os.name == "posix",
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        terminate_process_group(process)
        try:
            stdout, stderr = process.communicate(timeout=PROCESS_TERMINATION_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            kill_process_group(process)
            stdout, stderr = process.communicate()
        message = f"command timed out after {timeout_seconds}s"
        return 124, None, stdout, f"{stderr}\n{message}".strip(), True
    parsed = None
    if stdout.strip():
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            parsed = None
    return process.returncode or 0, parsed, stdout, stderr, False


def terminate_process_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
            return
        except ProcessLookupError:
            return
    process.terminate()


def kill_process_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return
        except ProcessLookupError:
            return
    process.kill()


def read_json_if_present(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return read_json(path)
    except (OSError, json.JSONDecodeError):
        return None


def extract_changed_count(run_json: dict[str, Any] | None) -> int | None:
    if not isinstance(run_json, dict):
        return None
    audit = run_json.get("audit")
    if isinstance(audit, dict) and isinstance(audit.get("changed_count"), int):
        return audit["changed_count"]
    attempt_results = run_json.get("attempt_results")
    if isinstance(attempt_results, list) and attempt_results:
        last = attempt_results[-1]
        if isinstance(last, dict) and isinstance(last.get("changed_count"), int):
            return last["changed_count"]
    return None


def extract_run_summary(run_json: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(run_json, dict):
        return None
    audit = run_json.get("audit") if isinstance(run_json.get("audit"), dict) else {}
    validation = audit.get("validation") if isinstance(audit.get("validation"), dict) else {}
    attempts = run_json.get("attempt_results")
    attempt_count = run_json.get("attempt_count")
    if attempt_count is None and isinstance(attempts, list):
        attempt_count = len(attempts)
    last_attempt = attempts[-1] if isinstance(attempts, list) and attempts else {}
    summary = {
        "ok": run_json.get("ok"),
        "status": run_json.get("status"),
        "supervisor_state": first_present(run_json.get("supervisor_state"), last_attempt.get("supervisor_state")),
        "attempt_count": attempt_count,
        "retry_count": run_json.get("retry_count"),
        "max_attempts": run_json.get("max_attempts"),
        "audit_ok": first_present(run_json.get("audit_ok"), last_attempt.get("audit_ok"), audit.get("ok")),
        "validation_ok": first_present(
            run_json.get("validation_ok"),
            last_attempt.get("validation_ok"),
            validation.get("ok"),
        ),
        "changed_count": first_present(
            run_json.get("changed_count"),
            run_json.get("zcode_changed_count"),
            last_attempt.get("changed_count"),
            audit.get("changed_count"),
        ),
        "safe_to_retry_later": run_json.get("safe_to_retry_later"),
        "usage_available": run_json.get("usage_available"),
        "no_usage_reason": run_json.get("no_usage_reason"),
    }
    if audit:
        summary["artifact_quality"] = audit.get("artifact_quality")
        summary["scope_safety"] = audit.get("scope_safety")
        summary["validation_result"] = audit.get("validation_result")
        summary["codex_repair_size_recommendation"] = audit.get("codex_repair_size_recommendation")
        summary["codex_review_required"] = audit.get("codex_review_required")
    if validation.get("ok") is False:
        summary["validation_returncode"] = validation.get("returncode")
        summary["validation_stdout_tail"] = tail_text(validation.get("stdout_tail"))
        summary["validation_stderr_tail"] = tail_text(validation.get("stderr_tail"))
    return summary


def run_result_timed_out(run_json: dict[str, Any] | None) -> bool:
    if not isinstance(run_json, dict):
        return False
    return (
        run_json.get("timed_out") is True
        or run_json.get("status") == "run_timeout"
        or run_json.get("supervisor_state") == "run_timeout"
    )


def tail_text(value: Any, limit: int = 1200) -> str:
    if not isinstance(value, str):
        return ""
    return value[-limit:]


def first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def result_verbosity(args: argparse.Namespace, config: dict[str, Any]) -> str:
    if args.result_verbosity:
        return args.result_verbosity
    value = route_defaults(config)["result_verbosity"]
    return value if value in {"compact", "full"} else "compact"


def build_paths(workspace: Path, config: dict[str, Any], objective: str) -> tuple[Path, Path, Path]:
    paths = config.get("paths") if isinstance(config.get("paths"), dict) else {}
    task_id = f"{utc_slug()}-{slugify(objective)}"
    packet_dir = workspace_output_path(workspace, paths.get("packets", ".codex/zcode/packets"))
    run_dir = workspace_output_path(workspace, paths.get("runs", ".codex/zcode/runs"))
    packet_path = packet_dir / f"{task_id}.json"
    prompt_path = packet_dir / f"{task_id}.prompt.txt"
    run_path = run_dir / f"{task_id}.zcode.json"
    return packet_path, prompt_path, run_path


def explain_decision(
    *,
    workspace: Path,
    config: dict[str, Any] | None,
    classification: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    route = classification["route"]
    needs_planning = route == "delegate_zcode" and (not args.allowed or not args.validation)
    return {
        "ok": True,
        "workspace": str(workspace),
        "routing_config": str(routing_path(workspace)) if config else None,
        "routing_mode": config.get("routing_mode") if config else None,
        "route": "needs_codex_planning" if needs_planning else route,
        "reason": "missing_allowed_or_validation" if needs_planning else classification["reason"],
        "codex_owns": (config or {}).get("policy", {}).get("codex_owns", []),
        "zcode_owns": (config or {}).get("policy", {}).get("zcode_owns", []),
        "next_action": next_action(route, classification["reason"], needs_planning),
    }


def next_action(route: str, reason: str, needs_planning: bool) -> str:
    if needs_planning:
        return "Codex should choose a tight allowed-file set and validation command, then rerun auto-route --execute."
    if route == "delegate_zcode":
        return "Run with --execute to create a packet and delegate bounded implementation to ZCode."
    if route == "ask_user":
        return "Pause for a concise plan because this matches an ask-before risk category."
    if route == "codex_direct":
        return f"Codex may handle this directly because {reason}."
    return "Inspect the routing decision before proceeding."


def auto_route_command(args: argparse.Namespace) -> int:
    workspace = args.workspace.resolve()
    if not workspace.is_dir():
        raise ValueError(f"workspace does not exist: {workspace}")
    config = load_routing(workspace)
    if config is None:
        emit_json({
            "ok": True,
            "workspace": str(workspace),
            "route": "codex_direct",
            "reason": "routing_config_missing",
            "next_action": "Run zcode-install-repo for this repo if ZCode delegation should be enabled.",
            **operational_gateway_fields(
                route="codex_direct",
                reason="routing_config_missing",
                zcode_attempted=False,
                zcode_ok=False,
            ),
        })
        return 0

    classification = classify_task(args.objective, args.task_kind)
    decision = explain_decision(workspace=workspace, config=config, classification=classification, args=args)
    if decision["route"] != "delegate_zcode" or not args.execute:
        if args.execute and decision["route"] != "delegate_zcode":
            payload = {
                **decision,
                "ok": False,
                "executed": False,
                "failure_reason": "execution_blocked_by_route",
                "required_acceptance": [
                    "route=delegate_zcode",
                    "executed=true",
                    "run_json_present",
                    "run_result.ok=true",
                    "artifact_quality=pass",
                    "scope_audit_pass",
                    "validation_pass",
                    "codex_review_completed",
                ],
                **operational_gateway_fields(
                    route=decision["route"],
                    reason=decision["reason"],
                    zcode_attempted=False,
                    zcode_ok=False,
                ),
            }
            if args.continue_with_codex_fallback and decision["route"] == "codex_direct":
                payload.update({
                    "ok": True,
                    "delegation_ok": False,
                    "codex_fallback_required": True,
                    "task_acceptance_ready": False,
                })
                emit_json(payload)
                return 0
            emit_json(payload)
            return 1
        emit_json(decision)
        return 0

    packet_path, prompt_path, run_path = build_paths(workspace, config, args.objective)
    packet_cmd = packet_command(args, config, packet_path, prompt_path)
    packet_rc, packet_json, packet_stdout, packet_stderr, packet_timed_out = run_json_command(packet_cmd, workspace)
    if packet_rc != 0:
        emit_json({
            **decision,
            "ok": False,
            "route": "packet_failed",
            "packet_command": packet_cmd,
            "packet_stdout": packet_stdout[-4000:],
            "packet_stderr": packet_stderr[-4000:],
            "timed_out": packet_timed_out,
        })
        return 1

    run_cmd = run_packet_command(args, config, packet_path, run_path)
    run_timeout_seconds = run_command_timeout_seconds(args, config)
    run_path.unlink(missing_ok=True)
    run_rc, run_stdout_json, run_stdout, run_stderr, run_timed_out = run_json_command(
        run_cmd,
        workspace,
        timeout_seconds=run_timeout_seconds,
    )
    run_file_json = read_json_if_present(run_path)
    run_json = run_file_json or run_stdout_json
    run_result_source = "run_file" if run_file_json else ("stdout" if run_stdout_json else None)
    changed_count = extract_changed_count(run_json)
    zcode_did_work = isinstance(changed_count, int) and changed_count > 0
    run_result_ok = isinstance(run_json, dict) and run_json.get("ok") is True
    run_timed_out_effective = run_timed_out or run_result_timed_out(run_json)
    run_file_required = True
    run_ok = run_rc == 0 and run_result_ok and run_file_json is not None
    failure_reason = None
    if run_timed_out_effective:
        failure_reason = "run_timeout"
        run_ok = False
    elif run_file_required and run_file_json is None:
        failure_reason = "run_timeout" if run_timed_out_effective else "run_json_missing"
        run_ok = False
    elif run_json is None:
        failure_reason = "run_timeout" if run_timed_out_effective else "run_json_missing"
        run_ok = False
    elif not args.allow_no_change and run_ok and not zcode_did_work:
        failure_reason = "zcode_no_changes"
        run_ok = False
    elif run_rc != 0:
        failure_reason = "run_command_failed"
    elif not run_result_ok:
        failure_reason = "run_result_not_ok"
    payload = {
        **decision,
        "executed": True,
        "delegation_ok": run_ok,
        "codex_fallback_required": args.continue_with_codex_fallback and not run_ok,
        "packet": str(packet_path),
        "prompt": str(prompt_path),
        "run": str(run_path),
        "run_command_timeout_seconds": run_timeout_seconds,
        "run_result_summary": extract_run_summary(run_json),
        "run_result_source": run_result_source,
        "run_file_exists": run_path.exists(),
        "zcode_changed_count": changed_count,
        "zcode_did_work": zcode_did_work,
        "failure_reason": failure_reason,
        "timed_out": run_timed_out_effective,
        "ok": run_ok,
        **operational_gateway_fields(
            route=decision["route"],
            reason=decision["reason"],
            zcode_attempted=True,
            zcode_ok=run_ok,
            run_json=run_json,
            failure_reason=failure_reason,
            timed_out=run_timed_out_effective,
        ),
    }
    if args.continue_with_codex_fallback and not run_ok:
        payload["ok"] = True
        payload["task_acceptance_ready"] = False
    if result_verbosity(args, config) == "full":
        payload.update(
            {
                "packet_command": packet_cmd,
                "run_command": run_cmd,
                "packet_result": packet_json,
                "run_result": run_json,
                "run_stdout_tail": run_stdout[-4000:] if run_json is None else "",
                "run_stderr_tail": run_stderr[-4000:],
            }
        )
    write_json(run_path.with_suffix(".route.json"), payload)
    emit_json(payload)
    return 0 if run_ok or args.continue_with_codex_fallback else 1


def add_auto_route_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    route = subparsers.add_parser("auto-route", help="Route a task through the repo-local ZCode delegation contract.")
    route.add_argument("--workspace", type=Path, default=Path("."))
    route.add_argument("--objective", required=True)
    route.add_argument("--task-kind", choices=("auto", "implementation", "read-only", "plan", "audit", "trivial"), default="auto")
    route.add_argument("--allowed", action="append", default=[])
    route.add_argument("--forbidden", action="append", default=[])
    route.add_argument("--validation", default="")
    route.add_argument("--expected-output", action="append", default=[])
    route.add_argument("--acceptance-criterion", action="append", default=[])
    route.add_argument("--what-not-to-do", action="append", default=[])
    route.add_argument("--final-report-line", action="append", default=[])
    route.add_argument("--execute", action="store_true")
    route.add_argument("--run-mode", choices=("plan", "edit", "build", "yolo"), default="edit")
    route.add_argument("--effort", choices=("high", "max"))
    route.add_argument("--task-class", choices=("small-fix", "long-horizon", "architecture", "root-cause", "production-gate", "mobile-debug", "research"))
    route.add_argument("--risk-budget", choices=("low", "medium", "high"))
    route.add_argument("--workspace-kind", choices=("regular", "worktree", "disposable", "fixture"))
    route.add_argument("--max-changed-files", type=int)
    route.add_argument("--max-attempts", type=int)
    route.add_argument("--retry-delay-ms", type=int)
    route.add_argument("--timeout-ms", type=int, help="Per-attempt ZCode CLI timeout in milliseconds.")
    route.add_argument("--validation-timeout", type=int, help="Codex supervisor validation timeout in seconds.")
    route.add_argument("--usage-snapshot-source", choices=("auto", "zai-api", "codexbar", "none"))
    route.add_argument("--usage-snapshot-timeout-ms", type=int)
    route.add_argument("--repair-validation", action=argparse.BooleanOptionalAction, default=None)
    route.add_argument("--accept-validated-artifact-after-ms", type=int)
    route.add_argument("--result-verbosity", choices=("compact", "full"))
    route.add_argument("--vision-image", action="append", default=[])
    route.add_argument("--vision-required", action="store_true")
    route.add_argument("--vision-service")
    route.add_argument("--task-contract", type=Path)
    route.add_argument("--task-contract-out", type=Path)
    route.add_argument("--strict-contract-rubric-id")
    route.add_argument("--strict-contract-task-id")
    route.add_argument("--strict-contract-id")
    route.add_argument("--strict-contract-risk-level", choices=("L0", "L1", "L2", "L3", "L4"))
    route.add_argument("--strict-contract-ambiguity-score", type=float, default=0)
    route.add_argument("--strict-contract-goal")
    route.add_argument("--strict-contract-override-json", type=Path)
    route.add_argument("--strict-contract-rubric-dir", type=Path)
    route.add_argument("--strict-contract-self-audit-path", default=".codex/zcode/runs/zcode_self_audit.json")
    route.add_argument("--strict-contract-max-prompt-chars", type=int, default=12000)
    route.add_argument("--trusted-zcodectl", type=Path, help=argparse.SUPPRESS)
    route.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    route.add_argument("--goal", action="store_true")
    route.add_argument("--allow-no-change", action="store_true")
    route.add_argument(
        "--continue-with-codex-fallback",
        action="store_true",
        help=(
            "Return a codex_fallback operational result instead of blocking the "
            "whole task when the bounded ZCode route is unavailable or fails."
        ),
    )
    route.set_defaults(func=auto_route_command)


def auto_route_entrypoint(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Route a task through a repo-local ZCode delegation contract.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_auto_route_parser(subparsers)
    args = parser.parse_args(["auto-route", *(argv if argv is not None else sys.argv[1:])])
    try:
        return args.func(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        emit_json({"ok": False, "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(auto_route_entrypoint())
