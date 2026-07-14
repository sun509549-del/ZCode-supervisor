"""Strict Contract comparison accounting and reporting helpers."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

try:
    from .metrics import PRIMARY_METRIC, PRIMARY_METRIC_FORMULA, compute_usage_metrics
    from .strict_contract import parse_path_reference
except ImportError:  # pragma: no cover - direct script execution
    from metrics import PRIMARY_METRIC, PRIMARY_METRIC_FORMULA, compute_usage_metrics
    from strict_contract import parse_path_reference


CODEX_OUTPUT_SCHEMA_VERSION = "strict_contract_comparison_codex_output.v1"
CODEX_EXEC_ROW_SCHEMA_VERSION = "strict_contract_comparison_codex_exec_row.v1"
END_TO_END_ACCOUNTING_SCHEMA_VERSION = "strict_contract_end_to_end_accounting.v1"
REPAIR_POLICY_VERSION = "strict_contract_bounded_repair_policy.v1"
VISION_TARGET_CARD_FIXTURE = Path(__file__).resolve().parent / "fixtures/vision-card-latest-target-card.png"
PHASE_USAGE_UNAVAILABLE_REASON = "codex_exec_phase_usage_missing"
ZCODE_WORKER_USAGE_MISSING_REASON = "zcode_cli_usage_missing"
DIRECT_SUPERVISOR_USAGE_MISSING_REASON = "direct_mode_supervisor_codex_orchestration_not_captured"
TOTAL_CODEX_SIDE_USAGE_MISSING_REASON = "total_codex_side_usage_incomplete"
TOTAL_WORKFLOW_USAGE_MISSING_REASON = "total_workflow_usage_incomplete"
ACCEPTABLE_REPAIR_SIZES = {"none", "small polish", "small_polish"}
WORKER_TOKEN_USAGE_NON_TOKEN_REASON = "worker_token_usage_unavailable_non_token_unit"
DEFAULT_REPAIR_ATTEMPT_BUDGET = 1
REPAIR_POLICY_FIELD_NAMES = (
    "repair_policy_version",
    "repair_policy_enabled",
    "repair_attempt_budget",
    "repair_attempts_used",
    "repair_decision",
    "repair_blocker",
    "repair_reason",
    "repair_failure_classification",
    "repair_size_class",
    "repair_allowed_files",
    "repair_forbidden_files",
    "repair_changed_files",
    "repair_changed_lines",
    "repair_stop_reason",
    "post_repair_validation_status",
    "post_repair_strict_accepted",
    "repair_evidence_paths",
)
ALLOWED_REPAIR_FAILURE_CLASSES = {
    "missing_required_artifact",
    "malformed_json",
    "malformed_markdown",
    "small_schema_mismatch",
    "path_reference_normalization",
    "path_range_parser_issue",
    "deterministic_contract_mismatch",
    "validation_failure",
}
BLOCKED_REPAIR_FAILURE_CLASSES = {
    "artifact_quality",
    "codex_repair_size",
    "risk_flags",
    "true_forbidden_file_edit",
    "weakening_strict_gate",
    "weakening_acceptance_criteria",
    "accounting_claim_weakening",
    "usage_unavailable_as_zero",
    "quota_credit_percent_to_tokens",
    "broad_refactor",
    "hidden_external_network_dependency",
    "secret_or_credential_handling",
    "production_rollout_change",
    "direct_mode_default_change",
    "outside_allowed_files",
}
ALLOWED_REPAIR_SIZE_CLASSES = {"none", "small_polish", "small_contract_fix"}
BLOCKED_REPAIR_SIZE_CLASSES = {"broad_blocked"}
TIMEOUT_ACCEPTANCE_OUTCOMES = {
    "timeout_true_worker_hang",
    "timeout_after_valid_artifact_but_acceptance_blocked",
    "acceptance_artifact_quality_failure",
    "supervisor_timeout_classification_bug_fixed",
    "terminal_provider_timeout",
}
REPAIR_SIZE_ALIASES = {
    "small polish": "small_polish",
    "small-polish": "small_polish",
    "small contract fix": "small_contract_fix",
    "small-contract-fix": "small_contract_fix",
    "moderate fix": "broad_blocked",
    "rewrite needed": "broad_blocked",
    "large": "broad_blocked",
    "broad": "broad_blocked",
}


def repair_policy_fields() -> list[str]:
    return list(REPAIR_POLICY_FIELD_NAMES)


def normalize_repair_size_class(value: Any) -> str:
    if value is None:
        return "none"
    text = str(value).strip().lower()
    if not text:
        return "none"
    if text in REPAIR_SIZE_ALIASES:
        return REPAIR_SIZE_ALIASES[text]
    text = text.replace("-", "_").replace(" ", "_")
    return REPAIR_SIZE_ALIASES.get(text, text)


def normalized_paths(values: list[Any] | tuple[Any, ...] | set[Any] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        if not isinstance(value, str):
            continue
        text = value.strip().replace("\\", "/")
        while text.startswith("./"):
            text = text[2:]
        if text and text not in result:
            result.append(text)
    return result


def path_matches(candidate: str, pattern: str) -> bool:
    candidate = candidate.strip().replace("\\", "/").lstrip("/")
    pattern = pattern.strip().replace("\\", "/").lstrip("/")
    if not candidate or not pattern:
        return False
    if candidate == pattern or candidate.startswith(f"{pattern.rstrip('/')}/"):
        return True
    return candidate.endswith(f"/{pattern}")


def repair_changed_line_count(diff_rows: list[dict[str, Any]] | None, changed_lines: Any = None) -> int:
    explicit = int_token(changed_lines)
    if explicit is not None:
        return explicit
    total = 0
    for item in diff_rows or []:
        if not isinstance(item, dict):
            continue
        total += int(item.get("added") or 0)
        total += int(item.get("deleted") or 0)
    return total


def first_blocked_path(
    *,
    changed_files: list[str],
    allowed_files: list[str],
    forbidden_files: list[str],
) -> tuple[str | None, str | None]:
    for changed in changed_files:
        if any(path_matches(changed, forbidden) for forbidden in forbidden_files):
            return "forbidden_file_changed", changed
    if not changed_files:
        return None, None
    if not allowed_files:
        return "outside_allowed_files", changed_files[0]
    for changed in changed_files:
        if not any(path_matches(changed, allowed) for allowed in allowed_files):
            return "outside_allowed_files", changed
    return None, None


def evaluate_repair_policy(
    *,
    repair_failure_classification: Any = None,
    repair_size_class: Any = None,
    attempts_used: int = 0,
    attempt_budget: int = DEFAULT_REPAIR_ATTEMPT_BUDGET,
    changed_files: list[Any] | None = None,
    changed_lines: Any = None,
    allowed_files: list[Any] | None = None,
    forbidden_files: list[Any] | None = None,
    repair_policy_enabled: bool = True,
    repair_reason: str | None = None,
    post_repair_validation_status: str = "not_run",
    post_repair_strict_accepted: bool = False,
    repair_evidence_paths: list[Any] | None = None,
) -> dict[str, Any]:
    classes = normalized_paths(repair_failure_classification if isinstance(repair_failure_classification, list) else [repair_failure_classification])
    size_class = normalize_repair_size_class(repair_size_class)
    normalized_changed = normalized_paths(changed_files)
    normalized_allowed = normalized_paths(allowed_files)
    normalized_forbidden = normalized_paths(forbidden_files)
    attempts = max(0, int(attempts_used or 0))
    budget = max(0, int(attempt_budget or 0))
    changed_line_count = repair_changed_line_count(None, changed_lines)
    blocker: str | None = None
    stop_reason: str | None = None
    decision = "allowed"

    if not repair_policy_enabled:
        decision = "skipped"
        stop_reason = "repair_policy_disabled"
        repair_reason = repair_reason or "repair_policy_disabled"
    elif not classes:
        decision = "skipped"
        stop_reason = "no_repair_needed"
        repair_reason = repair_reason or "no_repair_needed"
    elif attempts >= budget:
        decision = "blocked"
        blocker = "attempt_budget_exhausted"
        stop_reason = "attempt_budget_exhausted"
    else:
        path_blocker, path = first_blocked_path(
            changed_files=normalized_changed,
            allowed_files=normalized_allowed,
            forbidden_files=normalized_forbidden,
        )
        blocked_classes = sorted(set(classes) & BLOCKED_REPAIR_FAILURE_CLASSES)
        unsupported_classes = sorted(set(classes) - ALLOWED_REPAIR_FAILURE_CLASSES - BLOCKED_REPAIR_FAILURE_CLASSES)
        if path_blocker:
            decision = "blocked"
            blocker = f"{path_blocker}:{path}"
            stop_reason = path_blocker
        elif size_class in BLOCKED_REPAIR_SIZE_CLASSES:
            decision = "blocked"
            blocker = "repair_size_too_broad"
            stop_reason = "broad_repair_blocked"
        elif blocked_classes:
            decision = "blocked"
            blocker = ",".join(blocked_classes)
            stop_reason = "blocked_failure_classification"
        elif unsupported_classes:
            decision = "blocked"
            blocker = ",".join(unsupported_classes)
            stop_reason = "unsupported_failure_classification"
        elif size_class not in ALLOWED_REPAIR_SIZE_CLASSES:
            decision = "blocked"
            blocker = f"unsupported_repair_size_class:{size_class}"
            stop_reason = "unsupported_repair_size_class"
        else:
            repair_reason = repair_reason or ",".join(classes)

    if decision == "blocked" and repair_reason is None:
        repair_reason = blocker

    return {
        "repair_policy_version": REPAIR_POLICY_VERSION,
        "repair_policy_enabled": repair_policy_enabled,
        "repair_attempt_budget": budget,
        "repair_attempts_used": attempts,
        "repair_decision": decision,
        "repair_blocker": blocker,
        "repair_reason": repair_reason,
        "repair_failure_classification": classes,
        "repair_size_class": size_class,
        "repair_allowed_files": normalized_allowed,
        "repair_forbidden_files": normalized_forbidden,
        "repair_changed_files": normalized_changed,
        "repair_changed_lines": changed_line_count,
        "repair_stop_reason": stop_reason,
        "post_repair_validation_status": post_repair_validation_status,
        "post_repair_strict_accepted": bool(post_repair_strict_accepted),
        "repair_evidence_paths": normalized_paths(repair_evidence_paths),
    }


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def codex_output_schema() -> dict[str, Any]:
    observed_rc_properties = {
        key: {"type": ["integer", "null", "string", "boolean"]}
        for key in ("codex_rc", "launcher_rc", "route_rc", "acceptance_rc", "final_validation_rc", "validation_rc")
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "task", "arm", "phase", "status", "observed_rc", "notes"],
        "properties": {
            "schema_version": {"type": "string", "const": CODEX_OUTPUT_SCHEMA_VERSION},
            "task": {"type": "string"},
            "arm": {"type": "string"},
            "phase": {"type": "string"},
            "status": {"type": "string", "enum": ["pass", "fail", "blocked", "unknown"]},
            "observed_rc": {
                "type": "object",
                "additionalProperties": False,
                "required": list(observed_rc_properties),
                "properties": observed_rc_properties,
            },
            "notes": {"type": "array", "items": {"type": "string"}},
        },
    }


def ensure_codex_output_schema(report_dir: Path) -> Path:
    path = report_dir / "codex-output.schema.json"
    if not path.exists():
        write_json(path, codex_output_schema())
    return path


def fixed_json_instruction(task: str, arm: str, phase: str) -> str:
    return (
        "Final response contract: return only a JSON object matching "
        "`codex-output.schema.json`. No Markdown, no prose outside JSON. "
        f"Use schema_version={CODEX_OUTPUT_SCHEMA_VERSION!r}, task={task!r}, "
        f"arm={arm!r}, phase={phase!r}. Put any rc values you observed in "
        "`observed_rc`; include every rc key from the schema and use null for unknown values. Keep notes short."
    )


def compact_usage(usage: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(usage, dict):
        return {
            "uncached_input": None,
            "output": None,
            "reasoning_output": None,
            PRIMARY_METRIC: None,
        }
    return {
        "uncached_input": usage.get("uncached_input_tokens"),
        "output": usage.get("output_tokens"),
        "reasoning_output": usage.get("reasoning_output_tokens"),
        PRIMARY_METRIC: usage.get(PRIMARY_METRIC),
    }


def zero_usage_metrics() -> dict[str, Any]:
    usage = compute_usage_metrics(input_tokens=0, cached_input_tokens=0, output_tokens=0, reasoning_output_tokens=0)
    return {**usage, "usage_missing": False, "usage_anomaly": False, "turn_completed_usage_count": 0}


def unavailable_usage_metrics(reason: str) -> dict[str, Any]:
    return {
        "input_tokens": None,
        "cached_input_tokens": None,
        "uncached_input_tokens": None,
        "output_tokens": None,
        "reasoning_output_tokens": None,
        "total_in_out": None,
        "total_plus_reasoning": None,
        "uncached_plus_reasoning": None,
        PRIMARY_METRIC: None,
        "usage_missing": True,
        "no_usage_reason": reason,
    }


def create_vision_fixture(base: Path, expected_path: Path) -> None:
    if base.exists():
        shutil.rmtree(base)
    (base / "src").mkdir(parents=True)
    (base / "screenshots").mkdir(parents=True)

    if not VISION_TARGET_CARD_FIXTURE.is_file():
        raise FileNotFoundError(f"missing vision card fixture: {VISION_TARGET_CARD_FIXTURE}")
    shutil.copyfile(VISION_TARGET_CARD_FIXTURE, base / "screenshots/target-card.png")

    write_text(
        base / "README.md",
        "# Vision Card Contract\n\nUse `screenshots/target-card.png` as the source of truth.\n",
    )
    write_text(
        base / "src/cardSpec.js",
        """export const CARD_SPEC = {
  title: "",
  status: "",
  metric: "",
  trend: "",
  accentColor: "#000000",
  statusColor: "#000000",
  cta: "",
};
""",
    )
    write_text(
        base / "validate.mjs",
        """import fs from "node:fs";
import { CARD_SPEC } from "./src/cardSpec.js";

const expected = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
for (const [key, value] of Object.entries(expected)) {
  if (CARD_SPEC[key] !== value) {
    console.error(`${key}: expected ${value}, got ${CARD_SPEC[key]}`);
    process.exit(1);
  }
}
console.log("vision card spec ok");
""",
    )
    write_text(base / "package.json", json.dumps({"type": "module"}, indent=2) + "\n")
    write_text(
        expected_path,
        json.dumps(
            {
                "title": "Revenue Pulse",
                "status": "Ready",
                "metric": "$42.8K",
                "trend": "+18%",
                "accentColor": "#2563EB",
                "statusColor": "#16A34A",
                "cta": "Open forecast",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )


def build_codex_exec_row(
    *,
    task: str,
    arm: str,
    phase: str,
    component: str,
    prompt: str,
    events_path: Path,
    output_path: Path,
    result: Any,
    usage: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "schema_version": CODEX_EXEC_ROW_SCHEMA_VERSION,
        "task": task,
        "arm": arm,
        "phase": phase,
        "component": component,
        "prompt_hash": sha256_text(prompt),
        "prompt_bytes": len(prompt.encode("utf-8")),
        "codex_rc": result.rc,
        "duration_seconds": result.seconds,
        "timed_out": result.timed_out,
        "usage_status": "measured" if usage else "unavailable",
        "usage": compact_usage(usage),
        "raw_usage": usage,
        "events_path": str(events_path),
        "output_path": str(output_path),
    }


def compact_policy_contract(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "task_contract.v1",
        "contract_id": f"{task['slug']}@policy_exact_label_routing.v1.compact",
        "task_id": task["slug"],
        "rubric_id": task["strict_rubric"],
        "rubric_sha256": "compact_capsule_inline",
        "risk_level": task["strict_risk"],
        "ambiguity_score": 0,
        "allowed_files": [task["allowed"]],
        "forbidden_files": ["test", "README.md", "package.json", "validate.mjs"],
        "goal": "Fix exact policy reason labels with one allowed-file change.",
        "non_goals": ["Do not edit tests", "Do not broaden policy matching"],
        "requirements": [
            {
                "id": "REQ-001",
                "must": "Exact policy labels match the validation contract.",
                "verification": "npm test passes.",
                "evidence_required": ["validation_result"],
                "blocking": True,
                "applies_to_files": [task["allowed"]],
                "negative_checks": ["No near-synonym label substitutions"],
            },
            {
                "id": "REQ-002",
                "must": "Routing change is scoped and deterministic.",
                "verification": "Diffstat and code reference show a minimal branch/label update.",
                "evidence_required": ["diffstat", "code_reference"],
                "blocking": True,
                "applies_to_files": [task["allowed"]],
                "negative_checks": ["No unrelated routing changes"],
            },
            {
                "id": "REQ-003",
                "must": "Only allowed files change.",
                "verification": "Allowed-files check passes.",
                "evidence_required": ["changed_file"],
                "blocking": True,
                "applies_to_files": [task["allowed"]],
                "negative_checks": ["No forbidden file edits"],
            },
        ],
        "edge_cases": [
            {
                "id": "EDGE-001",
                "case": "exact spelling and casing",
                "expected": "human-readable labels match exactly",
                "evidence_required": ["validation_result"],
            }
        ],
        "forbidden_actions": ["change tests", "edit forbidden files", "broaden policy logic"],
        "implementation_plan": [
            {"id": "PLAN-001", "step": "Inspect allowed policy file.", "expected_change": "Find mismatch.", "max_diff_hint": None},
            {"id": "PLAN-002", "step": "Apply exact minimal fix.", "expected_change": "Small label/routing change.", "max_diff_hint": "+16/-12 soft"},
        ],
        "expected_diff_budget": {
            "files_changed_max": 1,
            "insertions_soft_max": 16,
            "deletions_soft_max": 12,
            "if_exceeded": "add_risk_flag_and_explain",
        },
        "validation_commands": [task["validation"]],
        "acceptance_rules": [
            "All blocking requirements have evidence",
            "Validation passes",
            "Only allowed files change",
            "No unresolved questions remain on pass",
        ],
        "failure_protocol": "Return blocked instead of guessing if the contract cannot be satisfied safely.",
        "self_audit_schema_ref": "docs/zcode-strict-contract-v3/schemas/zcode_self_audit.schema.json",
    }


def metric(row: dict[str, Any]) -> int | None:
    usage = row.get("usage")
    if not isinstance(usage, dict):
        return None
    value = usage.get(PRIMARY_METRIC)
    return int(value) if isinstance(value, int) else None


def status_is_measured(status: Any) -> bool:
    return status == "measured"


def usage_state(tokens: int | None, status: str, reason: str | None = None) -> dict[str, Any]:
    return {
        "tokens": tokens,
        "usage_status": status,
        "no_usage_reason": reason,
    }


def int_token(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    return None


def worker_kind_for_mode(mode: Any) -> str:
    if mode == "codex_only":
        return "codex"
    if mode in {"zcode_delegated", "zcode_direct_launcher"}:
        return "zcode"
    return "unknown"


def setdefault_worker_usage_fields(row: dict[str, Any]) -> None:
    mode = row.get("mode")
    row.setdefault("worker_kind", worker_kind_for_mode(mode))
    row.setdefault("worker_provider", None)
    row.setdefault("worker_model", None)
    row.setdefault("worker_usage_source", None)
    row.setdefault("worker_usage_source_path", None)
    row.setdefault("worker_usage_capture_method", None)
    row.setdefault("worker_input_tokens", None)
    row.setdefault("worker_output_tokens", None)
    row.setdefault("worker_reasoning_tokens", None)
    row.setdefault("worker_total_tokens", None)
    row.setdefault("worker_quota_percent_before", None)
    row.setdefault("worker_quota_percent_after", None)
    row.setdefault("worker_quota_percent_used", None)
    row.setdefault("worker_credits_used", None)


def apply_default_worker_usage(row: dict[str, Any], effective: int | None) -> None:
    mode = row.get("mode")
    if row.get("worker_usage_status") is None:
        if mode == "codex_only" and effective is not None:
            row["worker_usage_status"] = "measured"
            row["worker_usage_unit"] = "tokens"
            row["worker_usage_capture_method"] = "codex_exec_turn_completed"
            row["worker_total_tokens"] = effective
            usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
            row["worker_input_tokens"] = int_token(usage.get("uncached_input_tokens"))
            row["worker_output_tokens"] = int_token(usage.get("output_tokens"))
            row["worker_reasoning_tokens"] = int_token(usage.get("reasoning_output_tokens"))
        else:
            row["worker_usage_status"] = "unavailable"
            row.setdefault("worker_usage_unit", "unknown")
            if mode == "codex_only":
                row["worker_usage_no_usage_reason"] = row.get("worker_usage_no_usage_reason") or row.get("no_usage_reason") or PHASE_USAGE_UNAVAILABLE_REASON
            else:
                row["worker_usage_no_usage_reason"] = row.get("worker_usage_no_usage_reason") or row.get("no_usage_reason") or ZCODE_WORKER_USAGE_MISSING_REASON
    row.setdefault("worker_usage_unit", "unknown")


def normalize_worker_token_status(row: dict[str, Any]) -> None:
    if row.get("worker_usage_status") == "measured" and row.get("worker_usage_unit") == "tokens":
        row["worker_total_tokens"] = int_token(row.get("worker_total_tokens"))
        if row["worker_total_tokens"] is None or (row.get("worker_kind") == "zcode" and row["worker_total_tokens"] <= 0):
            row["worker_total_tokens"] = None
            row["worker_usage_status"] = "unavailable"
            row["worker_usage_no_usage_reason"] = ZCODE_WORKER_USAGE_MISSING_REASON
    elif row.get("worker_usage_status") == "partial":
        row["worker_total_tokens"] = None
        row["worker_usage_no_usage_reason"] = row.get("worker_usage_no_usage_reason") or WORKER_TOKEN_USAGE_NON_TOKEN_REASON
    else:
        row["worker_total_tokens"] = None
        row["worker_usage_no_usage_reason"] = row.get("worker_usage_no_usage_reason") or row.get("no_usage_reason") or ZCODE_WORKER_USAGE_MISSING_REASON
    row.setdefault("worker_usage_no_usage_reason", None)


def sync_legacy_zcode_worker_fields(row: dict[str, Any]) -> None:
    if row.get("worker_kind") == "zcode":
        if row.get("worker_usage_status") == "measured" and row.get("worker_usage_unit") == "tokens":
            row["zcode_worker_usage_status"] = "measured"
            row["zcode_worker_tokens"] = row.get("worker_total_tokens")
        else:
            row["zcode_worker_usage_status"] = "unavailable"
            row["zcode_worker_tokens"] = None
            row.setdefault("no_usage_reason", row.get("worker_usage_no_usage_reason") or ZCODE_WORKER_USAGE_MISSING_REASON)
        row.setdefault("zcode_worker_usage_scope", "excluded_from_codex_effective_work")
    else:
        row.setdefault("zcode_worker_usage_status", "not_applicable")
        row.setdefault("zcode_worker_tokens", None)


def ensure_worker_usage_fields(row: dict[str, Any], effective: int | None) -> None:
    setdefault_worker_usage_fields(row)
    apply_default_worker_usage(row, effective)
    normalize_worker_token_status(row)
    sync_legacy_zcode_worker_fields(row)


def delegated_arm_codex_state(row: dict[str, Any], effective: int | None) -> dict[str, Any]:
    mode = row.get("mode")
    if mode == "codex_only":
        return usage_state(None, "not_applicable", "not_applicable_for_codex_only")
    if mode == "zcode_direct_launcher" and effective == 0:
        return usage_state(
            0,
            "measured",
            "direct_mode_python_subprocess_no_codex_exec",
        )
    status = str(row.get("codex_usage_status") or ("measured" if effective is not None else "unavailable"))
    reason = None if status_is_measured(status) else row.get("no_usage_reason") or PHASE_USAGE_UNAVAILABLE_REASON
    return usage_state(effective if status_is_measured(status) else None, status, reason)


def codex_side_required_phase_states(row: dict[str, Any]) -> list[tuple[str, Any, Any, str | None]]:
    mode = row.get("mode")
    if mode == "codex_only":
        return [("implementation", row.get("codex_effective_work"), row.get("codex_usage_status"), row.get("no_usage_reason"))]
    if mode == "zcode_direct_launcher":
        return [
            (
                "supervisor_orchestration",
                None,
                "unavailable",
                DIRECT_SUPERVISOR_USAGE_MISSING_REASON,
            )
        ]
    return [
        (
            "launcher_orchestration",
            row.get("codex_orchestration_tokens"),
            row.get("codex_orchestration_usage_status"),
            row.get("codex_orchestration_no_usage_reason"),
        ),
        (
            "acceptance",
            row.get("codex_acceptance_tokens"),
            row.get("codex_acceptance_usage_status"),
            row.get("codex_acceptance_no_usage_reason"),
        ),
        (
            "repair",
            row.get("codex_repair_tokens"),
            row.get("codex_repair_usage_status"),
            row.get("codex_repair_no_usage_reason"),
        ),
    ]


def total_codex_side_state(row: dict[str, Any]) -> dict[str, Any]:
    missing: list[str] = []
    total = 0
    for phase, tokens, status, reason in codex_side_required_phase_states(row):
        if status_is_measured(status):
            total += int(tokens or 0)
            continue
        if status == "not_applicable":
            continue
        missing.append(f"{phase}:{reason or PHASE_USAGE_UNAVAILABLE_REASON}")
    if missing:
        return usage_state(None, "unavailable", ";".join(missing))
    return usage_state(total, "measured", None)


def total_workflow_state(row: dict[str, Any]) -> dict[str, Any]:
    codex_state = total_codex_side_state(row)
    if not status_is_measured(codex_state["usage_status"]):
        return usage_state(None, "unavailable", codex_state["no_usage_reason"] or TOTAL_CODEX_SIDE_USAGE_MISSING_REASON)
    if row.get("mode") == "codex_only":
        return usage_state(codex_state["tokens"], "measured", None)
    if row.get("worker_usage_status") != "measured" or row.get("worker_usage_unit") != "tokens":
        return usage_state(
            None,
            "unavailable",
            row.get("worker_usage_no_usage_reason") or row.get("no_usage_reason") or ZCODE_WORKER_USAGE_MISSING_REASON,
        )
    worker_tokens = int_token(row.get("worker_total_tokens"))
    if worker_tokens is None:
        return usage_state(None, "unavailable", ZCODE_WORKER_USAGE_MISSING_REASON)
    return usage_state(int(codex_state["tokens"] or 0) + worker_tokens, "measured", None)


def strict_failure_classification(row: dict[str, Any]) -> list[str]:
    classifications: list[str] = []
    strict = row.get("strict_contract") if isinstance(row.get("strict_contract"), dict) else {}
    acceptance = row.get("zcode_acceptance") if isinstance(row.get("zcode_acceptance"), dict) else {}
    violations = list(row.get("strict_violations") or strict.get("violations") or [])
    violation_text = "\n".join(str(item) for item in violations)
    allowed = set(row.get("task_contract_allowed_files") or [])
    expected_allowed = row.get("allowed_file")
    if isinstance(expected_allowed, str):
        allowed.add(expected_allowed)
    changed = set(row.get("changed_files") or [])

    for violation in violations:
        text = str(violation)
        ref = parse_path_reference(text)
        if ref.safety_error:
            classifications.append("path_reference_parser_or_safety")
            continue
        if ref.reason not in {"outside_allowed_files", "forbidden_file_changed", "forbidden_files_changed"}:
            continue
        if ref.ranges and ref.path in allowed:
            classifications.append("path_range_parser_issue")
        elif ref.reason == "outside_allowed_files" and ref.path in changed and ref.path not in allowed:
            classifications.append("allowed_files_manifest_mismatch")
        elif ref.reason in {"forbidden_file_changed", "forbidden_files_changed"} or ref.path not in allowed:
            classifications.append("true_forbidden_file_edit")

    if "risk_flags" in violations or row.get("risk_flags"):
        classifications.append("risk_flags")
    if acceptance.get("artifact_quality") not in {None, "pass"} or "artifact_quality" in violation_text:
        classifications.append("artifact_quality")
    repair_size = acceptance.get("codex_repair_size")
    if (repair_size is not None and str(repair_size) not in ACCEPTABLE_REPAIR_SIZES) or "codex_repair_size" in violation_text:
        classifications.append("codex_repair_size")
    if (
        row.get("final_validation_rc") not in {None, 0}
        or acceptance.get("validation_result") == "fail"
        or "validation_result" in violation_text
    ):
        classifications.append("validation_failure")
    if row.get("quality") == "fail" and not classifications:
        classifications.append("unknown")
    return sorted(dict.fromkeys(classifications))


def _truthy_timeout(row: dict[str, Any], acceptance: dict[str, Any]) -> bool:
    return bool(
        row.get("timed_out") is True
        or acceptance.get("timed_out") is True
        or row.get("route_rc") == 143
        or row.get("supervisor_state") == "run_timeout"
        or acceptance.get("supervisor_state") == "run_timeout"
        or acceptance.get("status") == "run_timeout"
    )


def _positive_changed_count(row: dict[str, Any], acceptance: dict[str, Any]) -> bool:
    changed = row.get("changed_files")
    if isinstance(changed, list) and len(changed) > 0:
        return True
    count = acceptance.get("changed_count")
    return isinstance(count, int) and count > 0


def _self_audit_missing(row: dict[str, Any], acceptance: dict[str, Any]) -> bool:
    strict = row.get("strict_contract") if isinstance(row.get("strict_contract"), dict) else {}
    violations = list(row.get("strict_violations") or strict.get("violations") or acceptance.get("violations") or [])
    return any("self_audit" in str(item) and "missing" in str(item) for item in violations)


def _provider_timeout_classification(row: dict[str, Any]) -> str | None:
    if row.get("provider_code") == "1302" or row.get("provider_error_kind") == "provider_rate_limit_1302":
        return "provider_rate_limit_1302"
    if row.get("provider_error_kind"):
        return str(row.get("provider_error_kind"))
    if row.get("provider_error") is True:
        return "provider_error"
    return None


def _timeout_phase(*, timed_out: bool, changed: bool, final_validation_passed: bool, self_audit_missing: bool) -> str | None:
    if not timed_out:
        return None
    if not changed:
        return "before_artifact"
    if not final_validation_passed:
        return "after_artifact_before_self_audit"
    if self_audit_missing:
        return "after_final_validation_before_final_report"
    return "unknown"


def timeout_acceptance_diagnostics(row: dict[str, Any]) -> dict[str, Any]:
    acceptance = row.get("zcode_acceptance") if isinstance(row.get("zcode_acceptance"), dict) else {}
    timed_out = _truthy_timeout(row, acceptance)
    final_validation_passed = bool(
        row.get("final_validation_rc") == 0
        or acceptance.get("validation_ok") is True
        or acceptance.get("validation_result") == "pass"
    )
    changed = _positive_changed_count(row, acceptance)
    validated_partial = timed_out and final_validation_passed and changed
    self_audit_missing = _self_audit_missing(row, acceptance)
    provider_timeout_classification = _provider_timeout_classification(row)
    route_rc_143_fails_closed = bool(
        row.get("route_rc") == 143
        and row.get("quality") != "pass"
        and row.get("strict_accepted") is not True
    )
    timeout_not_success = bool(
        timed_out
        and row.get("quality") != "pass"
        and row.get("strict_accepted") is not True
    )

    if timed_out:
        if validated_partial:
            outcome = "timeout_after_valid_artifact_but_acceptance_blocked"
            reason = "validated_partial_artifact_present_but_route_timeout_failed_closed"
        elif row.get("provider_error") is True:
            outcome = "terminal_provider_timeout"
            reason = "provider_error_timeout_without_validated_partial_artifact"
        else:
            outcome = "timeout_true_worker_hang"
            reason = "timeout_without_provider_error_or_validated_partial_artifact"
    elif acceptance.get("artifact_quality") == "fail":
        outcome = "acceptance_artifact_quality_failure"
        reason = "artifact_quality_failed_without_timeout"
    else:
        outcome = None
        reason = None

    return {
        "timeout_acceptance_outcome": outcome,
        "timeout_acceptance_reason": reason,
        "timeout_detected": timed_out,
        "validated_partial_artifact_present": validated_partial,
        "artifact_valid_before_timeout": validated_partial,
        "final_validation_passed": final_validation_passed,
        "final_validation_rc_preserved_after_timeout": bool(timed_out and row.get("final_validation_rc") is not None),
        "timeout_phase": _timeout_phase(
            timed_out=timed_out,
            changed=changed,
            final_validation_passed=final_validation_passed,
            self_audit_missing=self_audit_missing,
        ),
        "provider_timeout_classification": provider_timeout_classification,
        "provider_rate_limit_1302_detected": provider_timeout_classification == "provider_rate_limit_1302",
        "self_audit_missing_after_timeout": bool(timed_out and self_audit_missing),
        "acceptance_timeout_diagnostic": (
            "final_validation_passed_but_route_timeout_missing_self_audit_fail_closed"
            if validated_partial and self_audit_missing and route_rc_143_fails_closed
            else reason
        ),
        "route_rc_143_fails_closed": route_rc_143_fails_closed,
        "timeout_not_treated_as_success": timeout_not_success,
    }


def row_repair_size_class(row: dict[str, Any]) -> str:
    if row.get("repair_size_class") is not None:
        return normalize_repair_size_class(row.get("repair_size_class"))
    acceptance = row.get("zcode_acceptance") if isinstance(row.get("zcode_acceptance"), dict) else {}
    return normalize_repair_size_class(acceptance.get("codex_repair_size"))


def repair_policy_for_row(row: dict[str, Any]) -> dict[str, Any]:
    mode = row.get("mode")
    allowed_files = row.get("repair_allowed_files") or row.get("task_contract_allowed_files") or []
    if row.get("allowed_file") and row.get("allowed_file") not in allowed_files:
        allowed_files = [*allowed_files, row.get("allowed_file")]
    forbidden_files = row.get("repair_forbidden_files") or row.get("task_contract_forbidden_files") or []
    evidence_paths = row.get("repair_evidence_paths") or []
    if row.get("workspace"):
        evidence_paths = [*evidence_paths, row["workspace"]]

    if mode == "codex_only":
        return evaluate_repair_policy(
            repair_failure_classification=[],
            repair_size_class="none",
            attempts_used=0,
            attempt_budget=0,
            changed_files=row.get("changed_files") or [],
            changed_lines=repair_changed_line_count(row.get("diff") if isinstance(row.get("diff"), list) else []),
            allowed_files=allowed_files,
            forbidden_files=forbidden_files,
            repair_policy_enabled=False,
            repair_reason="not_delegated_row",
            repair_evidence_paths=evidence_paths,
        )

    failure_classes = row.get("strict_failure_classification") or []
    strict_accepted = row.get("strict_accepted") is True
    attempts_used = int(row.get("repair_attempts_used") or 0)
    if attempts_used > 0:
        policy = evaluate_repair_policy(
            repair_failure_classification=[],
            repair_size_class=row_repair_size_class(row),
            attempts_used=attempts_used,
            attempt_budget=max(attempts_used, int(row.get("repair_attempt_budget") or DEFAULT_REPAIR_ATTEMPT_BUDGET)),
            changed_files=row.get("changed_files") or [],
            changed_lines=repair_changed_line_count(row.get("diff") if isinstance(row.get("diff"), list) else []),
            allowed_files=allowed_files,
            forbidden_files=forbidden_files,
            repair_policy_enabled=True,
            repair_reason=row.get("repair_reason") or "repair_attempt_recorded",
            post_repair_validation_status=str(row.get("post_repair_validation_status") or "not_run"),
            post_repair_strict_accepted=bool(row.get("post_repair_strict_accepted", False)),
            repair_evidence_paths=evidence_paths,
        )
        policy["repair_decision"] = "attempted"
        policy["repair_stop_reason"] = (
            "post_repair_strict_validation_passed"
            if policy["post_repair_validation_status"] == "pass" and policy["post_repair_strict_accepted"]
            else "post_repair_recorded"
        )
        return policy
    if strict_accepted and row.get("quality") == "pass" and not failure_classes:
        return evaluate_repair_policy(
            repair_failure_classification=[],
            repair_size_class="none",
            attempts_used=attempts_used,
            attempt_budget=int(row.get("repair_attempt_budget") or DEFAULT_REPAIR_ATTEMPT_BUDGET),
            changed_files=row.get("changed_files") or [],
            changed_lines=repair_changed_line_count(row.get("diff") if isinstance(row.get("diff"), list) else []),
            allowed_files=allowed_files,
            forbidden_files=forbidden_files,
            repair_policy_enabled=True,
            repair_reason="strict_accepted_no_repair_needed",
            repair_evidence_paths=evidence_paths,
        )

    return evaluate_repair_policy(
        repair_failure_classification=failure_classes,
        repair_size_class=row_repair_size_class(row),
        attempts_used=int(row.get("repair_attempts_used") or 0),
        attempt_budget=int(row.get("repair_attempt_budget") or DEFAULT_REPAIR_ATTEMPT_BUDGET),
        changed_files=row.get("changed_files") or [],
        changed_lines=repair_changed_line_count(row.get("diff") if isinstance(row.get("diff"), list) else []),
        allowed_files=allowed_files,
        forbidden_files=forbidden_files,
        repair_policy_enabled=bool(row.get("repair_policy_enabled", True)),
        repair_reason=row.get("repair_reason"),
        post_repair_validation_status=str(row.get("post_repair_validation_status") or "not_run"),
        post_repair_strict_accepted=bool(row.get("post_repair_strict_accepted", False)),
        repair_evidence_paths=evidence_paths,
    )


def claim_scope_for_row(row: dict[str, Any]) -> str:
    if row.get("mode") == "codex_only":
        return "quality-green" if row.get("quality") == "pass" else "apparent-all"
    if row.get("quality") == "pass" and row.get("strict_accepted") is True:
        return "strict-green"
    if row.get("quality") == "pass":
        return "quality-green"
    return "apparent-all"


def apply_report_schema(
    row: dict[str, Any],
    *,
    measurement_mode: str,
    fresh_run: bool,
    baseline_cache_hit: bool,
    baseline_cache_key: str | None,
    baseline_reuse_mode: str | None = None,
) -> dict[str, Any]:
    usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
    usage_status = row.get("usage_status")
    effective = metric(row)
    row["measurement_mode"] = measurement_mode
    row["claim_scope"] = claim_scope_for_row(row)
    row["claim_family"] = None if row.get("mode") == "codex_only" else claim_family_for_arm(str(row.get("mode") or "unknown"))
    row["fresh_run"] = fresh_run
    row["baseline_cache_hit"] = baseline_cache_hit
    row["baseline_cache_key"] = baseline_cache_key
    if baseline_reuse_mode:
        row["baseline_reuse_mode"] = baseline_reuse_mode
    row["codex_effective_work"] = effective
    row["codex_usage_status"] = usage_status or ("measured" if effective is not None else "unavailable")
    if row["codex_usage_status"] == "unavailable":
        row.setdefault("no_usage_reason", usage.get("no_usage_reason") or PHASE_USAGE_UNAVAILABLE_REASON)

    if row.get("mode") == "zcode_delegated":
        row["codex_orchestration_tokens"] = effective
        row["codex_orchestration_usage_status"] = row["codex_usage_status"]
        row["codex_orchestration_no_usage_reason"] = None if effective is not None else PHASE_USAGE_UNAVAILABLE_REASON
    else:
        row["codex_orchestration_tokens"] = None
        row["codex_orchestration_usage_status"] = "not_applicable"
        row["codex_orchestration_no_usage_reason"] = "not_applicable_for_row_mode"
    for phase in ("acceptance", "repair"):
        row.setdefault(f"codex_{phase}_tokens", None)
        row.setdefault(f"codex_{phase}_usage_status", "unavailable")
        row.setdefault(f"codex_{phase}_no_usage_reason", PHASE_USAGE_UNAVAILABLE_REASON)

    if row.get("mode") == "zcode_direct_launcher":
        row["codex_orchestration_usage_status"] = "not_applicable"
        row["codex_orchestration_no_usage_reason"] = "direct_mode_python_subprocess_no_codex_exec"
    if row.get("mode") == "codex_only":
        row["codex_acceptance_usage_status"] = "not_applicable"
        row["codex_acceptance_no_usage_reason"] = "not_applicable_for_codex_only"
        row["codex_repair_usage_status"] = "not_applicable"
        row["codex_repair_no_usage_reason"] = "not_applicable_for_codex_only"

    ensure_worker_usage_fields(row, effective)
    delegated_state = delegated_arm_codex_state(row, effective)
    total_codex_state = total_codex_side_state(row)
    total_workflow = total_workflow_state(row)
    row["delegated_arm_codex_work"] = delegated_state["tokens"]
    row["delegated_arm_codex_usage_status"] = delegated_state["usage_status"]
    row["delegated_arm_codex_no_usage_reason"] = delegated_state["no_usage_reason"]
    row["total_codex_side_work"] = total_codex_state["tokens"]
    row["total_codex_side_usage_status"] = total_codex_state["usage_status"]
    row["total_codex_side_no_usage_reason"] = total_codex_state["no_usage_reason"]
    row["total_workflow_work"] = total_workflow["tokens"]
    row["total_workflow_usage_status"] = total_workflow["usage_status"]
    row["total_workflow_no_usage_reason"] = total_workflow["no_usage_reason"]
    row["strict_failure_classification"] = strict_failure_classification(row)
    row.update(timeout_acceptance_diagnostics(row))
    row.update(repair_policy_for_row(row))
    return row


def delta_label(row: dict[str, Any]) -> str:
    added = sum(item.get("added", 0) for item in row.get("diff", []))
    deleted = sum(item.get("deleted", 0) for item in row.get("diff", []))
    return f"+{added}/-{deleted}"


def codex_exec_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        result.extend(item for item in row.get("codex_exec_rows", []) if isinstance(item, dict))
    return result


def component_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = codex_exec_rows(rows)
    for row in rows:
        if row.get("mode") != "zcode_direct_launcher":
            continue
        result.append(
            {
                "task": row.get("task"),
                "arm": row.get("mode"),
                "phase": "launcher_execution",
                "component": "non_llm_direct_launcher",
                "usage_status": "not_applicable_non_llm",
                "usage": compact_usage(row.get("usage")),
                "duration_seconds": row.get("duration_seconds"),
            }
        )
    return result


def comparison_summary(
    rows: list[dict[str, Any]],
    *,
    quality_green_only: bool,
    strict_green_only: bool = False,
) -> dict[str, Any]:
    by_task = {row["task"]: row for row in rows if row.get("mode") == "codex_only"}
    entries: list[dict[str, Any]] = []
    totals: dict[str, dict[str, float | int | None]] = {}
    for row in rows:
        arm = row.get("mode")
        if arm == "codex_only" or row.get("task") not in by_task:
            continue
        baseline = by_task[row["task"]]
        if quality_green_only and (baseline.get("quality") != "pass" or row.get("quality") != "pass"):
            continue
        if strict_green_only and not (
            baseline.get("quality") == "pass"
            and row.get("quality") == "pass"
            and row.get("strict_accepted") is True
        ):
            continue
        base_tokens = metric(baseline)
        arm_tokens = metric(row)
        base_seconds = float(baseline.get("duration_seconds") or 0)
        arm_seconds = float(row.get("duration_seconds") or 0)
        reduction = round((base_tokens - arm_tokens) / base_tokens * 100, 2) if base_tokens and arm_tokens is not None else None
        entries.append(
            {
                "task": row["task"],
                "arm": arm,
                "measurement_mode": row.get("measurement_mode"),
                "claim_scope": row.get("claim_scope"),
                "codex_quality": baseline.get("quality"),
                "arm_quality": row.get("quality"),
                "baseline_effective": base_tokens,
                "arm_effective": arm_tokens,
                "delta_effective": arm_tokens - base_tokens if arm_tokens is not None and base_tokens is not None else None,
                "reduction_percent": reduction,
                "baseline_seconds": base_seconds,
                "arm_seconds": arm_seconds,
                "strict_accepted": row.get("strict_accepted"),
                "strict_failure_classification": row.get("strict_failure_classification", []),
                "repair_decision": row.get("repair_decision"),
                "repair_attempt_budget": row.get("repair_attempt_budget"),
                "repair_attempts_used": row.get("repair_attempts_used"),
                "repair_size_class": row.get("repair_size_class"),
                "repair_blocker": row.get("repair_blocker"),
                "post_repair_validation_status": row.get("post_repair_validation_status"),
                "post_repair_strict_accepted": row.get("post_repair_strict_accepted"),
            }
        )
        total = totals.setdefault(arm, {"baseline_effective": 0, "arm_effective": 0, "baseline_seconds": 0.0, "arm_seconds": 0.0})
        total["baseline_effective"] = int(total["baseline_effective"] or 0) + int(base_tokens or 0)
        total["arm_effective"] = int(total["arm_effective"] or 0) + int(arm_tokens or 0)
        total["baseline_seconds"] = float(total["baseline_seconds"] or 0) + base_seconds
        total["arm_seconds"] = float(total["arm_seconds"] or 0) + arm_seconds
    for total in totals.values():
        base = int(total["baseline_effective"] or 0)
        arm_value = int(total["arm_effective"] or 0)
        total["delta_effective"] = arm_value - base
        total["reduction_percent"] = round((base - arm_value) / base * 100, 2) if base else None
        total["time_ratio"] = round(float(total["arm_seconds"] or 0) / float(total["baseline_seconds"] or 1), 3) if total["baseline_seconds"] else None
    return {"task_arm": entries, "by_arm": totals}


def claim_family_for_arm(arm: str) -> str:
    if arm == "zcode_direct_launcher":
        return "direct_orchestrated_delegation_savings"
    if arm == "zcode_delegated":
        return "codex_mediated_delegation_savings"
    return f"{arm}_savings"


def claim_families(summary: dict[str, Any], rows: list[dict[str, Any]], *, scope: str) -> dict[str, Any]:
    by_arm = summary.get("by_arm", {})
    families: dict[str, Any] = {}
    for arm, totals in by_arm.items():
        row_modes = sorted(
            {
                row.get("measurement_mode")
                for row in rows
                if row.get("mode") == arm and row.get("measurement_mode")
            }
        )
        family = claim_family_for_arm(arm)
        families[family] = {
            "arm": arm,
            "scope": scope,
            "measurement_modes": row_modes,
            "mixed_measurement_modes": len(row_modes) > 1,
            "deployable_claim_allowed": scope in {"quality-green", "strict-green"} and len(row_modes) <= 1,
            **totals,
        }
    return families


def accounting_reduction(baseline: int | None, arm: int | None) -> float | None:
    if baseline is None or arm is None or baseline == 0:
        return None
    return round((baseline - arm) / baseline * 100, 2)


def accounting_layer(
    *,
    layer: str,
    claim_family: str,
    scope: str,
    baseline_work: int | None,
    arm_work: int | None,
    missing_reasons: list[str],
) -> dict[str, Any]:
    status = "measured" if not missing_reasons and baseline_work is not None and arm_work is not None else "unavailable"
    return {
        "layer": layer,
        "claim_family": claim_family,
        "scope": scope,
        "usage_status": status,
        "baseline_work": baseline_work,
        "arm_work": arm_work if status == "measured" else None,
        "delta_work": (arm_work - baseline_work) if status == "measured" else None,
        "reduction_percent": accounting_reduction(baseline_work, arm_work) if status == "measured" else None,
        "no_usage_reason": None if status == "measured" else ";".join(sorted(set(missing_reasons))),
        "deployable_claim_allowed": status == "measured" and scope in {"quality-green", "strict-green"},
    }


def rows_for_accounting_scope(
    rows: list[dict[str, Any]],
    *,
    quality_green_only: bool,
    strict_green_only: bool,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    baselines = {row["task"]: row for row in rows if row.get("mode") == "codex_only"}
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for row in rows:
        if row.get("mode") == "codex_only" or row.get("task") not in baselines:
            continue
        baseline = baselines[row["task"]]
        if quality_green_only and (baseline.get("quality") != "pass" or row.get("quality") != "pass"):
            continue
        if strict_green_only and not (
            baseline.get("quality") == "pass"
            and row.get("quality") == "pass"
            and row.get("strict_accepted") is True
        ):
            continue
        pairs.append((baseline, row))
    return pairs


def sum_if_measured(row: dict[str, Any], field: str, status_field: str, missing: list[str], label: str) -> int | None:
    status = row.get(status_field)
    value = row.get(field)
    if status_is_measured(status) and isinstance(value, int):
        return value
    missing.append(f"{label}:{row.get(field.replace('_work', '_no_usage_reason')) or row.get('no_usage_reason') or 'usage_unavailable'}")
    return None


def end_to_end_accounting_scope(
    rows: list[dict[str, Any]],
    *,
    scope: str,
    quality_green_only: bool,
    strict_green_only: bool = False,
) -> dict[str, Any]:
    by_arm: dict[str, dict[str, Any]] = {}
    for baseline, row in rows_for_accounting_scope(
        rows,
        quality_green_only=quality_green_only,
        strict_green_only=strict_green_only,
    ):
        arm = str(row.get("mode") or "unknown")
        family = claim_family_for_arm(arm)
        bucket = by_arm.setdefault(
            arm,
            {
                "arm": arm,
                "scope": scope,
                "delegated_arm_codex_savings": {
                    "baseline": 0,
                    "arm": 0,
                    "missing": [],
                },
                "total_codex_side_savings": {
                    "baseline": 0,
                    "arm": 0,
                    "missing": [],
                },
                "total_workflow_savings": {
                    "baseline": 0,
                    "arm": 0,
                    "missing": [],
                },
            },
        )

        delegated = bucket["delegated_arm_codex_savings"]
        base_delegated = metric(baseline)
        arm_delegated = row.get("delegated_arm_codex_work")
        if base_delegated is None:
            delegated["missing"].append(f"{baseline.get('task')}:baseline_delegated_arm_usage_unavailable")
        else:
            delegated["baseline"] += base_delegated
        if isinstance(arm_delegated, int):
            delegated["arm"] += arm_delegated
        else:
            delegated["missing"].append(f"{row.get('task')}:delegated_arm_usage_unavailable")

        total_codex = bucket["total_codex_side_savings"]
        base_total_codex = sum_if_measured(
            baseline,
            "total_codex_side_work",
            "total_codex_side_usage_status",
            total_codex["missing"],
            f"{baseline.get('task')}:baseline_total_codex_side",
        )
        arm_total_codex = sum_if_measured(
            row,
            "total_codex_side_work",
            "total_codex_side_usage_status",
            total_codex["missing"],
            f"{row.get('task')}:arm_total_codex_side",
        )
        if base_total_codex is not None:
            total_codex["baseline"] += base_total_codex
        if arm_total_codex is not None:
            total_codex["arm"] += arm_total_codex

        total_workflow = bucket["total_workflow_savings"]
        base_total_workflow = sum_if_measured(
            baseline,
            "total_workflow_work",
            "total_workflow_usage_status",
            total_workflow["missing"],
            f"{baseline.get('task')}:baseline_total_workflow",
        )
        arm_total_workflow = sum_if_measured(
            row,
            "total_workflow_work",
            "total_workflow_usage_status",
            total_workflow["missing"],
            f"{row.get('task')}:arm_total_workflow",
        )
        if base_total_workflow is not None:
            total_workflow["baseline"] += base_total_workflow
        if arm_total_workflow is not None:
            total_workflow["arm"] += arm_total_workflow

    rendered: dict[str, Any] = {}
    for arm, bucket in by_arm.items():
        family = claim_family_for_arm(arm)
        rendered[arm] = {
            "arm": arm,
            "scope": scope,
            "delegated_arm_codex_savings": accounting_layer(
                layer="delegated_arm_codex",
                claim_family=family,
                scope=scope,
                baseline_work=bucket["delegated_arm_codex_savings"]["baseline"],
                arm_work=bucket["delegated_arm_codex_savings"]["arm"],
                missing_reasons=bucket["delegated_arm_codex_savings"]["missing"],
            ),
            "total_codex_side_savings": accounting_layer(
                layer="total_codex_side",
                claim_family=f"total_codex_side_{family}",
                scope=scope,
                baseline_work=bucket["total_codex_side_savings"]["baseline"],
                arm_work=bucket["total_codex_side_savings"]["arm"],
                missing_reasons=bucket["total_codex_side_savings"]["missing"],
            ),
            "total_workflow_savings": accounting_layer(
                layer="total_workflow",
                claim_family=f"total_workflow_{family}",
                scope=scope,
                baseline_work=bucket["total_workflow_savings"]["baseline"],
                arm_work=bucket["total_workflow_savings"]["arm"],
                missing_reasons=bucket["total_workflow_savings"]["missing"],
            ),
        }
    return rendered


def end_to_end_accounting(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": END_TO_END_ACCOUNTING_SCHEMA_VERSION,
        "metric": PRIMARY_METRIC,
        "metric_formula": PRIMARY_METRIC_FORMULA,
        "layers": {
            "delegated_arm_codex": "Codex work inside the delegated arm only.",
            "total_codex_side": "All Codex-side supervisor/orchestration/acceptance/repair work required for the delegated workflow.",
            "total_workflow": "Total Codex-side work plus measured ZCode worker/model work.",
        },
        "all_rows": end_to_end_accounting_scope(
            rows,
            scope="apparent-all",
            quality_green_only=False,
        ),
        "quality_green_subset": end_to_end_accounting_scope(
            rows,
            scope="quality-green",
            quality_green_only=True,
        ),
        "strict_green_subset": end_to_end_accounting_scope(
            rows,
            scope="strict-green",
            quality_green_only=True,
            strict_green_only=True,
        ),
    }


def baseline_cache_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    codex_rows = [row for row in rows if row.get("mode") == "codex_only"]
    fresh_cost = sum(metric(row) or 0 for row in rows if row.get("fresh_run") is True)
    return {
        "baseline_cache_hits": sum(1 for row in codex_rows if row.get("baseline_cache_hit") is True),
        "baseline_cache_misses": sum(1 for row in codex_rows if row.get("baseline_cache_hit") is False),
        "fresh_run_rows": sum(1 for row in rows if row.get("fresh_run") is True),
        "reused_rows": sum(1 for row in rows if row.get("fresh_run") is False),
        "cache_adjusted_experiment_codex_work": fresh_cost,
    }


def repair_policy_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    decisions: dict[str, int] = {}
    blockers: dict[str, int] = {}
    for row in rows:
        decision = str(row.get("repair_decision") or "unknown")
        decisions[decision] = decisions.get(decision, 0) + 1
        blocker = row.get("repair_blocker")
        if blocker:
            text = str(blocker)
            blockers[text] = blockers.get(text, 0) + 1
    enabled_rows = [row for row in rows if row.get("repair_policy_enabled") is True]
    return {
        "schema_version": REPAIR_POLICY_VERSION,
        "repair_policy_version": REPAIR_POLICY_VERSION,
        "repair_policy_enabled": bool(enabled_rows),
        "default_repair_attempt_budget": DEFAULT_REPAIR_ATTEMPT_BUDGET,
        "allowed_repair_failure_classes": sorted(ALLOWED_REPAIR_FAILURE_CLASSES),
        "blocked_repair_failure_classes": sorted(BLOCKED_REPAIR_FAILURE_CLASSES),
        "allowed_repair_size_classes": sorted(ALLOWED_REPAIR_SIZE_CLASSES),
        "blocked_repair_size_classes": sorted(BLOCKED_REPAIR_SIZE_CLASSES),
        "repair_field_names": repair_policy_fields(),
        "rows_with_policy_enabled": len(enabled_rows),
        "repair_decisions": decisions,
        "repair_blockers": blockers,
        "repair_attempt_budget_total": sum(int(row.get("repair_attempt_budget") or 0) for row in enabled_rows),
        "repair_attempts_used_total": sum(int(row.get("repair_attempts_used") or 0) for row in enabled_rows),
        "post_repair_validation_statuses": sorted({str(row.get("post_repair_validation_status")) for row in rows if row.get("post_repair_validation_status")}),
        "post_repair_strict_accepted_count": sum(1 for row in rows if row.get("post_repair_strict_accepted") is True),
        "repaired_success_count": sum(
            1
            for row in rows
            if int(row.get("repair_attempts_used") or 0) > 0
            and row.get("post_repair_strict_accepted") is True
            and row.get("strict_accepted") is True
        ),
    }


def aggregate_components(rows: list[dict[str, Any]], *, quality_green_only: bool) -> dict[str, dict[str, int]]:
    quality = {f"{row.get('task')}::{row.get('mode')}": row.get("quality") for row in rows}
    baseline_quality = {row.get("task"): row.get("quality") for row in rows if row.get("mode") == "codex_only"}
    green_tasks = {
        row.get("task")
        for row in rows
        if row.get("mode") != "codex_only"
        and row.get("quality") == "pass"
        and baseline_quality.get(row.get("task")) == "pass"
    }
    groups: dict[str, dict[str, int]] = {}
    for item in component_rows(rows):
        if quality_green_only:
            if item.get("arm") == "codex_only" and item.get("task") not in green_tasks:
                continue
            if item.get("arm") != "codex_only" and quality.get(f"{item.get('task')}::{item.get('arm')}") != "pass":
                continue
        usage = item.get("usage") if isinstance(item.get("usage"), dict) else {}
        key = f"{item.get('arm')}::{item.get('phase')}::{item.get('component')}"
        group = groups.setdefault(key, {"effective_codex_work": 0, "uncached_input": 0, "output": 0, "reasoning_output": 0})
        for field in ("effective_codex_work", "uncached_input", "output", "reasoning_output"):
            group[field] += int(usage.get(field) or 0)
    return groups


def summarize(report_dir: Path, rows: list[dict[str, Any]], repo_root: Path) -> dict[str, Any]:
    pairs = []
    for task in sorted({row["task"] for row in rows}):
        direct = next((row for row in rows if row["task"] == task and row["mode"] == "codex_only"), None)
        zcode = next((row for row in rows if row["task"] == task and row["mode"] == "zcode_delegated"), None)
        if direct is None or zcode is None:
            continue
        direct_tokens = metric(direct)
        zcode_tokens = metric(zcode)
        reduction = round((direct_tokens - zcode_tokens) / direct_tokens * 100, 2) if direct_tokens and zcode_tokens is not None else None
        pairs.append(
            {
                "task": task,
                "kind": direct["kind"],
                "codex_quality": direct["quality"],
                "zcode_quality": zcode["quality"],
                "codex_seconds": direct["duration_seconds"],
                "zcode_seconds": zcode["duration_seconds"],
                "codex_tokens": direct_tokens,
                "zcode_codex_tokens": zcode_tokens,
                "reduction_percent": reduction,
                "codex_change": delta_label(direct),
                "zcode_change": delta_label(zcode),
                "zcode_acceptance": zcode.get("zcode_acceptance", {}),
                "strict_accepted": zcode.get("strict_accepted"),
                "strict_violations": zcode.get("strict_violations", []),
                "strict_failure_classification": zcode.get("strict_failure_classification", []),
                "repair_decision": zcode.get("repair_decision"),
                "repair_attempt_budget": zcode.get("repair_attempt_budget"),
                "repair_attempts_used": zcode.get("repair_attempts_used"),
                "repair_size_class": zcode.get("repair_size_class"),
                "repair_blocker": zcode.get("repair_blocker"),
                "post_repair_validation_status": zcode.get("post_repair_validation_status"),
                "post_repair_strict_accepted": zcode.get("post_repair_strict_accepted"),
                "contract_visible_bytes": zcode.get("contract_visible_bytes"),
                "self_audit_coverage": zcode.get("self_audit_coverage", {}),
            }
        )
    all_rows = comparison_summary(rows, quality_green_only=False)
    green_rows = comparison_summary(rows, quality_green_only=True)
    strict_green_rows = comparison_summary(rows, quality_green_only=True, strict_green_only=True)
    delegated_totals = all_rows["by_arm"].get("zcode_delegated", {})
    legacy_totals = {
        "direct_seconds": round(float(delegated_totals.get("baseline_seconds") or 0), 3),
        "zcode_seconds": round(float(delegated_totals.get("arm_seconds") or 0), 3),
        "zcode_time_ratio": delegated_totals.get("time_ratio"),
        "direct_codex_effective_work": delegated_totals.get("baseline_effective"),
        "zcode_codex_effective_work": delegated_totals.get("arm_effective"),
        "codex_token_reduction_percent": delegated_totals.get("reduction_percent"),
    }
    measurement_modes = sorted({row.get("measurement_mode") for row in rows if row.get("measurement_mode")})
    return {
        "repo_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_root, text=True).strip(),
        "repo_branch": subprocess.check_output(["git", "branch", "--show-current"], cwd=repo_root, text=True).strip(),
        "measurement_mode": measurement_modes[0] if len(measurement_modes) == 1 else "mixed",
        "measurement_modes": measurement_modes,
        "codex_token_metric": PRIMARY_METRIC,
        "codex_token_metric_formula": PRIMARY_METRIC_FORMULA,
        "dirty_tree": {
            "status_short": (report_dir / "dirty-status.txt").read_text(encoding="utf-8").splitlines(),
            "diff_sha256": (report_dir / "dirty-diff.sha256").read_text(encoding="utf-8").strip(),
        },
        "planning_usage_status": "phase_split_available_for_codex_exec_rows",
        "planning_usage_reason": "Codex exec rows now record task, arm, phase, prompt_hash, and compact usage fields.",
        "acceptance_shadow_audit_usage_status": "unavailable",
        "acceptance_shadow_audit_usage_reason": "strict acceptance remains deterministic/non-LLM unless a separate Codex shadow audit run is added.",
        "roi_estimate_status": {"measured": False, "estimate_method": "unavailable"},
        "pairs": pairs,
        "rows": rows,
        "codex_exec_rows": codex_exec_rows(rows),
        "component_rows": component_rows(rows),
        "baseline_cache": baseline_cache_summary(rows),
        "repair_policy": repair_policy_summary(rows),
        "token_delta": {
            "all_rows": {**all_rows, "by_phase_component": aggregate_components(rows, quality_green_only=False)},
            "quality_green_subset": {**green_rows, "by_phase_component": aggregate_components(rows, quality_green_only=True)},
            "strict_green_subset": strict_green_rows,
        },
        "claim_families": {
            "apparent-all": claim_families(all_rows, rows, scope="apparent-all"),
            "quality-green": claim_families(green_rows, rows, scope="quality-green"),
            "strict-green": claim_families(strict_green_rows, rows, scope="strict-green"),
        },
        "end_to_end_accounting": end_to_end_accounting(rows),
        "totals": legacy_totals,
    }


def write_markdown(report_dir: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Strict Contract V3 Comparison",
        "",
        f"- repo branch: `{summary['repo_branch']}`",
        f"- repo head: `{summary['repo_head'][:12]}`",
        f"- measurement mode: `{summary['measurement_mode']}`",
        f"- token metric: `{summary['codex_token_metric']}`",
        f"- token formula: `{summary['codex_token_metric_formula']}`",
        f"- repair policy: `{summary['repair_policy']['repair_policy_version']}`",
        f"- default repair attempt budget: `{summary['repair_policy']['default_repair_attempt_budget']}`",
        f"- dirty diff sha256: `{summary['dirty_tree']['diff_sha256']}`",
        "",
        "## Task/Arm Delta",
        "",
        "| task | arm | mode | Codex quality | arm quality | strict | failure class | baseline effective | arm effective | delta | reduction | arm sec |",
        "| --- | --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in summary["token_delta"]["all_rows"]["task_arm"]:
        reduction = "" if row["reduction_percent"] is None else f"{row['reduction_percent']}%"
        failure_class = ", ".join(row.get("strict_failure_classification") or [])
        lines.append(
            f"| `{row['task']}` | `{row['arm']}` | `{row.get('measurement_mode')}` | {row['codex_quality']} | {row['arm_quality']} | "
            f"{row.get('strict_accepted')} | {failure_class} | {row['baseline_effective']} | {row['arm_effective']} | "
            f"{row['delta_effective']} | {reduction} | {row['arm_seconds']} |"
        )
    lines.extend(["", "## Repair Policy", ""])
    repair = summary["repair_policy"]
    lines.extend(
        [
            f"- version: `{repair['repair_policy_version']}`",
            f"- default attempt budget: `{repair['default_repair_attempt_budget']}`",
            f"- decisions: `{json.dumps(repair['repair_decisions'], sort_keys=True)}`",
            f"- blockers: `{json.dumps(repair['repair_blockers'], sort_keys=True)}`",
            f"- attempts used: `{repair['repair_attempts_used_total']}` / budget `{repair['repair_attempt_budget_total']}`",
            "",
            "| task | arm | decision | budget | used | size | blocker | post-repair validation | post-repair strict |",
            "| --- | --- | --- | ---: | ---: | --- | --- | --- | --- |",
        ]
    )
    for row in summary["rows"]:
        if row.get("mode") == "codex_only":
            continue
        lines.append(
            f"| `{row.get('task')}` | `{row.get('mode')}` | `{row.get('repair_decision')}` | "
            f"{row.get('repair_attempt_budget')} | {row.get('repair_attempts_used')} | "
            f"`{row.get('repair_size_class')}` | `{row.get('repair_blocker')}` | "
            f"`{row.get('post_repair_validation_status')}` | `{row.get('post_repair_strict_accepted')}` |"
        )
    lines.extend(["", "## By Arm", ""])
    for label, bucket in (("All rows", "all_rows"), ("Quality-green subset", "quality_green_subset")):
        lines.extend([f"### {label}", ""])
        for arm, totals in summary["token_delta"][bucket]["by_arm"].items():
            lines.append(
                f"- `{arm}`: baseline `{totals['baseline_effective']}`, arm `{totals['arm_effective']}`, "
                f"delta `{totals['delta_effective']}`, reduction `{totals['reduction_percent']}%`, "
                f"time ratio `{totals['time_ratio']}`"
            )
        lines.append("")
    lines.extend(["## Phase/Component Totals", ""])
    for bucket in ("all_rows", "quality_green_subset"):
        lines.append(f"### {bucket}")
        for key, values in sorted(summary["token_delta"][bucket]["by_phase_component"].items()):
            lines.append(f"- `{key}`: `{values['effective_codex_work']}` effective")
        lines.append("")
    lines.extend(
        [
            "## Baseline Cache",
            "",
            f"- hits: `{summary['baseline_cache']['baseline_cache_hits']}`",
            f"- misses: `{summary['baseline_cache']['baseline_cache_misses']}`",
            f"- cache-adjusted experiment Codex work: `{summary['baseline_cache']['cache_adjusted_experiment_codex_work']}`",
            "",
            "## Claim Families",
            "",
        ]
    )
    for scope, families in summary["claim_families"].items():
        for family, payload in families.items():
            lines.append(
                f"- `{scope}` / `{family}`: arm `{payload['arm']}`, mixed modes `{payload['mixed_measurement_modes']}`, "
                f"deployable allowed `{payload['deployable_claim_allowed']}`"
            )
    lines.append("")
    lines.extend(["## End-to-End Accounting", ""])
    for scope, arms in summary["end_to_end_accounting"].items():
        if scope in {"schema_version", "metric", "metric_formula", "layers"}:
            continue
        lines.append(f"### {scope}")
        for arm, payload in arms.items():
            lines.append(f"- `{arm}`")
            for key in ("delegated_arm_codex_savings", "total_codex_side_savings", "total_workflow_savings"):
                layer = payload[key]
                lines.append(
                    f"  - `{key}`: status `{layer['usage_status']}`, baseline `{layer['baseline_work']}`, "
                    f"arm `{layer['arm_work']}`, reduction `{layer['reduction_percent']}`, "
                    f"deployable `{layer['deployable_claim_allowed']}`"
                )
        lines.append("")
    lines.extend(["## Worker Usage", ""])
    for row in summary["rows"]:
        if row.get("mode") == "codex_only":
            continue
        lines.append(
            f"- `{row.get('task')}` / `{row.get('mode')}`: worker `{row.get('worker_kind')}`, "
            f"provider `{row.get('worker_provider')}`, model `{row.get('worker_model')}`, "
            f"status `{row.get('worker_usage_status')}`, unit `{row.get('worker_usage_unit')}`, "
            f"tokens `{row.get('worker_total_tokens')}`, reason `{row.get('worker_usage_no_usage_reason')}`"
        )
    lines.append("")
    lines.extend(
        [
            "## Limits",
            "",
            "- ZCode worker/model tokens are included in total workflow accounting only when measured in token units.",
            "- Quota percent and credit usage are recorded separately and are not converted into tokens.",
            "- Missing usage is `unavailable`, not zero.",
            "- Repair never overrides strict acceptance and never hides the original failure classification.",
            "- Repaired success remains distinguishable from first-pass success by repair attempt and post-repair fields.",
            f"- Acceptance/shadow audit usage is `{summary['acceptance_shadow_audit_usage_status']}`: {summary['acceptance_shadow_audit_usage_reason']}",
            f"- ROI estimate is `{summary['roi_estimate_status']['estimate_method']}`, measured=`{summary['roi_estimate_status']['measured']}`.",
        ]
    )
    write_text(report_dir / "summary.md", "\n".join(lines) + "\n")
