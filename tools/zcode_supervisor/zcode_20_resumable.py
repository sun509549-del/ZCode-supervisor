"""Resumable ZCode-required 20+ live benchmark runner."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from tools.zcode_eval.zcode_model_usage_db_delta import SOURCE_TYPE as DB_DELTA_SOURCE_TYPE
from tools.zcode_eval.zcode_model_usage_db_delta import capture_after_delta


CLAIM_FAMILY = "zcode_required_20_live"
STATE_SCHEMA_VERSION = 1

OUTCOME_GREEN = "zcode_20_live_green_measured"
OUTCOME_PARTIAL = "zcode_20_live_partial_measured"
OUTCOME_PROVIDER_PAUSED = "provider_paused_resume_later"
OUTCOME_USAGE_FAILURE = "terminal_usage_capture_global_failure_after_repair"
OUTCOME_STRICT_HARNESS_FAILURE = "terminal_strict_harness_failure"
OUTCOME_CODEX_TOUCHED = "terminal_codex_touched_target_artifact"
ALLOWED_OUTCOMES = {
    OUTCOME_GREEN,
    OUTCOME_PARTIAL,
    OUTCOME_PROVIDER_PAUSED,
    OUTCOME_USAGE_FAILURE,
    OUTCOME_STRICT_HARNESS_FAILURE,
    OUTCOME_CODEX_TOUCHED,
}

PROVIDER_PAUSED_STATUS = "provider_paused_rate_limit_1302"
TASK_STATUSES = {"pending", "running", "strict_green", "failed", "provider_blocked"}
DEFAULT_COOLDOWN_SECONDS = (600, 1200, 2400, 3600)
DEFAULT_WORK_ROOT = Path(".local/zcode-20-resumable/work")
DEFAULT_STATE = Path(".local/zcode-20-resumable/state.json")
DEFAULT_FINAL_OUTCOME = Path(".local/zcode-20-resumable/final-outcome.json")
DEFAULT_READINESS = Path(".local/zcode-20-resumable/readiness.json")
DEFAULT_MANIFEST = Path(".local/zcode-20-resumable/manifest.json")
DEFAULT_MODEL_USAGE_DB = Path.home() / ".zcode/cli/db/db.sqlite"


@dataclass(frozen=True)
class TaskTemplate:
    fixture: str
    allowed_file: str
    objective: str
    strict_rubric_id: str
    strict_risk_level: str
    task_class: str = "small-fix"


TASK_TEMPLATES = (
    TaskTemplate(
        fixture="billing-credit-contract",
        allowed_file="src/credits.js",
        objective=(
            "Fix the billing-credit-contract hard benchmark fixture so npm test "
            "passes. Only edit src/credits.js. Preserve tests and README contract."
        ),
        strict_rubric_id="billing_cent_rounding.v1",
        strict_risk_level="L2",
    ),
    TaskTemplate(
        fixture="policy-reason-contract",
        allowed_file="src/policy.js",
        objective=(
            "Fix the policy-reason-contract hard benchmark fixture so npm test "
            "passes. Only edit src/policy.js. Preserve tests and README contract."
        ),
        strict_rubric_id="policy_exact_label_routing.v1",
        strict_risk_level="L2",
    ),
)


class ResumableBenchmarkError(RuntimeError):
    """Raised when the resumable benchmark cannot continue safely."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON payload must be an object: {path}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def relative_usage_path(path: Path, run_path: Path) -> str:
    try:
        return str(path.relative_to(run_path.parent))
    except ValueError:
        return str(path)


def positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        integer = int(value)
    except (TypeError, ValueError):
        return None
    return integer if integer > 0 else None


def int_or_zero(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        integer = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, integer)


def get_nested(payload: dict[str, Any] | None, *keys: str) -> Any:
    cursor: Any = payload
    for key in keys:
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(key)
    return cursor


def first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def task_templates_for_count(task_count: int) -> list[dict[str, Any]]:
    if task_count < 1:
        raise ValueError("--task-count must be positive")
    tasks: list[dict[str, Any]] = []
    for index in range(task_count):
        template = TASK_TEMPLATES[index % len(TASK_TEMPLATES)]
        task_number = index + 1
        tasks.append(
            {
                "task_id": f"{template.fixture}-{task_number:02d}",
                "fixture": template.fixture,
                "allowed_file": template.allowed_file,
                "objective": f"[ZCODE-20 row {task_number}] {template.objective}",
                "validation": "npm test",
                "strict_rubric_id": template.strict_rubric_id,
                "strict_risk_level": template.strict_risk_level,
                "task_class": template.task_class,
                "status": "pending",
                "attempts": 0,
                "blocker": None,
                "route_attempts": [],
                "repair_attempted": False,
                "workspace": None,
                "run_path": None,
                "packet_path": None,
                "worker_usage_status": None,
                "worker_usage_unit": None,
                "worker_total_tokens": None,
                "worker_usage_source_path": None,
                "row_ids": [],
                "zcode_implemented": False,
                "strict_accepted": None,
                "final_validation_rc": None,
                "provider_rate_limit_1302_count": 0,
            }
        )
    return tasks


def create_initial_state(*, task_count: int, work_root: Path, resume_command: str) -> dict[str, Any]:
    tasks = task_templates_for_count(task_count)
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "claim_family": CLAIM_FAMILY,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "task_count": task_count,
        "work_root": str(work_root),
        "tasks": tasks,
        "limits": {
            "max_total_attempts": 40,
            "max_attempts_per_task": 2,
            "max_cooldown_cycles": 4,
            "cooldown_seconds": list(DEFAULT_COOLDOWN_SECONDS),
        },
        "total_attempts": 0,
        "delegated_rows": 0,
        "strict_green_count": 0,
        "failure_count": 0,
        "provider_rate_limit_count": 0,
        "provider_pause_status": None,
        "provider_pause_count": 0,
        "last_provider_code": None,
        "last_route_output_path": None,
        "current_task_id": tasks[0]["task_id"] if tasks else None,
        "cooldown_cycles_used": 0,
        "resume_command": resume_command,
        "final_outcome": None,
        "terminal_blocker": None,
        "state_reused": False,
        "codex_touched_target_artifact": False,
        "usage_capture_global_failure": False,
        "production_green_path_enabled": False,
        "direct_mode_default": False,
        "strict_gate_weakened": False,
        "glm_5_2_fixed": True,
        "glm_4_7_fallback": False,
        "time_of_day_gate": False,
    }


def normalize_state(state: dict[str, Any], *, task_count: int, work_root: Path, resume_command: str) -> dict[str, Any]:
    if state.get("schema_version") != STATE_SCHEMA_VERSION:
        raise ValueError("unsupported state schema")
    if state.get("claim_family") != CLAIM_FAMILY:
        raise ValueError("state claim_family is not zcode_required_20_live")
    tasks = state.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError("state tasks must be a list")
    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError("state task must be an object")
        if task.get("status") not in TASK_STATUSES:
            raise ValueError(f"unsupported task status: {task.get('status')}")
        if task.get("status") == "running":
            task["status"] = "pending"
            task["blocker"] = "resumed_after_interrupted_running"
        task.setdefault("route_attempts", [])
        task.setdefault("attempts", 0)
        task.setdefault("provider_rate_limit_1302_count", 0)
    state["task_count"] = len(tasks)
    state["work_root"] = str(work_root)
    state["resume_command"] = resume_command
    state.setdefault("limits", {})
    state["limits"].setdefault("max_total_attempts", 40)
    state["limits"].setdefault("max_attempts_per_task", 2)
    state["limits"].setdefault("max_cooldown_cycles", 4)
    state["limits"].setdefault("cooldown_seconds", list(DEFAULT_COOLDOWN_SECONDS))
    state.setdefault("provider_pause_status", None)
    state.setdefault("provider_pause_count", 0)
    state.setdefault("last_provider_code", None)
    state.setdefault("last_route_output_path", None)
    state.setdefault("current_task_id", None)
    state["state_reused"] = True
    migrate_preimplementation_provider_pause(state)
    backfill_route_metadata(state)
    recompute_state_counts(state)
    if task_count != len(tasks):
        raise ValueError(f"state task_count {len(tasks)} does not match requested {task_count}")
    return state


def recompute_state_counts(state: dict[str, Any]) -> None:
    tasks = [task for task in state.get("tasks", []) if isinstance(task, dict)]
    attempted = [task for task in tasks if int_or_zero(task.get("attempts")) > 0]
    state["task_count"] = len(tasks)
    state["delegated_rows"] = len(attempted)
    state["strict_green_count"] = sum(1 for task in tasks if task.get("status") == "strict_green")
    state["failure_count"] = sum(1 for task in tasks if task.get("status") == "failed")
    state["total_attempts"] = sum(int_or_zero(task.get("attempts")) for task in tasks)
    state["provider_rate_limit_count"] = sum(
        int_or_zero(task.get("provider_rate_limit_1302_count")) for task in tasks
    )
    pause_events = sum(
        1
        for task in tasks
        for record in task.get("route_attempts", [])
        if isinstance(record, dict) and route_record_is_provider_pause(record)
    )
    state["provider_pause_count"] = max(int_or_zero(state.get("provider_pause_count")), pause_events)
    state["current_task_id"] = state.get("current_task_id") or next(
        (task.get("task_id") for task in tasks if task.get("status") in {"pending", "provider_blocked", "running"}),
        None,
    )
    state["updated_at"] = utc_now()


def strict_accepted(run: dict[str, Any]) -> bool | None:
    value = first_present(
        run.get("strict_accepted"),
        get_nested(run, "audit", "strict_contract", "accepted"),
        get_nested(run, "audit", "strict_accepted"),
        get_nested(run, "acceptance", "strict_accepted"),
    )
    return value if isinstance(value, bool) else None


def final_validation_rc(run: dict[str, Any]) -> int | None:
    value = first_present(
        run.get("final_validation_rc"),
        run.get("validation_rc"),
        get_nested(run, "validation", "returncode"),
        get_nested(run, "audit", "validation", "returncode"),
    )
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def changed_count(run: dict[str, Any]) -> int | None:
    value = first_present(
        run.get("changed_count"),
        run.get("zcode_changed_count"),
        get_nested(run, "audit", "changed_count"),
    )
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def usage_value(run: dict[str, Any], key: str) -> Any:
    value = run.get(key)
    if value is not None:
        return value
    accounting = run.get("usage_accounting")
    return accounting.get(key) if isinstance(accounting, dict) else None


def usage_measured(run: dict[str, Any]) -> bool:
    return (
        usage_value(run, "worker_usage_status") == "measured"
        and usage_value(run, "worker_usage_unit") == "tokens"
        and positive_int(usage_value(run, "worker_total_tokens")) is not None
        and isinstance(usage_value(run, "worker_usage_source_path"), str)
        and bool(str(usage_value(run, "worker_usage_source_path")).strip())
    )


def zcode_ran_without_provider_block(run: dict[str, Any]) -> bool:
    if run.get("provider_error_kind") == "provider_rate_limit_1302":
        return False
    if run.get("provider_error") and run.get("provider_error_kind"):
        return False
    if run.get("status") in {"success", "partial_success", "audit_failed", "unsafe_partial"}:
        return True
    count = changed_count(run)
    return count is not None and count > 0


def is_provider_rate_limit_1302(run: dict[str, Any]) -> bool:
    return (
        run.get("provider_error_kind") == "provider_rate_limit_1302"
        or run.get("provider_rate_limit_1302") is True
        or get_nested(run, "provider", "provider_error_kind") == "provider_rate_limit_1302"
    )


def provider_rate_limit_count(run: dict[str, Any]) -> int:
    direct = int_or_zero(run.get("provider_rate_limit_1302_count"))
    attempts = run.get("attempt_results")
    if isinstance(attempts, list):
        direct += sum(int_or_zero(item.get("provider_rate_limit_1302_count")) for item in attempts if isinstance(item, dict))
    return direct if direct > 0 else (1 if is_provider_rate_limit_1302(run) else 0)


def provider_blocked_before_implementation(run: dict[str, Any]) -> bool:
    return is_provider_rate_limit_1302(run) and int_or_zero(changed_count(run)) == 0


def route_record_is_provider_pause(record: dict[str, Any]) -> bool:
    return (
        record.get("provider_rate_limit_1302") is True
        and int_or_zero(record.get("changed_count")) == 0
        and record.get("task_attempt_consumed") is not True
    )


def migrate_preimplementation_provider_pause(state: dict[str, Any]) -> None:
    tasks = [task for task in state.get("tasks", []) if isinstance(task, dict)]
    event_index = 0
    for task in tasks:
        route_attempts = task.get("route_attempts")
        if not isinstance(route_attempts, list):
            continue
        provider_pause_records = []
        for record in route_attempts:
            if not isinstance(record, dict):
                continue
            is_pause = (
                record.get("provider_rate_limit_1302") is True
                and int_or_zero(record.get("changed_count")) == 0
            )
            if not is_pause:
                continue
            event_index += 1
            if "original_attempt" not in record and record.get("attempt") is not None:
                record["original_attempt"] = record.get("attempt")
            record["attempt"] = None
            record["provider_pause_event"] = event_index
            record["task_attempt_consumed"] = False
            provider_pause_records.append(record)
        if (
            task.get("status") == "provider_blocked"
            and task.get("zcode_implemented") is not True
            and provider_pause_records
        ):
            task["status"] = "pending"
            task["attempts"] = 0
            task["blocker"] = PROVIDER_PAUSED_STATUS
            task["safe_to_retry_later"] = True
            state["current_task_id"] = task.get("task_id")
            state["provider_pause_status"] = PROVIDER_PAUSED_STATUS
            state["last_provider_code"] = provider_pause_records[-1].get("provider_code") or "1302"
            state["last_route_output_path"] = task.get("run_path")
    if event_index:
        state["provider_pause_count"] = max(int_or_zero(state.get("provider_pause_count")), event_index)


def backfill_route_metadata(state: dict[str, Any]) -> None:
    last_route_output_path = None
    for task in state.get("tasks", []):
        if not isinstance(task, dict):
            continue
        task_route_output_path = task.get("run_path")
        for record in task.get("route_attempts", []):
            if not isinstance(record, dict):
                continue
            if route_record_is_provider_pause(record):
                original_attempt = positive_int(record.get("original_attempt"))
                if original_attempt is not None and task.get("workspace") and task.get("task_id"):
                    record["run_path"] = str(
                        Path(str(task["workspace"]))
                        / ".codex/zcode/runs"
                        / f"{task['task_id']}-attempt-{original_attempt}.zcode.json"
                    )
                elif not record.get("run_path"):
                    record["run_path"] = task_route_output_path or state.get("last_route_output_path")
            if record.get("run_path"):
                last_route_output_path = record.get("run_path")
            if record.get("task_attempt_consumed") is True and int_or_zero(record.get("changed_count")) > 0:
                task["zcode_implemented"] = True
    if last_route_output_path:
        state["last_route_output_path"] = last_route_output_path


def exact_blocker(run: dict[str, Any]) -> str:
    if is_provider_rate_limit_1302(run):
        return "provider_rate_limit_1302"
    if run.get("timed_out") is True:
        return "timeout_not_success"
    if run.get("status"):
        return str(run["status"])
    if run.get("error"):
        return str(run["error"])[:200]
    return "zcode_task_failed"


def run_is_strict_green(run: dict[str, Any]) -> bool:
    return (
        run.get("ok") is True
        and final_validation_rc(run) == 0
        and strict_accepted(run) is True
        and usage_measured(run)
    )


def safe_validation_repair(run: dict[str, Any]) -> bool:
    if run.get("timed_out") is True or is_provider_rate_limit_1302(run):
        return False
    if changed_count(run) == 0:
        return False
    validation_ok = first_present(run.get("validation_ok"), get_nested(run, "audit", "validation", "ok"))
    return validation_ok is False


class SubprocessExecutor:
    """Create packets and run the ZCode CLI for one benchmark task attempt."""

    def __init__(
        self,
        *,
        repo_root: Path,
        model_usage_db: Path,
        timeout_ms: int,
        validation_timeout: int,
    ) -> None:
        self.repo_root = repo_root
        self.model_usage_db = model_usage_db
        self.timeout_ms = timeout_ms
        self.validation_timeout = validation_timeout

    def run(self, task: dict[str, Any], *, repair: bool = False) -> dict[str, Any]:
        workspace = Path(str(task["workspace"]))
        run_dir = workspace / ".codex/zcode/runs"
        packet_dir = workspace / ".codex/zcode/packets"
        run_dir.mkdir(parents=True, exist_ok=True)
        packet_dir.mkdir(parents=True, exist_ok=True)
        route_sequence = len(task.get("route_attempts", [])) + 1
        packet_path = packet_dir / f"{task['task_id']}-route-{route_sequence}.json"
        prompt_path = packet_path.with_suffix(".prompt.txt")
        route_dir = run_dir / f"{task['task_id']}-route-{route_sequence}"
        route_dir.mkdir(parents=True, exist_ok=True)
        run_path = route_dir / "zcode-run.json"
        task["packet_path"] = str(packet_path)
        task["run_path"] = str(run_path)

        objective = str(task["objective"])
        if repair:
            objective = (
                f"{objective}\n\nRepair only the previous validation failure. "
                "Keep the same allowed file and make the smallest correction."
            )
        packet_cmd = [
            sys.executable,
            str(self.repo_root / "tools/zcode_supervisor/zcode_supervisor.py"),
            "packet",
            "--workspace",
            str(workspace),
            "--objective",
            objective,
            "--allowed",
            str(task["allowed_file"]),
            "--validation",
            str(task["validation"]),
            "--effort",
            "max",
            "--task-class",
            str(task["task_class"]),
            "--risk-budget",
            "low",
            "--workspace-kind",
            "fixture",
            "--max-changed-files",
            "1",
            "--strict-contract-rubric-id",
            str(task["strict_rubric_id"]),
            "--strict-contract-task-id",
            str(task["task_id"]),
            "--strict-contract-risk-level",
            str(task["strict_risk_level"]),
            "--out",
            str(packet_path),
            "--prompt-out",
            str(prompt_path),
        ]
        self.run_json_command(packet_cmd, cwd=self.repo_root, timeout=60)

        env = os.environ.copy()
        env.pop("GIT_DIR", None)
        env.pop("GIT_WORK_TREE", None)
        env.pop("ZCODE_PROVIDER_USAGE_LEDGER", None)
        run_cmd = [
            "node",
            str(self.repo_root / "tools/zcode_control/zcodectl.mjs"),
            "run-packet",
            "--packet",
            str(packet_path),
            "--mode",
            "edit",
            "--max-attempts",
            "1",
            "--retry-delay-ms",
            "0",
            "--timeout-ms",
            str(self.timeout_ms),
            "--validation-timeout",
            str(self.validation_timeout),
            "--no-repair-validation",
            "--usage-snapshot-source",
            "none",
            "--model-usage-db",
            str(self.model_usage_db),
            "--vision-preflight",
            "off",
            "--json",
            "--out",
            str(run_path),
        ]
        return self.run_json_command(run_cmd, cwd=self.repo_root, timeout=(self.timeout_ms // 1000) + self.validation_timeout + 60, env=env)

    @staticmethod
    def run_json_command(
        command: list[str],
        *,
        cwd: Path,
        timeout: int,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        parsed: dict[str, Any] | None = None
        if completed.stdout.strip():
            try:
                candidate = json.loads(completed.stdout)
                if isinstance(candidate, dict):
                    parsed = candidate
            except json.JSONDecodeError:
                parsed = None
        out_index = command.index("--out") + 1 if "--out" in command else -1
        if out_index > 0:
            out_path = Path(command[out_index])
            if out_path.exists():
                try:
                    parsed = read_json(out_path)
                except (OSError, json.JSONDecodeError, ValueError):
                    pass
        if parsed is None:
            parsed = {
                "ok": False,
                "status": "command_json_missing",
                "exit_code": completed.returncode,
                "stdout_tail": completed.stdout[-2000:],
                "stderr_tail": completed.stderr[-2000:],
            }
        parsed.setdefault("command_exit_code", completed.returncode)
        if completed.returncode != 0:
            parsed.setdefault("ok", False)
        return parsed


class ResumableRunner:
    def __init__(
        self,
        *,
        repo_root: Path,
        state_path: Path,
        final_outcome_path: Path,
        readiness_path: Path,
        manifest_path: Path,
        task_count: int,
        work_root: Path,
        executor: Any,
        sleep_fn: Callable[[int], None] = time.sleep,
        cooldown_seconds: tuple[int, ...] = DEFAULT_COOLDOWN_SECONDS,
        max_total_attempts: int = 40,
        max_attempts_per_task: int = 2,
        max_cooldown_cycles: int = 4,
        until_done: bool = False,
        max_wall_clock_hours: float | None = None,
        provider_cooldown_seconds_arg: int | None = None,
        provider_max_pause_cycles: int | None = None,
    ) -> None:
        self.repo_root = repo_root
        self.state_path = state_path
        self.final_outcome_path = final_outcome_path
        self.readiness_path = readiness_path
        self.manifest_path = manifest_path
        self.task_count = task_count
        self.work_root = work_root
        self.executor = executor
        self.sleep_fn = sleep_fn
        self.cooldown_seconds = cooldown_seconds
        self.max_total_attempts = max_total_attempts
        self.max_attempts_per_task = max_attempts_per_task
        self.max_cooldown_cycles = max_cooldown_cycles
        self.until_done = until_done
        self.max_wall_clock_hours = max_wall_clock_hours
        self.provider_cooldown_seconds_arg = provider_cooldown_seconds_arg
        self.provider_max_pause_cycles = provider_max_pause_cycles or max_cooldown_cycles
        self.provider_pause_cycles_this_run = 0

    def resume_command(self) -> str:
        parts = [
            "python3",
            "scripts/run_zcode_20_resumable.py",
            "--resume",
            "--until-done",
            "--task-count",
            str(self.task_count),
            "--state",
            str(self.state_path),
            "--final-outcome",
            str(self.final_outcome_path),
        ]
        if self.max_wall_clock_hours is not None:
            parts.extend(["--max-wall-clock-hours", str(self.max_wall_clock_hours)])
        if self.provider_cooldown_seconds_arg is not None:
            parts.extend(["--provider-cooldown-seconds", str(self.provider_cooldown_seconds_arg)])
        if self.provider_max_pause_cycles is not None:
            parts.extend(["--provider-max-pause-cycles", str(self.provider_max_pause_cycles)])
        return " ".join(parts)

    def load_or_create_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            state = read_json(self.state_path)
            return normalize_state(
                state,
                task_count=self.task_count,
                work_root=self.work_root,
                resume_command=self.resume_command(),
            )
        return create_initial_state(
            task_count=self.task_count,
            work_root=self.work_root,
            resume_command=self.resume_command(),
        )

    def save_state(self, state: dict[str, Any]) -> None:
        recompute_state_counts(state)
        write_json(self.state_path, state)

    def repair_saved_usage_capture_failures(self, state: dict[str, Any]) -> None:
        if state.get("usage_capture_global_failure") is not True:
            return
        state["usage_capture_repair_active"] = True
        repaired_any = False
        for task in state.get("tasks", []):
            if not isinstance(task, dict):
                continue
            if task.get("blocker") != "worker_usage_unavailable_after_zcode_run":
                continue
            run_path_value = task.get("run_path")
            if not isinstance(run_path_value, str):
                continue
            run_path = Path(run_path_value)
            before_path = run_path.parent / "zcode-model-usage-before.json"
            if not before_path.exists():
                task["usage_capture_repair_blocker"] = "zcode_model_usage_before_marker_missing"
                continue
            ledger_dir = run_path.parent / f"{run_path.stem}-usage-repair"
            ledger_path = ledger_dir / "worker-usage.jsonl"
            before = read_json(before_path)
            model_usage_db = getattr(self.executor, "model_usage_db", DEFAULT_MODEL_USAGE_DB)
            after = capture_after_delta(before, db_path=Path(model_usage_db), ledger_path=ledger_path, row_dir=ledger_dir)
            task["usage_capture_repair_result"] = after
            if after.get("status") != "measured" or positive_int(after.get("total_tokens")) is None:
                task["usage_capture_repair_blocker"] = after.get("no_usage_reason") or "zcode_model_usage_repair_unavailable"
                continue
            run = read_json(run_path)
            source_path = relative_usage_path(ledger_path, run_path)
            usage_accounting = {
                "usage_available": True,
                "no_usage_reason": None,
                "tokens_source": DB_DELTA_SOURCE_TYPE,
                "tokens_used": after.get("total_tokens"),
                "tokens_total": after.get("total_tokens"),
                "worker_usage_status": "measured",
                "worker_usage_unit": "tokens",
                "worker_total_tokens": after.get("total_tokens"),
                "worker_usage_source_path": source_path,
                "worker_usage_capture_method": DB_DELTA_SOURCE_TYPE,
                "before_max_rowid": after.get("before_max_rowid"),
                "after_max_rowid": after.get("after_max_rowid"),
                "row_ids": after.get("row_ids") if isinstance(after.get("row_ids"), list) else [],
                "row_count": after.get("row_count"),
                "rows": after.get("rows") if isinstance(after.get("rows"), list) else [],
                "total_tokens": after.get("total_tokens"),
                "usage": after.get("usage"),
                "db_delta_status": "measured",
                "repaired_from_saved_state": True,
            }
            run.update(
                {
                    "usage_available": True,
                    "no_usage_reason": None,
                    "worker_usage_status": "measured",
                    "worker_usage_unit": "tokens",
                    "worker_total_tokens": after.get("total_tokens"),
                    "worker_usage_source_path": source_path,
                    "worker_usage_capture_method": DB_DELTA_SOURCE_TYPE,
                    "row_ids": usage_accounting["row_ids"],
                    "row_count": after.get("row_count"),
                    "rows": usage_accounting["rows"],
                    "total_tokens": after.get("total_tokens"),
                    "usage_accounting": usage_accounting,
                }
            )
            write_json(run_path, run)
            self.apply_run_fields(task, run)
            task["usage_capture_repaired"] = True
            if final_validation_rc(run) == 0 and strict_accepted(run) is not True:
                task["blocker"] = "strict_contract_not_accepted_after_usage_capture_repair"
            else:
                task["blocker"] = exact_blocker(run)
            task["status"] = "failed"
            repaired_any = True
        if repaired_any:
            state["usage_capture_global_failure"] = False
            state["terminal_blocker"] = None
            state["final_outcome"] = None
            state["usage_capture_diagnosis"] = (
                "resumable runner reused one run directory, leaving worker-usage.jsonl from "
                "provider pause; saved run was repaired from DB-delta rows into a route-local ledger"
            )

    def dry_run(self) -> dict[str, Any]:
        state = create_initial_state(
            task_count=self.task_count,
            work_root=self.work_root,
            resume_command=self.resume_command(),
        )
        self.save_state(state)
        readiness = self.build_readiness(state, dry_run=True)
        write_json(self.readiness_path, readiness)
        final = self.build_final_outcome(state, final_outcome="dry_run_preflight_ready")
        write_json(self.final_outcome_path, final)
        return final

    def run(self) -> dict[str, Any]:
        started_monotonic = time.monotonic()
        self.provider_pause_cycles_this_run = 0
        state = self.load_or_create_state()
        self.repair_saved_usage_capture_failures(state)
        state["limits"] = {
            "max_total_attempts": self.max_total_attempts,
            "max_attempts_per_task": self.max_attempts_per_task,
            "max_cooldown_cycles": self.max_cooldown_cycles,
            "provider_max_pause_cycles": self.provider_max_pause_cycles,
            "max_wall_clock_hours": self.max_wall_clock_hours,
            "until_done": self.until_done,
            "cooldown_seconds": list(self.cooldown_seconds),
        }
        write_json(self.readiness_path, self.build_readiness(state, dry_run=False))
        self.save_state(state)
        if state.get("usage_capture_global_failure") is True:
            blocker = str(state.get("terminal_blocker") or "worker_usage_unavailable_after_repair")
            return self.finish_terminal(state, OUTCOME_USAGE_FAILURE, blocker)
        if state.get("final_outcome") in {OUTCOME_STRICT_HARNESS_FAILURE, OUTCOME_CODEX_TOUCHED}:
            blocker = str(state.get("terminal_blocker") or state.get("final_outcome"))
            return self.finish_terminal(state, str(state["final_outcome"]), blocker)
        if self.target_artifacts_dirty():
            return self.finish_terminal(state, OUTCOME_CODEX_TOUCHED, "tracked_target_artifact_dirty_before_run")

        while True:
            progressed = False
            provider_paused = False
            for task in state["tasks"]:
                if not self.task_eligible(task):
                    continue
                if self.wall_clock_bound_reached(started_monotonic):
                    state["wall_clock_bound_reached"] = True
                    self.save_state(state)
                    if int_or_zero(state.get("provider_pause_count")) > 0:
                        return self.provider_paused_later(state, "wall_clock_bound_reached")
                    return self.finish_terminal(state, OUTCOME_STRICT_HARNESS_FAILURE, "wall_clock_bound_reached")
                if int_or_zero(state.get("total_attempts")) >= self.max_total_attempts:
                    return self.finish_terminal(state, OUTCOME_STRICT_HARNESS_FAILURE, "max_total_attempts_exhausted")
                if int_or_zero(task.get("attempts")) >= self.max_attempts_per_task:
                    task["status"] = "failed"
                    task["blocker"] = task.get("blocker") or "max_attempts_per_task_exhausted"
                    self.save_state(state)
                    continue
                progressed = True
                result = self.run_task_attempt(state, task)
                if result in {OUTCOME_CODEX_TOUCHED, OUTCOME_USAGE_FAILURE, OUTCOME_STRICT_HARNESS_FAILURE}:
                    return self.finish_terminal(state, result, str(state.get("terminal_blocker") or result))
                if result == "repair_planned":
                    break
                if result == "provider_paused":
                    provider_paused = True
                    break

            if self.all_tasks_terminal(state):
                return self.finish_completed(state)
            if provider_paused:
                if not self.cooldown_or_pause_later(state, started_monotonic):
                    return self.provider_paused_later(state, "provider_rate_limit_1302_pause_bound_reached")
                continue
            if not progressed:
                return self.finish_completed(state)

    def task_eligible(self, task: dict[str, Any]) -> bool:
        if task.get("status") == "strict_green":
            return False
        if task.get("status") == "failed":
            return False
        return task.get("status") in {"pending", "provider_blocked", "running"}

    def all_tasks_terminal(self, state: dict[str, Any]) -> bool:
        return all(task.get("status") in {"strict_green", "failed"} for task in state["tasks"])

    def run_task_attempt(self, state: dict[str, Any], task: dict[str, Any]) -> str | None:
        self.prepare_workspace(task)
        if self.target_artifacts_dirty():
            state["codex_touched_target_artifact"] = True
            state["terminal_blocker"] = "tracked_target_artifact_dirty_before_task"
            self.save_state(state)
            return OUTCOME_CODEX_TOUCHED

        task["status"] = "running"
        task["started_at"] = utc_now()
        self.save_state(state)
        repair = bool(task.get("blocker")) and safe_blocker_for_repair(str(task.get("blocker")))
        attempt_number = int_or_zero(task.get("attempts")) + 1
        run = self.executor.run(task, repair=repair)
        is_preimplementation_provider_pause = provider_blocked_before_implementation(run)
        if not is_preimplementation_provider_pause:
            task["attempts"] = attempt_number
        attempt_record = compact_attempt_record(
            run,
            task,
            attempt_number if not is_preimplementation_provider_pause else None,
            provider_pause_event=(
                int_or_zero(state.get("provider_pause_count")) + 1
                if is_preimplementation_provider_pause
                else None
            ),
            task_attempt_consumed=not is_preimplementation_provider_pause,
        )
        task["route_attempts"].append(attempt_record)
        task["last_run"] = attempt_record
        task["run_path"] = run.get("run_path") or task.get("run_path")
        state["last_route_output_path"] = task.get("run_path")
        task["provider_rate_limit_1302_count"] = int_or_zero(task.get("provider_rate_limit_1302_count")) + provider_rate_limit_count(run)

        if self.target_artifacts_dirty():
            state["codex_touched_target_artifact"] = True
            state["terminal_blocker"] = "tracked_target_artifact_dirty_after_task"
            self.save_state(state)
            return OUTCOME_CODEX_TOUCHED

        if is_preimplementation_provider_pause:
            task["status"] = "pending"
            task["blocker"] = PROVIDER_PAUSED_STATUS
            task["safe_to_retry_later"] = True
            state["provider_pause_status"] = PROVIDER_PAUSED_STATUS
            state["provider_pause_count"] = int_or_zero(state.get("provider_pause_count")) + 1
            state["last_provider_code"] = run.get("provider_code") or get_nested(run, "provider", "provider_code") or "1302"
            state["last_route_output_path"] = task.get("run_path")
            state["current_task_id"] = task.get("task_id")
            self.save_state(state)
            return "provider_paused"

        if is_provider_rate_limit_1302(run):
            task["status"] = "provider_blocked"
            task["blocker"] = exact_blocker(run)
            task["safe_to_retry_later"] = True
            self.save_state(state)
            return None

        if zcode_ran_without_provider_block(run) and not usage_measured(run):
            self.apply_run_fields(task, run)
            task["status"] = "failed"
            task["blocker"] = run.get("no_usage_reason") or "worker_usage_unavailable_after_zcode_run"
            state["usage_capture_global_failure"] = True
            state["usage_capture_repair_active"] = True
            state["usage_capture_diagnosis"] = (
                "repaired DB-delta worker usage capture path was active, but no measured "
                "worker usage rows or safe source could be attributed"
            )
            state["terminal_blocker"] = task["blocker"]
            self.save_state(state)
            return OUTCOME_USAGE_FAILURE

        self.apply_run_fields(task, run)
        if run_is_strict_green(run):
            task["status"] = "strict_green"
            task["blocker"] = None
            task["completed_at"] = utc_now()
            self.save_state(state)
            return None

        blocker = exact_blocker(run)
        if safe_validation_repair(run) and int_or_zero(task.get("attempts")) < self.max_attempts_per_task:
            task["status"] = "pending"
            task["blocker"] = f"validation_failed_repair_planned:{blocker}"
            task["repair_attempted"] = True
            self.save_state(state)
            return "repair_planned"
        else:
            task["status"] = "failed"
            task["blocker"] = blocker
        self.save_state(state)
        return None

    def apply_run_fields(self, task: dict[str, Any], run: dict[str, Any]) -> None:
        task["worker_usage_status"] = usage_value(run, "worker_usage_status")
        task["worker_usage_unit"] = usage_value(run, "worker_usage_unit")
        task["worker_total_tokens"] = usage_value(run, "worker_total_tokens")
        task["worker_usage_source_path"] = usage_value(run, "worker_usage_source_path")
        task["worker_usage_capture_method"] = usage_value(run, "worker_usage_capture_method")
        row_ids = usage_value(run, "row_ids")
        task["row_ids"] = row_ids if isinstance(row_ids, list) else []
        rows = usage_value(run, "rows")
        task["worker_usage_rows"] = rows if isinstance(rows, list) else []
        task["zcode_implemented"] = (changed_count(run) or 0) > 0
        task["strict_accepted"] = strict_accepted(run)
        task["final_validation_rc"] = final_validation_rc(run)
        task["route_used"] = "zcode_cli"

    def prepare_workspace(self, task: dict[str, Any]) -> None:
        workspace = self.work_root / "tasks" / str(task["task_id"])
        if not workspace.exists():
            source = self.repo_root / "benchmarks/hard-token-fixtures" / str(task["fixture"])
            if not source.is_dir():
                raise ResumableBenchmarkError(f"fixture missing: {source}")
            workspace.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, workspace)
            self.install_repo(workspace)
        task["workspace"] = str(workspace)

    def install_repo(self, workspace: Path) -> None:
        command = [
            sys.executable,
            str(self.repo_root / "tools/zcode_supervisor/zcode_supervisor.py"),
            "install-repo",
            "--repo",
            str(workspace),
        ]
        subprocess.run(command, cwd=self.repo_root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def target_artifacts_dirty(self) -> bool:
        command = ["git", "status", "--porcelain", "--", "benchmarks/hard-token-fixtures"]
        completed = subprocess.run(command, cwd=self.repo_root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        return bool(completed.stdout.strip())

    def wall_clock_bound_reached(self, started_monotonic: float) -> bool:
        if self.max_wall_clock_hours is None:
            return False
        return time.monotonic() - started_monotonic >= self.max_wall_clock_hours * 3600

    def cooldown_or_pause_later(self, state: dict[str, Any], started_monotonic: float) -> bool:
        if not self.until_done:
            return False
        if self.provider_pause_cycles_this_run >= self.provider_max_pause_cycles:
            return False
        used = int_or_zero(state.get("cooldown_cycles_used"))
        wait_seconds = self.cooldown_seconds[min(used, len(self.cooldown_seconds) - 1)]
        if self.max_wall_clock_hours is not None:
            elapsed = time.monotonic() - started_monotonic
            if elapsed + wait_seconds > self.max_wall_clock_hours * 3600:
                state["wall_clock_bound_reached"] = True
                self.save_state(state)
                return False
        state["cooldown_cycles_used"] = used + 1
        self.provider_pause_cycles_this_run += 1
        state["cooldown_last_wait_seconds"] = wait_seconds
        state["cooldown_last_started_at"] = utc_now()
        self.save_state(state)
        self.sleep_fn(wait_seconds)
        state["cooldown_last_completed_at"] = utc_now()
        self.save_state(state)
        return True

    def finish_completed(self, state: dict[str, Any]) -> dict[str, Any]:
        self.save_state(state)
        delegated = int_or_zero(state.get("delegated_rows"))
        strict_green = int_or_zero(state.get("strict_green_count"))
        if delegated >= self.task_count and strict_green >= self.task_count:
            return self.finish_terminal(state, OUTCOME_GREEN, None)
        if delegated >= self.task_count:
            return self.finish_terminal(state, OUTCOME_PARTIAL, None)
        return self.finish_terminal(state, OUTCOME_STRICT_HARNESS_FAILURE, "delegated_rows_below_task_count")

    def provider_paused_later(self, state: dict[str, Any], blocker: str) -> dict[str, Any]:
        state["provider_pause_status"] = PROVIDER_PAUSED_STATUS
        return self.finish_terminal(state, OUTCOME_PROVIDER_PAUSED, blocker)

    def finish_terminal(self, state: dict[str, Any], final_outcome: str, blocker: str | None) -> dict[str, Any]:
        if final_outcome not in ALLOWED_OUTCOMES:
            raise ValueError(f"unsupported final outcome: {final_outcome}")
        state["final_outcome"] = final_outcome
        state["terminal_blocker"] = blocker
        self.save_state(state)
        manifest = self.build_manifest(state)
        write_json(self.manifest_path, manifest)
        final = self.build_final_outcome(state, final_outcome=final_outcome, blocker=blocker)
        write_json(self.final_outcome_path, final)
        return final

    def build_readiness(self, state: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "claim_family": CLAIM_FAMILY,
            "dry_run": dry_run,
            "task_count": len(state.get("tasks", [])),
            "tasks": [
                {
                    "task_id": task.get("task_id"),
                    "fixture": task.get("fixture"),
                    "allowed_file": task.get("allowed_file"),
                    "validation": task.get("validation"),
                    "strict_rubric_id": task.get("strict_rubric_id"),
                }
                for task in state.get("tasks", [])
                if isinstance(task, dict)
            ],
            "zcode_required_mode": True,
            "codex_fallback_forbidden": True,
            "production_green_path_enabled": False,
            "direct_mode_default": False,
            "created_at": utc_now(),
        }

    def build_manifest(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "claim_family": CLAIM_FAMILY,
            "benchmark_run_kind": "live_provider_benchmark",
            "task_count": state.get("task_count"),
            "delegated_rows": state.get("delegated_rows"),
            "strict_green_count": state.get("strict_green_count"),
            "failure_count": state.get("failure_count"),
            "provider_rate_limit_count": state.get("provider_rate_limit_count"),
            "provider_pause_status": state.get("provider_pause_status"),
            "provider_pause_count": state.get("provider_pause_count"),
            "last_provider_code": state.get("last_provider_code"),
            "last_route_output_path": state.get("last_route_output_path"),
            "current_task_id": state.get("current_task_id"),
            "cooldown_cycles_used": state.get("cooldown_cycles_used"),
            "wall_clock_bound_reached": state.get("wall_clock_bound_reached") is True,
            "state_file": str(self.state_path),
            "resume_command": state.get("resume_command"),
            "rows": summary_rows(state),
            "production_green_path_enabled": False,
            "direct_mode_default": False,
            "created_at": utc_now(),
        }

    def build_final_outcome(
        self,
        state: dict[str, Any],
        *,
        final_outcome: str,
        blocker: str | None = None,
    ) -> dict[str, Any]:
        measured = measured_tasks(state)
        rows = summary_rows(state)
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "final_outcome": final_outcome,
            "terminal_blocker": blocker,
            "claim_family": CLAIM_FAMILY,
            "task_count": state.get("task_count"),
            "delegated_rows": state.get("delegated_rows"),
            "strict_green_count": state.get("strict_green_count"),
            "failure_count": state.get("failure_count"),
            "provider_rate_limit_count": state.get("provider_rate_limit_count"),
            "provider_pause_status": state.get("provider_pause_status"),
            "provider_pause_count": state.get("provider_pause_count"),
            "last_provider_code": state.get("last_provider_code"),
            "last_route_output_path": state.get("last_route_output_path"),
            "current_task_id": state.get("current_task_id"),
            "cooldown_cycles_used": state.get("cooldown_cycles_used"),
            "wall_clock_bound_reached": state.get("wall_clock_bound_reached") is True,
            "total_attempts": state.get("total_attempts"),
            "route_used_summary": route_used_summary(state),
            "state_reused": state.get("state_reused") is True,
            "usage_capture_repair_active": state.get("usage_capture_repair_active") is True,
            "usage_capture_diagnosis": state.get("usage_capture_diagnosis"),
            "zcode_implemented": any(task.get("zcode_implemented") for task in state.get("tasks", [])),
            "codex_touched_target_artifact": state.get("codex_touched_target_artifact") is True,
            "worker_token_measured_count": len(measured),
            "worker_total_tokens": sum(int(task["worker_total_tokens"]) for task in measured),
            "worker_usage_unit": "tokens" if measured else None,
            "worker_usage_source_paths": [
                task.get("worker_usage_source_path") for task in measured if task.get("worker_usage_source_path")
            ],
            "row_ids": [row.get("row_id") for row in rows if row.get("row_id")],
            "rows": rows,
            "provider_pause_events": provider_pause_events(state),
            "exact_blockers": exact_blockers(state, blocker),
            "state_file": str(self.state_path),
            "manifest_path": str(self.manifest_path),
            "readiness_path": str(self.readiness_path),
            "resume_command": state.get("resume_command"),
            "production_green_path_enabled": False,
            "direct_mode_default": False,
            "strict_gate_weakened": False,
            "glm_5_2_fixed": True,
            "glm_4_7_fallback": False,
            "time_of_day_gate": False,
            "created_at": utc_now(),
        }


def safe_blocker_for_repair(blocker: str) -> bool:
    return blocker.startswith("validation_failed_repair_planned")


def compact_attempt_record(
    run: dict[str, Any],
    task: dict[str, Any],
    attempt_number: int | None,
    *,
    provider_pause_event: int | None = None,
    task_attempt_consumed: bool = True,
) -> dict[str, Any]:
    return {
        "task_id": task.get("task_id"),
        "attempt": attempt_number,
        "route_sequence": len(task.get("route_attempts", [])) + 1,
        "task_attempt_consumed": task_attempt_consumed,
        "provider_pause_event": provider_pause_event,
        "route_used": "zcode_cli",
        "codex_fallback_implementation": False,
        "ok": run.get("ok"),
        "status": run.get("status"),
        "exit_code": run.get("exit_code"),
        "provider_error_kind": run.get("provider_error_kind"),
        "provider_code": run.get("provider_code"),
        "provider_rate_limit_1302": is_provider_rate_limit_1302(run),
        "provider_rate_limit_1302_count": provider_rate_limit_count(run),
        "timed_out": run.get("timed_out") is True,
        "changed_count": changed_count(run),
        "strict_accepted": strict_accepted(run),
        "final_validation_rc": final_validation_rc(run),
        "worker_usage_status": usage_value(run, "worker_usage_status"),
        "worker_usage_unit": usage_value(run, "worker_usage_unit"),
        "worker_total_tokens": usage_value(run, "worker_total_tokens"),
        "worker_usage_source_path": usage_value(run, "worker_usage_source_path"),
        "worker_usage_capture_method": usage_value(run, "worker_usage_capture_method"),
        "run_path": run.get("run_path") or task.get("run_path"),
        "row_ids": usage_value(run, "row_ids") if isinstance(usage_value(run, "row_ids"), list) else [],
    }


def measured_tasks(state: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = [task for task in state.get("tasks", []) if isinstance(task, dict)]
    return [
        task
        for task in tasks
        if task.get("worker_usage_status") == "measured"
        and task.get("worker_usage_unit") == "tokens"
        and positive_int(task.get("worker_total_tokens")) is not None
        and task.get("worker_usage_source_path")
    ]


def summary_rows(state: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for task in state.get("tasks", []):
        if not isinstance(task, dict) or int_or_zero(task.get("attempts")) == 0:
            continue
        rows.append(
            {
                "row_id": task.get("task_id"),
                "task": task.get("task_id"),
                "fixture": task.get("fixture"),
                "status": task.get("status"),
                "attempts": task.get("attempts"),
                "mode": "zcode_delegated",
                "route_used": "zcode_cli",
                "claim_family": CLAIM_FAMILY,
                "blocker": task.get("blocker"),
                "strict_accepted": task.get("strict_accepted"),
                "final_validation_rc": task.get("final_validation_rc"),
                "worker_usage_status": task.get("worker_usage_status"),
                "worker_usage_unit": task.get("worker_usage_unit"),
                "worker_total_tokens": task.get("worker_total_tokens"),
                "worker_usage_source_path": task.get("worker_usage_source_path"),
                "worker_usage_capture_method": task.get("worker_usage_capture_method"),
                "row_ids": task.get("row_ids") or [],
                "worker_usage_rows": task.get("worker_usage_rows") or [],
                "zcode_implemented": task.get("zcode_implemented") is True,
                "provider_rate_limit_1302_count": task.get("provider_rate_limit_1302_count"),
                "quality": "pass" if task.get("status") == "strict_green" else "fail",
            }
        )
    return rows


def provider_pause_events(state: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for task in state.get("tasks", []):
        if not isinstance(task, dict):
            continue
        for record in task.get("route_attempts", []):
            if not isinstance(record, dict) or not route_record_is_provider_pause(record):
                continue
            events.append(
                {
                    "task_id": task.get("task_id"),
                    "provider_pause_event": record.get("provider_pause_event"),
                    "route_sequence": record.get("route_sequence"),
                    "provider_code": record.get("provider_code"),
                    "provider_error_kind": record.get("provider_error_kind"),
                    "provider_rate_limit_1302_count": record.get("provider_rate_limit_1302_count"),
                    "run_path": record.get("run_path"),
                    "row_ids": record.get("row_ids") if isinstance(record.get("row_ids"), list) else [],
                }
            )
    return events


def exact_blockers(state: dict[str, Any], terminal_blocker: str | None) -> list[str]:
    blockers = []
    if terminal_blocker:
        blockers.append(terminal_blocker)
    for task in state.get("tasks", []):
        if isinstance(task, dict) and task.get("blocker"):
            blockers.append(str(task["blocker"]))
    return sorted(set(blockers))


def route_used_summary(state: dict[str, Any]) -> dict[str, int]:
    summary = {
        "zcode_cli": 0,
        "zcode_app_cdp": 0,
        "codex_fallback": 0,
        "provider_blocked": 0,
        "provider_paused": 0,
        "failed": 0,
        "strict_green": 0,
    }
    for task in state.get("tasks", []):
        if not isinstance(task, dict):
            continue
        route_attempts = [record for record in task.get("route_attempts", []) if isinstance(record, dict)]
        summary["zcode_cli"] += sum(1 for record in route_attempts if record.get("route_used") == "zcode_cli")
        summary["codex_fallback"] += sum(1 for record in route_attempts if record.get("route_used") == "codex_fallback")
        summary["provider_paused"] += sum(1 for record in route_attempts if route_record_is_provider_pause(record))
        if int_or_zero(task.get("attempts")) == 0:
            continue
        status = str(task.get("status"))
        if status in summary:
            summary[status] += 1
    return summary


def final_outcome_errors(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    outcome = payload.get("final_outcome")
    if outcome not in ALLOWED_OUTCOMES:
        errors.append("final_outcome:unsupported")
        return errors
    if payload.get("claim_family") != CLAIM_FAMILY:
        errors.append("claim_family:not_zcode_required_20_live")
    if int_or_zero(payload.get("task_count")) < 20:
        errors.append("task_count:lt_20")
    if int_or_zero(payload.get("total_attempts")) > 40:
        errors.append("total_attempts:gt_40")
    if payload.get("production_green_path_enabled") is not False:
        errors.append("production_green_path_enabled:not_false")
    if payload.get("direct_mode_default") is not False:
        errors.append("direct_mode_default:not_false")
    if payload.get("strict_gate_weakened") is not False:
        errors.append("strict_gate_weakened:not_false")
    if payload.get("glm_5_2_fixed") is not True:
        errors.append("glm_5_2_fixed:not_true")
    if payload.get("glm_4_7_fallback") is not False:
        errors.append("glm_4_7_fallback:not_false")
    if payload.get("time_of_day_gate") is not False:
        errors.append("time_of_day_gate:not_false")
    if payload.get("route_used_summary", {}).get("codex_fallback", 0) != 0:
        errors.append("route_used_summary:codex_fallback_nonzero")
    if outcome != OUTCOME_CODEX_TOUCHED and payload.get("codex_touched_target_artifact") is not False:
        errors.append("codex_touched_target_artifact:not_false")
    if outcome in {OUTCOME_GREEN, OUTCOME_PARTIAL}:
        if int_or_zero(payload.get("delegated_rows")) < 20:
            errors.append("delegated_rows:lt_20")
        if int_or_zero(payload.get("worker_token_measured_count")) < 1:
            errors.append("worker_token_measured_count:lt_1")
        if payload.get("worker_usage_unit") != "tokens":
            errors.append("worker_usage_unit:not_tokens")
        if positive_int(payload.get("worker_total_tokens")) is None:
            errors.append("worker_total_tokens:not_positive")
        if not payload.get("worker_usage_source_paths"):
            errors.append("worker_usage_source_paths:missing")
    if outcome == OUTCOME_GREEN:
        if payload.get("strict_green_count") != payload.get("task_count"):
            errors.append("strict_green_count:not_task_count")
    if outcome == OUTCOME_PROVIDER_PAUSED:
        if not payload.get("resume_command"):
            errors.append("resume_command:missing")
        if not payload.get("state_file"):
            errors.append("state_file:missing")
        if int_or_zero(payload.get("provider_rate_limit_count")) < 1:
            errors.append("provider_rate_limit_count:lt_1")
        if int_or_zero(payload.get("provider_pause_count")) < 1:
            errors.append("provider_pause_count:lt_1")
        if payload.get("provider_pause_status") != PROVIDER_PAUSED_STATUS:
            errors.append("provider_pause_status:not_provider_paused_rate_limit_1302")
        if not payload.get("last_provider_code"):
            errors.append("last_provider_code:missing")
        if not payload.get("last_route_output_path"):
            errors.append("last_route_output_path:missing")
        if not payload.get("current_task_id"):
            errors.append("current_task_id:missing")
    if outcome in {OUTCOME_USAGE_FAILURE, OUTCOME_STRICT_HARNESS_FAILURE, OUTCOME_CODEX_TOUCHED}:
        if not payload.get("terminal_blocker"):
            errors.append("terminal_blocker:missing")
    if outcome == OUTCOME_USAGE_FAILURE:
        if payload.get("usage_capture_repair_active") is not True:
            errors.append("usage_capture_repair_active:not_true")
        if not payload.get("usage_capture_diagnosis"):
            errors.append("usage_capture_diagnosis:missing")
    return errors


def validate_final_outcome(payload: dict[str, Any]) -> None:
    errors = final_outcome_errors(payload)
    if errors:
        raise ValueError("; ".join(errors))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-count", type=int, default=20)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--final-outcome", type=Path, default=DEFAULT_FINAL_OUTCOME)
    parser.add_argument("--readiness", type=Path, default=DEFAULT_READINESS)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    parser.add_argument("--model-usage-db", type=Path, default=DEFAULT_MODEL_USAGE_DB)
    parser.add_argument("--timeout-ms", type=int, default=30 * 60 * 1000)
    parser.add_argument("--validation-timeout", type=int, default=60)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--until-done", action="store_true")
    parser.add_argument("--max-wall-clock-hours", type=float, default=None)
    parser.add_argument("--provider-cooldown-seconds", type=int, default=None)
    parser.add_argument("--provider-max-pause-cycles", type=int, default=None)
    parser.add_argument("--cooldown-scale", type=float, default=1.0)
    parser.add_argument("--max-cooldown-cycles", type=int, default=4)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    repo_root = Path.cwd().resolve()
    if args.provider_cooldown_seconds is not None:
        cooldown = tuple(max(0, args.provider_cooldown_seconds) for _ in DEFAULT_COOLDOWN_SECONDS)
    else:
        cooldown = tuple(max(0, int(seconds * args.cooldown_scale)) for seconds in DEFAULT_COOLDOWN_SECONDS)
    provider_max_pause_cycles = args.provider_max_pause_cycles or args.max_cooldown_cycles
    executor = SubprocessExecutor(
        repo_root=repo_root,
        model_usage_db=args.model_usage_db,
        timeout_ms=args.timeout_ms,
        validation_timeout=args.validation_timeout,
    )
    runner = ResumableRunner(
        repo_root=repo_root,
        state_path=args.state,
        final_outcome_path=args.final_outcome,
        readiness_path=args.readiness,
        manifest_path=args.manifest,
        task_count=args.task_count,
        work_root=args.work_root,
        executor=executor,
        cooldown_seconds=cooldown,
        max_cooldown_cycles=args.max_cooldown_cycles,
        until_done=args.until_done,
        max_wall_clock_hours=args.max_wall_clock_hours,
        provider_cooldown_seconds_arg=args.provider_cooldown_seconds,
        provider_max_pause_cycles=provider_max_pause_cycles,
    )
    final = runner.dry_run() if args.dry_run else runner.run()
    print(json.dumps(final, indent=2, sort_keys=True))
    return 0 if final.get("final_outcome") != OUTCOME_STRICT_HARNESS_FAILURE else 1


if __name__ == "__main__":
    raise SystemExit(main())
