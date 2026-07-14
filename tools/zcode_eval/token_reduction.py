"""Opt-in token-reduction experiment arms, manifests, and summaries."""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from .codex_context_intake import DEFAULT_CONTEXT_LEDGER, load_context_intake_records
    from .codex_usage import DEFAULT_CODEX_LEDGER, load_codex_usage_records, markdown_table, sum_usage
    from .metrics import PRIMARY_METRIC, PRIMARY_METRIC_FORMULA, token_metric_metadata
except ImportError:  # pragma: no cover - direct script execution
    from codex_context_intake import DEFAULT_CONTEXT_LEDGER, load_context_intake_records
    from codex_usage import DEFAULT_CODEX_LEDGER, load_codex_usage_records, markdown_table, sum_usage
    from metrics import PRIMARY_METRIC, PRIMARY_METRIC_FORMULA, token_metric_metadata

ACCEPTABLE_REPAIR_SIZES = {"none", "small_polish", "small polish"}
DEFAULT_SUMMARY_PATH = Path("artifacts/evals/token-reduction-summary.md")
EXPERIMENT_ARMS: dict[str, dict[str, Any]] = {
    "baseline_latest": {
        "feature_flags": [],
        "default_workflow_changed": False,
        "status": "supported",
    },
    "baseline_plus_logging_only": {
        "feature_flags": ["context_intake_logging"],
        "default_workflow_changed": False,
        "status": "supported",
    },
    "logging_only": {
        "feature_flags": ["context_intake_logging"],
        "default_workflow_changed": False,
        "status": "supported",
    },
    "contract_packet_normal_acceptance": {
        "feature_flags": ["strict_contract_packet", "zcode_self_audit", "normal_codex_acceptance"],
        "default_workflow_changed": False,
        "status": "supported_opt_in",
    },
    "contract_packet_manifest_acceptance": {
        "feature_flags": ["strict_contract_packet", "zcode_self_audit", "manifest_only_acceptance"],
        "default_workflow_changed": False,
        "status": "supported_opt_in",
    },
    "rubric_expanded_manifest_acceptance": {
        "feature_flags": ["rubric_expansion_non_llm", "strict_contract_packet", "manifest_only_acceptance"],
        "default_workflow_changed": False,
        "status": "supported_opt_in",
    },
    "manifest_only_acceptance": {
        "feature_flags": ["context_intake_logging", "manifest_only_acceptance"],
        "default_workflow_changed": False,
        "status": "supported",
    },
    "green_path_non_llm_acceptance": {
        "feature_flags": ["manifest_only_acceptance", "green_path_non_llm_acceptance", "shadow_codex_audit"],
        "default_workflow_changed": False,
        "status": "opt_in_experimental",
    },
    "green_path_non_llm_acceptance_shadow": {
        "feature_flags": ["strict_contract_packet", "green_path_non_llm_acceptance", "shadow_codex_audit"],
        "default_workflow_changed": False,
        "status": "opt_in_experimental",
    },
    "zcode_direct_harness_non_llm_planner": {
        "feature_flags": ["zcode_direct_harness", "deterministic_packet_planner"],
        "default_workflow_changed": False,
        "status": "scaffolded",
    },
    "deterministic_planner_benchmark": {
        "feature_flags": ["zcode_direct_harness", "deterministic_packet_planner", "strict_contract_packet"],
        "default_workflow_changed": False,
        "status": "scaffolded",
    },
    "slim_automation_profile": {
        "feature_flags": ["slim_automation_profile", "context_budget_guard"],
        "default_workflow_changed": False,
        "status": "scaffolded",
    },
}


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected JSON object")
    return payload


def first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def as_bool_pass(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.lower() == "pass"
    return False


def parse_allowed_files_only(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.lower()
        if lowered in {"pass", "true"}:
            return True
        if lowered in {"fail", "false"}:
            return False
    return None


def audit_payload(payload: dict[str, Any]) -> dict[str, Any]:
    audit = payload.get("audit")
    if isinstance(audit, dict):
        return audit
    return payload


def validation_payload(payload: dict[str, Any]) -> dict[str, Any]:
    audit = audit_payload(payload)
    validation = audit.get("validation")
    return validation if isinstance(validation, dict) else {}


def allowed_files_only_from_payload(payload: dict[str, Any]) -> bool | None:
    audit = audit_payload(payload)
    containers: list[dict[str, Any]] = [payload, audit]
    for parent in (payload, audit):
        for key in ("quality", "safety", "allowed_files"):
            nested = parent.get(key)
            if isinstance(nested, dict):
                containers.append(nested)
    for container in containers:
        for key in ("allowed_files_only", "allowed_files_result", "allowed_files_check"):
            parsed = parse_allowed_files_only(container.get(key))
            if parsed is not None:
                return parsed
    return None


def manifest_hash(path: Path | None) -> str | None:
    if path is None or not path.exists() or not path.is_file():
        return None
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def cap_text(text: str, cap: int) -> str:
    data = text.encode("utf-8")
    if len(data) <= cap:
        return text
    return data[:cap].decode("utf-8", errors="replace")


def validation_summary(args: argparse.Namespace, payload: dict[str, Any]) -> dict[str, Any]:
    validation = validation_payload(payload)
    result = first_present(validation.get("result"), audit_payload(payload).get("validation_result"))
    if result is None:
        result = "pass" if validation.get("ok") is True else ("fail" if validation.get("ok") is False else "unknown")
    log_text = args.validation_log.read_text(encoding="utf-8", errors="replace") if args.validation_log and args.validation_log.exists() else ""
    captured = len(log_text.encode("utf-8"))
    if result == "pass":
        summary = cap_text(first_present(validation.get("summary"), "validation passed; raw log referenced by path/hash only"), args.pass_validation_visible_max_bytes)
        visible = len(summary.encode("utf-8"))
    elif log_text:
        summary = cap_text(log_text, args.failure_validation_excerpt_max_bytes)
        visible = len(summary.encode("utf-8"))
    else:
        summary = str(first_present(validation.get("summary"), "validation result unavailable"))
        visible = len(summary.encode("utf-8"))
    return {
        "command_hash": manifest_hash(args.validation_log),
        "exit_code": int(first_present(validation.get("exit_code"), 0 if result == "pass" else 1)),
        "result": result,
        "summary": summary,
        "captured_bytes_total": captured,
        "model_visible_bytes": visible,
    }


def risk_flags_from_payload(payload: dict[str, Any]) -> list[str]:
    audit = audit_payload(payload)
    flags: list[str] = []
    for item in audit.get("violations") or []:
        if isinstance(item, dict):
            flags.append(str(item.get("type") or "violation"))
        else:
            flags.append(str(item))
    for item in payload.get("risk_flags") or []:
        flags.append(str(item))
    return sorted(set(flags))


def changed_files_from_payload(payload: dict[str, Any], args: argparse.Namespace) -> list[str]:
    if args.changed_file:
        return sorted(set(args.changed_file))
    for key in ("changed_files", "files_changed"):
        raw = payload.get(key)
        if isinstance(raw, list):
            return sorted(str(item) for item in raw)
    return []


def changed_file_path_violations(manifest: dict[str, Any], workspace_root: Path) -> list[str]:
    changed_files = manifest.get("changed_files")
    if not isinstance(changed_files, list):
        return ["path_safety_changed_files_invalid"]
    if not changed_files:
        return ["path_safety_no_changed_files"]
    violations: set[str] = set()
    root = workspace_root.resolve()
    for raw_path in changed_files:
        if not isinstance(raw_path, str) or not raw_path.strip():
            violations.add("path_safety_changed_file_invalid")
            continue
        posix_path = PurePosixPath(raw_path)
        if posix_path.is_absolute() or Path(raw_path).is_absolute():
            violations.add("path_safety_absolute_changed_file")
            continue
        if any(part == ".." for part in posix_path.parts):
            violations.add("path_safety_parent_changed_file")
            continue
        if "\\" in raw_path:
            violations.add("path_safety_backslash_changed_file")
            continue
        resolved = (root / Path(*posix_path.parts)).resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError:
            violations.add("path_safety_symlink_escape")
    return sorted(violations)


def non_negative_int_field(value: Any, violation: str, violations: list[str], default: int | None = None) -> int | None:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        violations.append(violation)
        return None
    return value


def manifest_contract_violations(manifest: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    if manifest.get("schema_version") != "zcode_result_manifest.v1":
        violations.append("schema_version")
    if not isinstance(manifest.get("task_id"), str) or not manifest.get("task_id"):
        violations.append("task_id")
    if not isinstance(manifest.get("allowed_files_only"), bool):
        violations.append("allowed_files_only_invalid")
    if manifest.get("untrusted_data_notice") is not True:
        violations.append("untrusted_data_notice")
    diffstat = manifest.get("diffstat")
    if not isinstance(diffstat, dict):
        violations.append("diffstat_invalid")
    else:
        for field in ("insertions", "deletions"):
            if field not in diffstat:
                violations.append(f"diffstat_{field}_invalid")
            else:
                non_negative_int_field(diffstat.get(field), f"diffstat_{field}_invalid", violations)
    validation = manifest.get("validation")
    if not isinstance(validation, dict):
        violations.append("validation_invalid")
    elif not isinstance(validation.get("summary"), str):
        violations.append("validation_summary_invalid")
    if not isinstance(manifest.get("artifact"), dict):
        violations.append("artifact_invalid")
    risk_flags = manifest.get("risk_flags")
    if not isinstance(risk_flags, list) or any(not isinstance(item, str) for item in risk_flags):
        violations.append("risk_flags_invalid")
    return violations


def build_zcode_manifest(args: argparse.Namespace) -> dict[str, Any]:
    payload = read_json(args.zcode_run_json) if args.zcode_run_json else {}
    audit = audit_payload(payload)
    changed_files = changed_files_from_payload(payload, args)
    validation = validation_summary(args, payload)
    files_changed = int(first_present(args.files_changed, audit.get("changed_count"), len(changed_files)))
    artifact_quality = first_present(audit.get("artifact_quality"), payload.get("artifact_quality"), "unknown")
    scope_safety = first_present(audit.get("scope_safety"), payload.get("scope_safety"), "unknown")
    repair_size = first_present(audit.get("codex_repair_size_recommendation"), payload.get("codex_repair_size"), "unknown")
    payload_allowed = allowed_files_only_from_payload(payload)
    allowed_files_only = args.allowed_files_only if args.allowed_files_only is not None else bool(payload_allowed)
    return {
        "schema_version": "zcode_result_manifest.v1",
        "task_id": args.task_id,
        "changed_files": changed_files,
        "allowed_files_only": allowed_files_only,
        "diffstat": {
            "insertions": args.insertions,
            "deletions": args.deletions,
            "files_changed": files_changed,
        },
        "validation": validation,
        "artifact": {
            "patch_sha256": manifest_hash(args.diff_path),
            "scope_safety": scope_safety if scope_safety in {"pass", "fail"} else "unknown",
            "artifact_quality": artifact_quality if artifact_quality in {"pass", "fail"} else "unknown",
            "codex_repair_size": str(repair_size).replace(" ", "_") if repair_size else "unknown",
            "raw_paths": {
                "zcode_run_json": str(args.zcode_run_json) if args.zcode_run_json else None,
                "diff_path": str(args.diff_path) if args.diff_path else None,
                "validation_log": str(args.validation_log) if args.validation_log else None,
            },
            "raw_hashes": {
                "zcode_run_json": manifest_hash(args.zcode_run_json),
                "diff_path": manifest_hash(args.diff_path),
                "validation_log": manifest_hash(args.validation_log),
            },
        },
        "risk_flags": risk_flags_from_payload(payload),
        "untrusted_data_notice": True,
    }


def manifest_violations(manifest: dict[str, Any], args: argparse.Namespace) -> list[str]:
    violations: list[str] = manifest_contract_violations(manifest)
    artifact = manifest.get("artifact") if isinstance(manifest.get("artifact"), dict) else {}
    validation = manifest.get("validation") if isinstance(manifest.get("validation"), dict) else {}
    diffstat = manifest.get("diffstat") if isinstance(manifest.get("diffstat"), dict) else {}
    violations.extend(changed_file_path_violations(manifest, args.workspace_root))
    if manifest.get("allowed_files_only") is not True:
        violations.append("allowed_files_only")
    if validation.get("result") != "pass":
        violations.append("validation_result")
    exit_code = validation.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int) or exit_code < 0:
        violations.append("validation_exit_code_invalid")
    elif exit_code != 0:
        violations.append("validation_exit_code")
    if artifact.get("scope_safety") != "pass":
        violations.append("scope_safety")
    if artifact.get("artifact_quality") != "pass":
        violations.append("artifact_quality")
    if artifact.get("codex_repair_size") not in ACCEPTABLE_REPAIR_SIZES:
        violations.append("codex_repair_size")
    if manifest.get("risk_flags"):
        violations.append("risk_flags")
    files_changed = non_negative_int_field(diffstat.get("files_changed"), "diffstat_files_changed_invalid", violations)
    changed_files = manifest.get("changed_files")
    changed_file_count = len(changed_files) if isinstance(changed_files, list) else None
    if files_changed is None or files_changed <= 0:
        violations.append("diffstat_no_changed_files")
    if files_changed is not None and files_changed > args.max_changed_files:
        violations.append("diffstat_budget")
    if changed_file_count is not None and changed_file_count > args.max_changed_files:
        violations.append("changed_files_budget")
    if files_changed is not None and changed_file_count is not None and files_changed != changed_file_count:
        violations.append("diffstat_changed_files_mismatch")
    validation_visible = non_negative_int_field(
        validation.get("model_visible_bytes"),
        "validation_visible_bytes_invalid",
        violations,
        default=0,
    )
    if validation_visible is not None and validation_visible > args.validation_visible_max_bytes:
        violations.append("validation_visible_budget")
    if args.secret_scan_result == "fail":
        violations.append("secret_scan_failure")
    elif args.secret_scan_result != "pass":
        violations.append("secret_scan_not_passed")
    if args.path_safety_violation:
        violations.append("path_safety_violation")
    if args.unexpected_changed_files:
        violations.append("unexpected_changed_files")
    if args.unexpected_untracked_files:
        violations.append("unexpected_untracked_files")
    if args.budget_violation:
        violations.append("visible_context_budget")
    return violations


def build_acceptance_payload(args: argparse.Namespace) -> dict[str, Any]:
    manifest = read_json(args.manifest)
    violations = manifest_violations(manifest, args)
    deterministic_accept = not violations
    green_enabled = args.green_path_non_llm
    acceptance_mode = "non_llm_green_path" if green_enabled and deterministic_accept else "codex_manifest_only"
    return {
        "schema_version": "codex_acceptance.v1",
        "task_id": manifest.get("task_id", "unknown"),
        "accepted": deterministic_accept,
        "acceptance_mode": acceptance_mode,
        "quality": {
            "allowed_files_only": manifest.get("allowed_files_only"),
            "validation_result": manifest.get("validation", {}).get("result"),
            "scope_safety": manifest.get("artifact", {}).get("scope_safety"),
            "artifact_quality": manifest.get("artifact", {}).get("artifact_quality"),
        },
        "risk_flags": manifest.get("risk_flags") or [],
        "repair_size": manifest.get("artifact", {}).get("codex_repair_size", "unknown"),
        "notes": "compact manifest only; raw logs referenced by path/hash",
        "violations": violations,
        "green_path_acceptance": {
            "enabled": green_enabled,
            "deterministic_accept": deterministic_accept,
            "codex_acceptance_skipped": bool(green_enabled and deterministic_accept),
            "shadow_codex_audit_enabled": args.shadow_codex_audit_enabled,
            "shadow_codex_audit_result": args.shadow_codex_audit_result,
            "missed_risk_flags": args.missed_risk_flag,
        },
    }


def command_build_manifest(args: argparse.Namespace) -> int:
    manifest = build_zcode_manifest(args)
    args.manifest_out.parent.mkdir(parents=True, exist_ok=True)
    args.manifest_out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


def command_accept_manifest(args: argparse.Namespace) -> int:
    payload = build_acceptance_payload(args)
    if args.acceptance_out:
        args.acceptance_out.parent.mkdir(parents=True, exist_ok=True)
        args.acceptance_out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["accepted"] else 1


def command_describe_arm(args: argparse.Namespace) -> int:
    payload = EXPERIMENT_ARMS if args.arm == "all" else {args.arm: EXPERIMENT_ARMS[args.arm]}
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def command_build_harness_packet(args: argparse.Namespace) -> int:
    task = read_json(args.task_definition)
    missing = [field for field in ("objective", "allowed_files", "validation_commands") if field not in task]
    packet = {
        "schema_version": "zcode_direct_harness_packet.v1",
        "scaffolded": True,
        "codex_planning_required": bool(missing),
        "anomaly_audit_required": bool(missing),
        "missing_required_fields": missing,
        "objective": task.get("objective"),
        "allowed_files": task.get("allowed_files", []),
        "expected_outputs": task.get("expected_outputs", []),
        "validation_commands": task.get("validation_commands", []),
        "acceptance_criteria": task.get("acceptance_criteria", []),
        "risk_flags": ["missing_required_fields"] if missing else [],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(packet, indent=2, sort_keys=True))
    return 0 if not missing else 1


def group_by(records: list[dict[str, Any]], keys: tuple[str, ...]) -> dict[tuple[Any, ...], list[dict[str, Any]]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(tuple(record.get(key, "unknown") for key in keys), []).append(record)
    return grouped


def usage_missing_count(records: list[dict[str, Any]]) -> int:
    return sum(1 for item in records if item.get("usage", {}).get("usage_missing") is True)


def summary_metric_value(records: list[dict[str, Any]]) -> int | str:
    if not records:
        return 0
    if usage_missing_count(records):
        return "unavailable"
    return sum_usage(records)[PRIMARY_METRIC]


def build_token_reduction_summary(usage_records: list[dict[str, Any]], context_records: list[dict[str, Any]], experiment_id: str) -> str:
    metric = token_metric_metadata()
    clean_records = [
        item for item in usage_records
        if item.get("attempt_valid_for_benchmark") is not False and item.get("invalid_measurement") is not True and item.get("exclusion_reason") in (None, "")
    ]
    excluded_records = [item for item in usage_records if item not in clean_records]
    lines = [
        "# ZCode Token Reduction Summary",
        "",
        "## Executive Summary",
        "",
        f"- experiment_id: `{experiment_id}`",
        f"- primary_metric: `{metric['primary_metric']}`",
        f"- primary_metric_formula: `{metric['primary_metric_formula']}`",
        f"- usage_records: `{len(usage_records)}`",
        f"- context_intake_records: `{len(context_records)}`",
        "",
        "## Clean Benchmark Vs Operator Total",
        "",
    ]
    lines.extend(markdown_table(
        ["bucket", "attempts", "effective_codex_work", "usage_missing"],
        [
            ["benchmark_clean_usage", len(clean_records), summary_metric_value(clean_records), usage_missing_count(clean_records)],
            ["operator_total_usage", len(usage_records), summary_metric_value(usage_records), usage_missing_count(usage_records)],
            ["excluded_attempt_usage", len(excluded_records), summary_metric_value(excluded_records), usage_missing_count(excluded_records)],
        ],
    ))
    lines.extend(["", "## Task / Mode / Phase Tokens", ""])
    token_rows = []
    for key, group in sorted(group_by(usage_records, ("task_id", "mode", "phase")).items()):
        token_rows.append([key[0], key[1], key[2], len(group), summary_metric_value(group)])
    lines.extend(markdown_table(["task", "mode", "phase", "attempts", "effective_codex_work"], token_rows))
    lines.extend(["", "## Context-Intake Bytes", ""])
    context_rows = []
    for record in context_records:
        tool = record.get("tool_context_intake", {})
        command_visible = int(tool.get("command_stdout_bytes_visible_to_model") or 0) + int(tool.get("command_stderr_bytes_visible_to_model") or 0)
        context_rows.append([
            record.get("task_id"),
            record.get("canonical_mode"),
            record.get("phase"),
            record.get("prompt", {}).get("prompt_bytes", 0),
            record.get("stdin", {}).get("stdin_bytes", 0),
            record.get("diff", {}).get("model_visible_bytes", 0),
            record.get("validation", {}).get("model_visible_bytes", 0),
            command_visible,
        ])
    lines.extend(markdown_table(["task", "mode", "phase", "prompt", "stdin", "diff_visible", "validation_visible", "command_visible"], context_rows))
    lines.extend(["", "## Quality Gates", ""])
    quality_rows = []
    for key, group in sorted(group_by(usage_records, ("mode",)).items()):
        quality_rows.append([key[0], len(group), sum(1 for item in group if item.get("quality", {}).get("validation_result") == "pass"), sum(1 for item in group if item.get("quality", {}).get("scope_safety") == "pass")])
    lines.extend(markdown_table(["mode", "attempts", "validation_pass", "scope_pass"], quality_rows))
    routing_rows = [
        [
            item.get("task_id"),
            item.get("routing_decision", {}).get("selected"),
            ",".join(item.get("routing_decision", {}).get("reason_codes") or []),
            item.get("routing_decision", {}).get("fallback"),
            item.get("routing_decision", {}).get("background_safe"),
        ]
        for item in usage_records
        if isinstance(item.get("routing_decision"), dict)
    ]
    lines.extend([
        "",
        "## Repair Burden",
        "",
        "`codex_repair_size` is aggregated in the usage ledger; missing values are reported as unavailable.",
        "",
        "## Failures / Retries / Unavailable Usage",
        "",
        f"- failed_attempts: `{sum(1 for item in usage_records if item.get('failure', {}).get('failed') is True)}`",
        f"- unavailable_usage_attempts: `{sum(1 for item in usage_records if item.get('usage', {}).get('usage_missing') is True)}`",
        f"- rerun_attempts: `{sum(1 for item in usage_records if item.get('rerun_reason'))}`",
        "",
        "## Wall-Clock Breakdown",
        "",
        "`unavailable` unless duration metadata is recorded on usage rows.",
        "",
        "## User Impact",
        "",
        f"- ask_user_count: `{sum(int(item.get('user_impact', {}).get('ask_user_count') or 0) for item in usage_records)}`",
        f"- manual_interventions: `{sum(1 for item in usage_records if item.get('user_impact', {}).get('manual_intervention_required') is True)}`",
        "",
        "## Routing Decisions",
        "",
    ])
    if routing_rows:
        lines.extend(markdown_table(["task", "selected", "reason_codes", "fallback", "background_safe"], routing_rows))
    else:
        lines.append("`unavailable` until routing_decision records are attached.")
    lines.extend(["", "## Next Optimization Target", ""])
    measured_groups = [
        (key, group, sum_usage(group)[PRIMARY_METRIC])
        for key, group in group_by(usage_records, ("task_id", "mode")).items()
        if group and usage_missing_count(group) == 0
    ]
    if measured_groups:
        largest = max(measured_groups, key=lambda item: item[2])
        lines.append(f"- `{largest[0][0]}/{largest[0][1]}` has the largest recorded `{PRIMARY_METRIC}`: `{largest[2]}`.")
    else:
        lines.append("- `unavailable`: no fully measured usage rows were present in the selected ledgers.")
    return "\n".join(lines) + "\n"


def command_summarize_token_reduction(args: argparse.Namespace) -> int:
    usage_records = load_codex_usage_records(args.usage_ledger)
    context_records = load_context_intake_records(args.context_ledger)
    markdown = build_token_reduction_summary(usage_records, context_records, args.experiment_id)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(markdown, encoding="utf-8")
    print(str(args.out))
    return 0


def add_token_reduction_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    arm = subparsers.add_parser("describe-token-reduction-arm", help="Print feature flags for a token-reduction experiment arm.")
    arm.add_argument("--arm", choices=("all", *EXPERIMENT_ARMS.keys()), default="all")
    arm.set_defaults(func=command_describe_arm)

    manifest = subparsers.add_parser("build-zcode-result-manifest", help="Build compact zcode_result_manifest.json for manifest-only acceptance.")
    manifest.add_argument("--task-id", required=True)
    manifest.add_argument("--zcode-run-json", type=Path)
    manifest.add_argument("--manifest-out", type=Path, required=True)
    manifest.add_argument("--validation-log", type=Path)
    manifest.add_argument("--diff-path", type=Path)
    manifest.add_argument("--changed-file", action="append", default=[])
    manifest.add_argument("--allowed-files-only", action=argparse.BooleanOptionalAction, default=None)
    manifest.add_argument("--insertions", type=int, default=0)
    manifest.add_argument("--deletions", type=int, default=0)
    manifest.add_argument("--files-changed", type=int)
    manifest.add_argument("--pass-validation-visible-max-bytes", type=int, default=1024)
    manifest.add_argument("--failure-validation-excerpt-max-bytes", type=int, default=4096)
    manifest.set_defaults(func=command_build_manifest)

    accept = subparsers.add_parser("accept-zcode-manifest", help="Accept a compact ZCode result manifest.")
    accept.add_argument("--manifest", type=Path, required=True)
    accept.add_argument("--acceptance-out", type=Path)
    accept.add_argument("--green-path-non-llm", action="store_true")
    accept.add_argument("--shadow-codex-audit-enabled", action="store_true")
    accept.add_argument("--shadow-codex-audit-result", choices=("agree", "disagree", "unavailable"), default="unavailable")
    accept.add_argument("--missed-risk-flag", action="append", default=[])
    accept.add_argument("--max-changed-files", type=int, default=10)
    accept.add_argument("--validation-visible-max-bytes", type=int, default=1024)
    accept.add_argument("--secret-scan-result", choices=("pass", "fail", "skipped", "unknown"), default="unknown")
    accept.add_argument("--workspace-root", type=Path, default=Path("."))
    accept.add_argument("--path-safety-violation", action="store_true")
    accept.add_argument("--unexpected-changed-files", action="store_true")
    accept.add_argument("--unexpected-untracked-files", action="store_true")
    accept.add_argument("--budget-violation", action="store_true")
    accept.set_defaults(func=command_accept_manifest)

    harness = subparsers.add_parser("build-zcode-direct-harness-packet", help="Scaffold a deterministic ZCode packet from a benchmark task definition.")
    harness.add_argument("--task-definition", type=Path, required=True)
    harness.add_argument("--out", type=Path, required=True)
    harness.set_defaults(func=command_build_harness_packet)

    summary = subparsers.add_parser("summarize-token-reduction", help="Generate non-LLM benchmark summary from usage and context ledgers.")
    summary.add_argument("--usage-ledger", type=Path, default=DEFAULT_CODEX_LEDGER)
    summary.add_argument("--context-ledger", type=Path, default=DEFAULT_CONTEXT_LEDGER)
    summary.add_argument("--experiment-id", default="unknown")
    summary.add_argument("--out", type=Path, default=DEFAULT_SUMMARY_PATH)
    summary.set_defaults(func=command_summarize_token_reduction)
