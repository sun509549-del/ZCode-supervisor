"""Strict Contract V3 rubric expansion and deterministic acceptance."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from .metrics import PRIMARY_METRIC, PRIMARY_METRIC_FORMULA
except ImportError:  # pragma: no cover - direct script execution
    from metrics import PRIMARY_METRIC, PRIMARY_METRIC_FORMULA

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STRICT_CONTRACT_DIR = REPO_ROOT / "docs" / "zcode-strict-contract-v3"
DEFAULT_RUBRIC_DIR = Path(__file__).resolve().with_name("rubrics")
DEFAULT_CONTRACT_LEDGER = Path("artifacts/evals/strict-contract-ledger.jsonl")
DEFAULT_TRACE_LEDGER = Path("artifacts/evals/strict-contract-trace-ledger.jsonl")
DEFAULT_ACCEPTANCE_LEDGER = Path("artifacts/evals/strict-contract-acceptance-ledger.jsonl")
DEFAULT_ROI_LEDGER = Path("artifacts/evals/strict-contract-roi-ledger.jsonl")
ACCEPTANCE_MODES = ("manifest_only", "green_path_skip", "codex_anomaly_audit", "shadow_codex_audit")
REPAIR_SIZES = ("none", "small_polish", "substantial_repair", "rewrite", "unknown")
RISK_LEVELS = ("L0", "L1", "L2", "L3", "L4")
LOCAL_VALIDATION_UNAVAILABLE_NEEDLES = (
    "bash",
    "permission",
    "could not run",
    "cannot run",
    "unavailable",
    "skipped",
    "not run",
    "pending",
    "supervisor",
)
LINE_RANGE_SUFFIX_RE = re.compile(r":(?P<ranges>\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*)$")
KNOWN_PATH_REASON_SUFFIXES = {
    "forbidden_file_changed",
    "forbidden_files_changed",
    "missing_allowed_files",
    "outside_allowed_files",
    "path_absolute",
    "path_backslash",
    "path_empty",
    "path_parent",
    "path_safety",
}


@dataclass(frozen=True)
class PathReference:
    raw: str
    path: str | None
    ranges: list[tuple[int, int]]
    reason: str | None
    safety_error: str | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected JSON object")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def json_bytes(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rubric_path(rubric_dir: Path, rubric_id: str) -> Path:
    candidate = rubric_dir / f"{rubric_id}.json"
    if candidate.exists():
        return candidate
    candidate = rubric_dir / rubric_id
    if candidate.exists():
        return candidate
    raise FileNotFoundError(f"rubric not found: {rubric_id} in {rubric_dir}")


def load_overrides(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    return read_json_object(path)


def list_from_args(values: list[str] | None) -> list[str]:
    return sorted(dict.fromkeys(values or []))


def build_contract_payload(args: argparse.Namespace) -> dict[str, Any]:
    source = rubric_path(args.rubric_dir, args.rubric_id)
    rubric = read_json_object(source)
    overrides = load_overrides(args.override_json)
    risk_level = args.risk_level or rubric.get("default_risk_level", "L2")
    contract = {
        "schema_version": "task_contract.v1",
        "contract_id": args.contract_id or f"{args.task_id}@{rubric['rubric_id']}",
        "task_id": args.task_id,
        "rubric_id": rubric["rubric_id"],
        "rubric_sha256": sha256_file(source),
        "risk_level": risk_level,
        "ambiguity_score": args.ambiguity_score,
        "allowed_files": list_from_args(args.allowed_file),
        "forbidden_files": list_from_args(args.forbidden_file),
        "goal": args.goal or rubric.get("goal_template", ""),
        "non_goals": list(rubric.get("non_goals", [])),
        "requirements": list(rubric.get("requirements", [])),
        "edge_cases": list(rubric.get("edge_cases", [])),
        "forbidden_actions": list(rubric.get("forbidden_actions", [])),
        "implementation_plan": list(rubric.get("implementation_plan", [])),
        "expected_diff_budget": dict(rubric.get("expected_diff_budget", {})),
        "validation_commands": list_from_args(args.validation_command),
        "acceptance_rules": list(rubric.get("acceptance_rules", [])),
        "failure_protocol": (
            "Return blocked rather than guessing when any blocking requirement is "
            "ambiguous, unverifiable, or impossible within allowed file scope."
        ),
        "self_audit_schema_ref": "docs/zcode-strict-contract-v3/schemas/zcode_self_audit.schema.json",
    }
    for key, value in overrides.items():
        if key in contract and key not in {"schema_version", "rubric_sha256"}:
            contract[key] = value
    return contract


def contract_ledger_record(args: argparse.Namespace, contract: dict[str, Any]) -> dict[str, Any]:
    visible_bytes = json_bytes(contract)
    generation_usage = args.generation_effective_codex_work
    return {
        "schema_version": "contract_ledger.v1",
        "recorded_at": utc_now(),
        "experiment_id": args.experiment_id,
        "task_id": contract["task_id"],
        "contract_id": contract["contract_id"],
        "rubric_id": contract["rubric_id"],
        "risk_level": contract["risk_level"],
        "ambiguity_score": contract.get("ambiguity_score", 0),
        "contract_generation": {
            "codex_run_id": args.codex_run_id,
            "effective_codex_work": generation_usage,
            "usage_missing": generation_usage is None,
        },
        "contract_size": {
            "codex_visible_bytes": visible_bytes,
            "zcode_visible_bytes": visible_bytes,
            "expanded_by_non_llm": True,
        },
        "requirements": {
            "count": len(contract.get("requirements", [])),
            "edge_case_count": len(contract.get("edge_cases", [])),
            "forbidden_action_count": len(contract.get("forbidden_actions", [])),
        },
        "reuse": {"rubric_reused": True, "new_rubric_created": False},
    }


def command_build_strict_contract(args: argparse.Namespace) -> int:
    contract = build_contract_payload(args)
    write_json(args.out, contract)
    if args.contract_ledger:
        append_jsonl(args.contract_ledger, contract_ledger_record(args, contract))
    print(json.dumps(contract, indent=2, sort_keys=True))
    return 0


def normalize_rel_path(raw_path: str) -> tuple[str | None, str | None]:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None, "path_empty"
    if "\\" in raw_path:
        return None, "path_backslash"
    posix = PurePosixPath(raw_path)
    if posix.is_absolute() or Path(raw_path).is_absolute():
        return None, "path_absolute"
    if any(part == ".." for part in posix.parts):
        return None, "path_parent"
    return str(posix), None


def parse_line_ranges(raw_ranges: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for part in raw_ranges.split(","):
        if "-" in part:
            start, end = part.split("-", 1)
        else:
            start = end = part
        ranges.append((int(start), int(end)))
    return ranges


def parse_path_reference(raw_ref: str) -> PathReference:
    text = raw_ref.strip() if isinstance(raw_ref, str) else ""
    if not text:
        return PathReference(raw=str(raw_ref), path=None, ranges=[], reason=None, safety_error="path_empty")

    reason = None
    candidate = text
    prefix, sep, suffix = text.rpartition(":")
    if sep and prefix and suffix in KNOWN_PATH_REASON_SUFFIXES:
        reason = suffix
        candidate = prefix

    ranges: list[tuple[int, int]] = []
    match = LINE_RANGE_SUFFIX_RE.search(candidate)
    if match:
        ranges = parse_line_ranges(match.group("ranges"))
        candidate = candidate[: match.start()]

    path, problem = normalize_rel_path(candidate)
    return PathReference(raw=text, path=path, ranges=ranges, reason=reason, safety_error=problem)


def pattern_matches(path: str, pattern: str) -> bool:
    normalized, problem = normalize_rel_path(pattern)
    if problem:
        return False
    assert normalized is not None
    if normalized.endswith("/"):
        return path.startswith(normalized)
    if any(char in normalized for char in "*?[]"):
        return fnmatch.fnmatch(path, normalized)
    return path == normalized


def path_policy_violations(changed_files: list[str], contract: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    allowed = contract.get("allowed_files") if isinstance(contract.get("allowed_files"), list) else []
    forbidden = contract.get("forbidden_files") if isinstance(contract.get("forbidden_files"), list) else []
    for raw_path in changed_files:
        ref = parse_path_reference(raw_path)
        if ref.safety_error or ref.path is None:
            violations.append(f"{raw_path}:path_safety:{ref.safety_error}")
            continue
        path = ref.path
        if not any(pattern_matches(path, str(pattern)) for pattern in allowed):
            violations.append(f"{path}:outside_allowed_files")
        if any(pattern_matches(path, str(pattern)) for pattern in forbidden):
            violations.append(f"{path}:forbidden_file_changed")
    return violations


def evidence_types(trace: dict[str, Any]) -> set[str]:
    evidence = trace.get("evidence")
    if not isinstance(evidence, list):
        return set()
    return {item.get("type") for item in evidence if isinstance(item, dict) and isinstance(item.get("type"), str)}


def audit_requirement_traces(audit: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("requirements", "requirement_trace"):
        traces = audit.get(key)
        if isinstance(traces, list):
            return [item for item in traces if isinstance(item, dict)]
    evidence = audit.get("requirements_evidence")
    if not isinstance(evidence, dict):
        return []
    traces: list[dict[str, Any]] = []
    for req_id, payload in evidence.items():
        if not isinstance(payload, dict):
            continue
        trace = dict(payload)
        trace.setdefault("id", str(req_id))
        traces.append(trace)
    return traces


def audit_edge_traces(audit: dict[str, Any]) -> list[dict[str, Any]]:
    edge_cases = audit.get("edge_cases")
    if isinstance(edge_cases, list):
        return [item for item in edge_cases if isinstance(item, dict)]
    coverage = audit.get("edge_case_coverage")
    if not isinstance(coverage, dict):
        return []
    traces: list[dict[str, Any]] = []
    for edge_id, payload in coverage.items():
        if not isinstance(payload, dict):
            continue
        trace = dict(payload)
        trace.setdefault("id", str(edge_id))
        evidence = trace.get("evidence")
        if isinstance(evidence, str):
            trace["evidence"] = [{"type": "manual_trace", "detail": evidence}]
        traces.append(trace)
    return traces


def audit_risk_flag_labels(audit: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for item in audit.get("risk_flags") or []:
        if isinstance(item, str):
            labels.append(item)
            continue
        if isinstance(item, dict):
            flag_id = item.get("id") or item.get("flag") or item.get("area") or item.get("severity") or "risk_flag"
            detail = item.get("detail") or item.get("reason")
            labels.append(f"{flag_id}: {detail}" if detail else str(flag_id))
            continue
        labels.append(str(item))
    return labels


def strip_text_location(raw_ref: str) -> str:
    ref = parse_path_reference(raw_ref)
    return ref.path if ref.path is not None else raw_ref


def local_validation_unavailable_item(item: Any) -> bool:
    text = json.dumps(item, sort_keys=True).lower() if isinstance(item, (dict, list)) else str(item).lower()
    validation_context = any(needle in text for needle in ("validation", "npm test", "supervisor", "bash"))
    return validation_context and any(needle in text for needle in LOCAL_VALIDATION_UNAVAILABLE_NEEDLES)


def list_items(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def all_items_are_local_validation(items: list[Any]) -> bool:
    return bool(items) and all(local_validation_unavailable_item(item) for item in items)


def no_op_deviation(item: Any) -> bool:
    text = json.dumps(item, sort_keys=True).lower() if isinstance(item, (dict, list)) else str(item).lower()
    return "no deviation" in text or "no deviations" in text


def actionable_deviations(audit: dict[str, Any]) -> list[Any]:
    return [item for item in list_items(audit.get("deviations_from_plan")) if not no_op_deviation(item)]


def supervisor_validation_backfill_used(args: argparse.Namespace, audit: dict[str, Any]) -> bool:
    if args.validation_result != "pass" or args.validation_exit_code != 0:
        return False
    validation = audit.get("validation") if isinstance(audit.get("validation"), dict) else {}
    result = str(validation.get("result") or "").lower()
    if result in {"skipped", "unknown", "not_run", "not_run_locally"}:
        return True
    return local_validation_unavailable_item(validation)


def filter_validation_backfilled_violations(violations: list[str], backfilled: bool) -> list[str]:
    if not backfilled:
        return violations
    return [
        violation
        for violation in violations
        if not violation.endswith(":missing_evidence:validation_result")
    ]


def supervisor_diffstat_backfill_used(args: argparse.Namespace) -> bool:
    return args.files_changed is not None or args.insertions is not None or args.deletions is not None


def filter_diffstat_backfilled_violations(violations: list[str], backfilled: bool) -> list[str]:
    if not backfilled:
        return violations
    return [
        violation
        for violation in violations
        if not violation.endswith(":missing_evidence:diffstat")
    ]


def requirement_status_satisfied(trace: dict[str, Any], validation_backfilled: bool) -> bool:
    if trace.get("status") == "satisfied":
        return True
    if not validation_backfilled:
        return False
    if trace.get("status") != "unknown":
        return False
    return local_validation_unavailable_item(trace)


def changed_files_from_audit(audit: dict[str, Any], explicit: list[str]) -> list[str]:
    files = list(explicit)
    for item in audit.get("changed_files") or []:
        if isinstance(item, str):
            files.append(strip_text_location(item))
        elif isinstance(item, dict) and isinstance(item.get("path"), str):
            files.append(strip_text_location(item["path"]))
    for trace in audit_requirement_traces(audit) + audit_edge_traces(audit):
        if not isinstance(trace, dict):
            continue
        for item in trace.get("evidence") or []:
            if isinstance(item, dict) and item.get("type") == "changed_file" and isinstance(item.get("ref"), str):
                files.append(strip_text_location(item["ref"]))
    return sorted(dict.fromkeys(files))


def requirement_violations(contract: dict[str, Any], audit: dict[str, Any], validation_backfilled: bool) -> list[str]:
    violations: list[str] = []
    traces = {
        trace.get("id"): trace
        for trace in audit_requirement_traces(audit)
        if isinstance(trace, dict) and isinstance(trace.get("id"), str)
    }
    for requirement in contract.get("requirements") or []:
        if not isinstance(requirement, dict):
            continue
        req_id = requirement.get("id")
        trace = traces.get(req_id)
        if trace is None:
            violations.append(f"{req_id}:missing_trace")
            continue
        if requirement.get("blocking") is True and not requirement_status_satisfied(trace, validation_backfilled):
            violations.append(f"{req_id}:blocking_not_satisfied")
        if requirement.get("blocking") is True and not trace.get("evidence"):
            violations.append(f"{req_id}:missing_evidence")
        present = evidence_types(trace)
        for required_type in requirement.get("evidence_required") or []:
            if required_type not in present:
                violations.append(f"{req_id}:missing_evidence:{required_type}")
    return violations


def validation_violations(args: argparse.Namespace, audit: dict[str, Any], validation_backfilled: bool) -> list[str]:
    if validation_backfilled:
        return []
    validation = audit.get("validation") if isinstance(audit.get("validation"), dict) else {}
    actual = args.validation_result or validation.get("result")
    violations: list[str] = []
    if args.validation_result is None:
        violations.append("validation_result_unverified")
    if validation.get("result") != actual:
        violations.append("validation_result_mismatch")
    if actual != "pass":
        violations.append("validation_result")
    if args.validation_exit_code is None:
        violations.append("validation_exit_code_unverified")
    elif actual == "pass" and args.validation_exit_code != 0:
        violations.append("validation_exit_code")
    return violations


def diff_budget_violations(args: argparse.Namespace, contract: dict[str, Any], audit: dict[str, Any], changed_files: list[str]) -> list[str]:
    budget = contract.get("expected_diff_budget") if isinstance(contract.get("expected_diff_budget"), dict) else {}
    files_changed = args.files_changed if args.files_changed is not None else len(changed_files)
    insertions = args.insertions or 0
    deletions = args.deletions or 0
    exceeded = (
        files_changed > int(budget.get("files_changed_max", files_changed))
        or insertions > int(budget.get("insertions_soft_max", insertions))
        or deletions > int(budget.get("deletions_soft_max", deletions))
    )
    if not exceeded:
        return []
    if budget.get("if_exceeded") == "fail_closed":
        return ["diff_budget_exceeded"]
    flags = [item.lower() for item in audit_risk_flag_labels(audit)]
    if not any("diff" in flag or "budget" in flag for flag in flags):
        return ["diff_budget_exceeded_without_risk_flag"]
    return ["diff_budget_exceeded_audit_required"]


def trace_coverage_record(
    args: argparse.Namespace,
    contract: dict[str, Any],
    audit: dict[str, Any],
    changed_files: list[str],
    violations: list[str],
    validation_backfilled: bool,
) -> dict[str, Any]:
    reqs = contract.get("requirements") or []
    traces = audit_requirement_traces(audit)
    edge_traces = audit_edge_traces(audit)
    return {
        "schema_version": "trace_coverage.v1",
        "recorded_at": utc_now(),
        "experiment_id": args.experiment_id,
        "task_id": contract.get("task_id"),
        "contract_id": contract.get("contract_id"),
        "requirements_total": len(reqs),
        "blocking_requirements_total": sum(1 for req in reqs if isinstance(req, dict) and req.get("blocking") is True),
        "requirements_claimed_satisfied": sum(1 for trace in traces if trace.get("status") == "satisfied"),
        "requirements_effectively_satisfied": sum(
            1 for trace in traces if requirement_status_satisfied(trace, validation_backfilled)
        ),
        "requirements_with_evidence": sum(1 for trace in traces if bool(trace.get("evidence"))),
        "edge_cases_total": len(contract.get("edge_cases") or []),
        "edge_cases_with_evidence": sum(1 for trace in edge_traces if bool(trace.get("evidence"))),
        "changed_files": changed_files,
        "deviation_count": len(actionable_deviations(audit)),
        "unresolved_question_count": len(audit.get("unresolved_questions") or []),
        "risk_flag_count": len(audit.get("risk_flags") or []),
        "blocked_status": audit.get("overall_status") == "blocked",
        "violations": violations,
    }


def build_acceptance_result_for_payload(
    args: argparse.Namespace,
    contract: dict[str, Any],
    audit: dict[str, Any],
) -> dict[str, Any]:
    changed_files = changed_files_from_audit(audit, args.changed_file)
    validation_backfilled = supervisor_validation_backfill_used(args, audit)
    diffstat_backfilled = supervisor_diffstat_backfill_used(args)
    violations: list[str] = []
    if audit.get("contract_id") != contract.get("contract_id"):
        violations.append("contract_id_mismatch")
    if audit.get("task_id") != contract.get("task_id"):
        violations.append("task_id_mismatch")
    blocked_only_for_local_validation = (
        validation_backfilled
        and audit.get("overall_status") == "blocked"
        and all_items_are_local_validation(list_items(audit.get("blocked_reasons")))
    )
    unknown_only_for_local_validation = (
        validation_backfilled
        and audit.get("overall_status") == "unknown"
        and not list_items(audit.get("blocked_reasons"))
    )
    if audit.get("overall_status") != "pass" and not blocked_only_for_local_validation and not unknown_only_for_local_validation:
        violations.append(f"overall_status:{audit.get('overall_status')}")
    if audit.get("blocked_reasons") and audit.get("overall_status") == "pass":
        violations.append("pass_with_blocked_reasons")
    if actionable_deviations(audit):
        violations.append("deviations_from_plan")
    if audit.get("unresolved_questions") and not (
        validation_backfilled and all_items_are_local_validation(list_items(audit.get("unresolved_questions")))
    ):
        violations.append("unresolved_questions")
    risk_flag_items = list_items(audit.get("risk_flags"))
    non_validation_risk_flags = [
        item for item in risk_flag_items if not (validation_backfilled and local_validation_unavailable_item(item))
    ]
    if non_validation_risk_flags:
        violations.append("risk_flags")
    requirement_findings = requirement_violations(contract, audit, validation_backfilled)
    requirement_findings = filter_validation_backfilled_violations(requirement_findings, validation_backfilled)
    requirement_findings = filter_diffstat_backfilled_violations(requirement_findings, diffstat_backfilled)
    violations.extend(requirement_findings)
    violations.extend(validation_violations(args, audit, validation_backfilled))
    violations.extend(path_policy_violations(changed_files, contract))
    violations.extend(diff_budget_violations(args, contract, audit, changed_files))
    trace = trace_coverage_record(args, contract, audit, changed_files, violations, validation_backfilled)
    compact_visible = json_bytes({"contract_id": contract.get("contract_id"), "trace_coverage": trace})
    accepted = not violations
    return {
        "schema_version": "strict_contract_acceptance_result.v1",
        "accepted": accepted,
        "violations": violations,
        "trace_coverage": trace,
        "validation_backfill": {
            "used": validation_backfilled,
            "source": "codex_supervisor" if validation_backfilled else None,
            "result": args.validation_result if validation_backfilled else None,
            "exit_code": args.validation_exit_code if validation_backfilled else None,
        },
        "diffstat_backfill": {
            "used": diffstat_backfilled,
            "source": "codex_supervisor" if diffstat_backfilled else None,
            "files_changed": args.files_changed if diffstat_backfilled else None,
            "insertions": args.insertions if diffstat_backfilled else None,
            "deletions": args.deletions if diffstat_backfilled else None,
        },
        "codex_acceptance_audit": codex_acceptance_audit_payload(args, contract, audit, violations, compact_visible),
        "green_path_acceptance": green_path_acceptance_payload(args, accepted),
    }


def strict_acceptance_mode(args: argparse.Namespace, accepted: bool) -> str:
    green_enabled = bool(getattr(args, "green_path_non_llm", False))
    if green_enabled and accepted:
        return "green_path_skip"
    if green_enabled:
        return "codex_anomaly_audit"
    return "codex_anomaly_audit" if args.mode == "green_path_skip" and not accepted else args.mode


def codex_acceptance_audit_payload(
    args: argparse.Namespace,
    contract: dict[str, Any],
    audit: dict[str, Any],
    violations: list[str],
    compact_visible: int,
) -> dict[str, Any]:
    missed = list(getattr(args, "missed_risk_flag", []) or [])
    accepted = not violations
    risk_flags = sorted(dict.fromkeys(audit_risk_flag_labels(audit) + violations + missed))
    return {
        "schema_version": "codex_acceptance_audit.v1",
        "task_id": str(contract.get("task_id")),
        "contract_id": str(contract.get("contract_id")),
        "mode": strict_acceptance_mode(args, accepted),
        "accepted": accepted,
        "codex_visible_bytes": compact_visible,
        "full_diff_read": args.full_diff_read,
        "full_log_read": args.full_log_read,
        "requirements_checked": len(contract.get("requirements") or []),
        "trace_matrix_checked": True,
        "codex_repair_size": args.codex_repair_size,
        "risk_flags": risk_flags,
        "notes": "strict contract trace checked deterministically; raw logs/diff omitted on green path",
    }


def green_path_acceptance_payload(args: argparse.Namespace, accepted: bool) -> dict[str, Any]:
    green_enabled = bool(getattr(args, "green_path_non_llm", False))
    return {
        "enabled": green_enabled,
        "deterministic_accept": accepted,
        "codex_acceptance_skipped": bool(green_enabled and accepted),
        "shadow_codex_audit_enabled": bool(getattr(args, "shadow_codex_audit_enabled", False)),
        "shadow_codex_audit_result": getattr(args, "shadow_codex_audit_result", "unavailable"),
        "missed_risk_flags": list(getattr(args, "missed_risk_flag", []) or []),
    }


def build_acceptance_result(args: argparse.Namespace) -> dict[str, Any]:
    contract = read_json_object(args.contract)
    audit = read_json_object(args.self_audit)
    return build_acceptance_result_for_payload(args, contract, audit)


def roi_record(args: argparse.Namespace, contract_id: str, task_id: str) -> dict[str, Any]:
    return {
        "schema_version": "contract_roi.v1",
        "experiment_id": args.experiment_id,
        "task_id": task_id,
        "contract_id": contract_id,
        "added_spec_effective_tokens": args.added_spec_effective_tokens,
        "acceptance_tokens_saved_estimate": None,
        "repair_tokens_saved_estimate": None,
        "retry_tokens_saved_estimate": None,
        "net_codex_token_delta_estimate": None,
        "estimate_method": "unavailable",
        "measured": False,
    }


def command_accept_strict_contract(args: argparse.Namespace) -> int:
    result = build_acceptance_result(args)
    if args.acceptance_out:
        write_json(args.acceptance_out, result)
    if args.trace_ledger:
        append_jsonl(args.trace_ledger, result["trace_coverage"])
    if args.acceptance_ledger:
        append_jsonl(args.acceptance_ledger, result["codex_acceptance_audit"])
    if args.roi_ledger:
        audit = result["codex_acceptance_audit"]
        append_jsonl(args.roi_ledger, roi_record(args, audit["contract_id"], audit["task_id"]))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["accepted"] else 1


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if text:
                records.append(json.loads(text))
    return records


def strict_task_totals(traces: list[dict[str, Any]], acceptances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tasks = sorted({str(row.get("task_id")) for row in traces + acceptances if row.get("task_id")})
    task_totals = []
    for task in tasks:
        trace = next((row for row in traces if row.get("task_id") == task), {})
        accepted = [row for row in acceptances if row.get("task_id") == task and row.get("accepted") is True]
        task_totals.append({
            "task_id": task,
            "accepted": bool(accepted),
            "requirements_total": trace.get("requirements_total"),
            "requirements_with_evidence": trace.get("requirements_with_evidence"),
            "violations": trace.get("violations", []),
        })
    return task_totals


def strict_next_target(task_totals: list[dict[str, Any]]) -> str:
    failing = next((row for row in task_totals if not row["accepted"]), None)
    if failing:
        return f"fix strict contract acceptance for {failing['task_id']}"
    return "run shadow audit before production green-path skip"


def strict_summary_payload(
    args: argparse.Namespace,
    contracts: list[dict[str, Any]],
    traces: list[dict[str, Any]],
    acceptances: list[dict[str, Any]],
    roi: list[dict[str, Any]],
) -> dict[str, Any]:
    task_totals = strict_task_totals(traces, acceptances)
    summary = {
        "schema_version": "strict_contract_summary.v1",
        "experiment_id": args.experiment_id,
        "primary_metric": PRIMARY_METRIC,
        "primary_metric_formula": PRIMARY_METRIC_FORMULA,
        "task_totals": task_totals,
        "clean_benchmark_vs_operator_total": {
            "status": "unavailable",
            "reason": "strict-contract summary does not read raw Codex JSONL; use summarize-token-reduction for measured usage totals",
        },
        "contract_generation_tokens": [
            {
                "task_id": row.get("task_id"),
                "contract_id": row.get("contract_id"),
                "effective_codex_work": (row.get("contract_generation") or {}).get("effective_codex_work"),
                "usage_missing": (row.get("contract_generation") or {}).get("usage_missing"),
            }
            for row in contracts
        ],
        "contract_visible_bytes": [
            {
                "task_id": row.get("task_id"),
                "contract_id": row.get("contract_id"),
                "codex_visible_bytes": (row.get("contract_size") or {}).get("codex_visible_bytes"),
                "zcode_visible_bytes": (row.get("contract_size") or {}).get("zcode_visible_bytes"),
                "expanded_by_non_llm": (row.get("contract_size") or {}).get("expanded_by_non_llm"),
            }
            for row in contracts
        ],
        "zcode_self_audit_coverage": traces,
        "deterministic_acceptance_result": [
            {
                "task_id": row.get("task_id"),
                "contract_id": row.get("contract_id"),
                "mode": row.get("mode"),
                "accepted": row.get("accepted"),
                "risk_flags": row.get("risk_flags", []),
            }
            for row in acceptances
        ],
        "codex_acceptance_shadow_audit": [
            row for row in acceptances if row.get("mode") in {"shadow_codex_audit", "green_path_skip"}
        ],
        "contract_roi": roi,
        "trace_coverage": traces,
        "quality_gates": acceptances,
        "repair_burden": {
            "codex_repair_size_counts": {
                size: sum(1 for row in acceptances if row.get("codex_repair_size") == size)
                for size in REPAIR_SIZES
            }
        },
        "wall_clock_breakdown": {
            "status": "unavailable",
            "reason": "wall-clock phase rows are not present in strict contract ledgers yet",
        },
        "phase_split_measurement": {
            "phases": [
                "contract_generation",
                "zcode_launch",
                "zcode_self_audit_parse",
                "deterministic_acceptance",
                "codex_acceptance_audit",
                "shadow_codex_audit",
                "summary_render",
            ],
            "token_policy": "only Codex exec phases may report Codex usage; non-LLM phases record bytes/wall-clock and leave token fields unavailable",
        },
        "contract_records": len(contracts),
        "next_optimization_target": strict_next_target(task_totals),
    }
    return summary


def command_summarize_strict_contract(args: argparse.Namespace) -> int:
    contracts = load_jsonl(args.contract_ledger)
    traces = load_jsonl(args.trace_ledger)
    acceptances = load_jsonl(args.acceptance_ledger)
    roi = load_jsonl(args.roi_ledger)
    summary = strict_summary_payload(args, contracts, traces, acceptances, roi)
    write_json(args.out, summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0
