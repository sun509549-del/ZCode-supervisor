"""Goal P Codex-worker same-packet comparison runner and report builder."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

try:
    from .goal_p_codex_worker_audit import (
        git_status_snapshot,
        normalize_self_audit,
        workspace_isolation_result,
    )
    from .goal_p_codex_worker_report import (
        CLAIM_FAMILY,
        OUTCOME_EVIDENCE_MISSING,
        OUTCOME_MEASURED,
        OUTCOME_USAGE_UNAVAILABLE,
        ROUTE_USED,
        SCHEMA_VERSION,
        build_report,
        positive_int,
        read_json_object,
        runner_unavailable_report,
        write_json,
        write_markdown_report,
    )
    from .strict_contract import build_acceptance_result_for_payload
    from .strict_contract_comparison import (
        changed_files,
        copy_fixture,
        line_delta,
        run_codex,
        run_validation,
        usage_from_events,
    )
    from .strict_contract_breakdown import ensure_codex_output_schema, unavailable_usage_metrics
except ImportError:  # pragma: no cover - direct script execution
    from goal_p_codex_worker_audit import (
        git_status_snapshot,
        normalize_self_audit,
        workspace_isolation_result,
    )
    from goal_p_codex_worker_report import (
        CLAIM_FAMILY,
        OUTCOME_EVIDENCE_MISSING,
        OUTCOME_MEASURED,
        OUTCOME_USAGE_UNAVAILABLE,
        ROUTE_USED,
        SCHEMA_VERSION,
        build_report,
        positive_int,
        read_json_object,
        runner_unavailable_report,
        write_json,
        write_markdown_report,
    )
    from strict_contract import build_acceptance_result_for_payload
    from strict_contract_comparison import (
        changed_files,
        copy_fixture,
        line_delta,
        run_codex,
        run_validation,
        usage_from_events,
    )
    from strict_contract_breakdown import ensure_codex_output_schema, unavailable_usage_metrics


DEFAULT_COMPARISON_ROOT = Path(".local/goal-p-codex-worker-comparison")
CODEX_WORKER_SANDBOX = "workspace-write"
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
FIXTURE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_task_id(value: Any) -> str:
    if not isinstance(value, str) or not TASK_ID_RE.fullmatch(value):
        raise ValueError(f"unsafe task_id: {value!r}")
    return value


def safe_fixture_name(value: Any) -> str:
    if not isinstance(value, str) or not FIXTURE_RE.fullmatch(value):
        raise ValueError(f"unsafe fixture: {value!r}")
    return value


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def codex_worker_writable_dirs(task_dir: Path) -> list[Path]:
    return [task_dir]


def worker_audit_output_path(task_dir: Path) -> Path:
    return task_dir / f"zcode_self_audit-{uuid.uuid4().hex}.json"


def prepare_worker_audit_dir(workspace: Path) -> Path:
    audit_dir = workspace / ".codex" / "zcode" / "runs"
    audit_dir.mkdir(parents=True, exist_ok=True)
    return audit_dir


def evidence_path(evidence_root: Path, relative: str | None) -> Path | None:
    if not relative:
        return None
    root = evidence_root.resolve()
    rel = Path(relative)
    marker = Path(".local/zcode-20-resumable")
    try:
        rel = rel.relative_to(marker)
    except ValueError:
        pass
    candidate = rel.resolve() if rel.is_absolute() else (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"evidence path escapes evidence root: {relative!r}") from exc
    return candidate


def load_zcode_task_packets(evidence_root: Path, min_task_count: int) -> list[dict[str, Any]]:
    state_path = evidence_root / "state.json"
    if not state_path.exists():
        raise FileNotFoundError(f"missing state.json: {state_path}")
    state = read_json_object(state_path)
    tasks = state.get("tasks")
    if not isinstance(tasks, list) or len(tasks) < min_task_count:
        raise ValueError(f"expected at least {min_task_count} tasks in {state_path}")
    rows: list[dict[str, Any]] = []
    for task in tasks[:min_task_count]:
        if not isinstance(task, dict):
            raise ValueError("state task must be an object")
        task_id = safe_task_id(task.get("task_id"))
        packet_path = evidence_path(evidence_root, task.get("packet_path"))
        if packet_path is None or not packet_path.exists():
            raise FileNotFoundError(f"missing packet for {task_id}: {packet_path}")
        packet = read_json_object(packet_path)
        rows.append({"task": task, "packet": packet, "packet_path": packet_path})
    return rows


def clean_workspace(repo: Path, task: dict[str, Any], comparison_root: Path) -> tuple[Path, Path]:
    fixture = safe_fixture_name(task.get("fixture"))
    fixture_root = (repo / "benchmarks" / "hard-token-fixtures").resolve()
    source = (fixture_root / fixture).resolve()
    try:
        source.relative_to(fixture_root)
    except ValueError as exc:
        raise ValueError(f"fixture escapes fixture root: {fixture!r}") from exc
    if not source.is_dir():
        raise FileNotFoundError(f"missing clean fixture: {source}")
    task_id = safe_task_id(task.get("task_id"))
    workspace = comparison_root / "work" / "tasks" / task_id
    base = comparison_root / "work" / "bases" / task_id
    copy_fixture(source, workspace)
    copy_fixture(source, base)
    prepare_worker_audit_dir(workspace)
    return workspace, base


def packet_task_contract(packet: dict[str, Any]) -> dict[str, Any]:
    strict = packet.get("strict_contract")
    if not isinstance(strict, dict):
        raise ValueError("packet strict_contract must be an object")
    contract = strict.get("task_contract")
    if not isinstance(contract, dict):
        raise ValueError("packet strict_contract.task_contract must be an object")
    return copy.deepcopy(contract)


def packet_validation_command(packet: dict[str, Any], task: dict[str, Any]) -> str:
    commands = packet.get("validation_commands")
    if isinstance(commands, list) and commands:
        command = commands[0]
        if isinstance(command, str) and command.strip():
            return command
    fallback = task.get("validation")
    if isinstance(fallback, str) and fallback.strip():
        return fallback
    return "npm test"


def synthesized_packet_prompt(task: dict[str, Any], packet: dict[str, Any]) -> str:
    allowed = ", ".join(packet.get("allowed_files") or [task.get("allowed_file")])
    validation = ", ".join(packet.get("validation_commands") or [packet_validation_command(packet, task)])
    return f"""Task ID: {task["task_id"]}
Source packet objective: {packet.get("objective") or task.get("objective")}
Allowed files: {allowed}
Validation command: {validation}
Max changed files: {packet.get("max_changed_files", 1)}

Acceptance criteria:
{json.dumps(packet.get("acceptance_criteria") or [], indent=2, sort_keys=True)}
"""


def rebound_packet_prompt(task: dict[str, Any], packet: dict[str, Any], workspace: Path | None) -> str:
    prompt = packet.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        prompt = synthesized_packet_prompt(task, packet)
    packet_workspace = packet.get("workspace")
    if workspace is not None and isinstance(packet_workspace, str) and packet_workspace:
        prompt = prompt.replace(packet_workspace, str(workspace))
    return prompt.rstrip()


def worker_prompt(
    task: dict[str, Any],
    packet: dict[str, Any],
    *,
    audit_output: Path | None = None,
    workspace: Path | None = None,
) -> str:
    contract = packet_task_contract(packet)
    audit_target = audit_output or Path(".codex/zcode/runs/zcode_self_audit.json")
    workspace_text = str(workspace) if workspace is not None else "the current working directory"
    base_prompt = rebound_packet_prompt(task, packet, workspace)
    return f"""You are the Codex-as-worker baseline for an existing 20-row task packet.
Do not run ZCode, the ZCode CLI, the ZCode app, or any delegation tool.
Work only in the current workspace. The original packet workspace is rebound to {workspace_text}.

Original packet prompt with workspace rebinding:
{base_prompt}

Codex-worker audit addendum:
- The original packet prompt above is authoritative.
- The following addendum only sets the worker route, isolated workspace, and strict audit sidecar requirements for this baseline.
- The task_contract JSON below is authoritative.
- The .codex/zcode/runs directory already exists.
- Write the required zcode_self_audit.v1 JSON to this audit path: {audit_target}
- Do this after the code edit and before final response.
- Use exact root keys: schema_version, contract_id, task_id, overall_status, requirements, edge_cases, validation, deviations_from_plan, unresolved_questions, risk_flags, blocked_reasons.
- Use exact requirement statuses: satisfied, not_satisfied, blocked, not_applicable, unknown.
- Use exact edge case statuses: covered, not_covered, blocked, unknown.
- Use validation as one object, not a list: {{"result":"pass|fail|skipped|unknown","summary":"..."}}.
- Do not return pass unless every blocking requirement has evidence.
- Evidence entries must include type, ref, and summary.
- Use validation_result, changed_file, and diffstat evidence where required.
- If validation passes, set overall_status to pass, every satisfied requirement status to satisfied, covered edge case status to covered, and validation.result to pass.
- If validation cannot run, set validation.result to skipped or unknown and explain it.

task_contract JSON:
{json.dumps(contract, indent=2, sort_keys=True)}

Rules:
- Read the minimum files needed.
- Edit only the allowed file.
- Do not edit tests, README, package metadata, hidden validation files, or unrelated files.
- Run the validation command as a black-box check.
- Prefer the smallest fix that satisfies the objective.
"""


def diff_totals(diffs: list[dict[str, Any]]) -> tuple[int, int]:
    insertions = sum(int(row.get("added") or 0) for row in diffs)
    deletions = sum(int(row.get("deleted") or 0) for row in diffs)
    return insertions, deletions


def missing_acceptance(reason: str, task_id: str, contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "strict_contract_acceptance_result.v1",
        "accepted": False,
        "violations": [reason],
        "trace_coverage": {
            "schema_version": "trace_coverage.v1",
            "task_id": task_id,
            "contract_id": contract.get("contract_id"),
            "violations": [reason],
        },
        "codex_acceptance_audit": {
            "schema_version": "codex_acceptance_audit.v1",
            "task_id": task_id,
            "contract_id": str(contract.get("contract_id")),
            "mode": "manifest_only",
            "accepted": False,
            "risk_flags": [reason],
        },
    }


def strict_acceptance(
    *,
    contract: dict[str, Any],
    self_audit: Path,
    normalized_audit: Path,
    changed: list[str],
    diffs: list[dict[str, Any]],
    validation_rc: int,
) -> tuple[int, dict[str, Any]]:
    task_id = str(contract.get("task_id") or "")
    if not self_audit.exists():
        return 1, missing_acceptance("self_audit_missing", task_id, contract)
    try:
        audit = normalize_self_audit(read_json_object(self_audit))
        write_json(normalized_audit, audit)
        insertions, deletions = diff_totals(diffs)
        args = argparse.Namespace(
            changed_file=changed,
            validation_result="pass" if validation_rc == 0 else "fail",
            validation_exit_code=validation_rc,
            files_changed=len(changed),
            insertions=insertions,
            deletions=deletions,
            mode="manifest_only",
            codex_repair_size="none",
            full_diff_read=True,
            full_log_read=True,
            green_path_non_llm=False,
            shadow_codex_audit_enabled=False,
            shadow_codex_audit_result="unavailable",
            missed_risk_flag=[],
            experiment_id="goal-p-codex-worker-comparison",
        )
        result = build_acceptance_result_for_payload(args, contract, audit)
    except Exception as exc:  # strict gate failure is evidence, not a crash.
        return 1, missing_acceptance(f"strict_acceptance_error:{type(exc).__name__}", task_id, contract)
    return (0 if result.get("accepted") is True else 1), result


def measured_codex_usage(usage: dict[str, Any] | None) -> tuple[str, int | None, dict[str, Any]]:
    if not usage or usage.get("usage_missing") is True:
        return "unavailable", None, unavailable_usage_metrics("codex_exec_phase_usage_missing")
    tokens = positive_int(usage.get("effective_codex_work"))
    if tokens is None:
        return "unavailable", None, {**usage, "no_usage_reason": "codex_worker_zero_usage_invalid"}
    return "measured", tokens, usage


def worker_row_payload(
    *,
    task: dict[str, Any],
    packet_path: Path,
    packet_sha: str,
    task_dir: Path,
    workspace: Path,
    result: Any,
    exec_row: dict[str, Any],
    validation_rc: int,
    acceptance_rc: int,
    acceptance: dict[str, Any],
    changed: list[str],
    diffs: list[dict[str, Any]],
    isolation: dict[str, Any],
) -> dict[str, Any]:
    usage_status, tokens, usage = measured_codex_usage(usage_from_events(task_dir / "codex-events.jsonl"))
    scope_ok = changed == [task.get("allowed_file")]
    strict_accepted = acceptance.get("accepted") is True
    quality = all([
        result.rc == 0, validation_rc == 0, acceptance_rc == 0,
        scope_ok, strict_accepted, isolation["workspace_isolation_ok"],
    ])
    return {
        "task_id": str(task["task_id"]),
        "fixture": task.get("fixture"),
        "route_used": ROUTE_USED,
        "claim_family": CLAIM_FAMILY,
        "quality": "pass" if quality else "fail",
        "strict_accepted": strict_accepted,
        "codex_rc": result.rc,
        "timed_out": result.timed_out,
        "duration_seconds": result.seconds,
        "final_validation_rc": validation_rc,
        "acceptance_rc": acceptance_rc,
        "changed_files": changed,
        "allowed_file": task.get("allowed_file"),
        "scope_ok": scope_ok,
        "diff": diffs,
        "codex_worker_usage_status": usage_status,
        "codex_worker_total_tokens": tokens,
        "codex_worker_usage": usage,
        "codex_exec_rows": [exec_row],
        "source_packet_path": str(packet_path),
        "source_packet_sha256": packet_sha,
        "workspace": str(workspace),
        "strict_violations": acceptance.get("violations", []),
        **isolation,
    }


def write_packet_metadata(task_dir: Path, packet_path: Path, packet_sha: str, task_id: str) -> None:
    write_json(
        task_dir / "source-packet-metadata.json",
        {
            "source_packet_path": str(packet_path),
            "source_packet_sha256": packet_sha,
            "source_task_id": task_id,
        },
    )


def write_initial_task_artifacts(
    *,
    task_dir: Path,
    packet: dict[str, Any],
    packet_path: Path,
    task_id: str,
) -> tuple[dict[str, Any], str]:
    task_dir.mkdir(parents=True, exist_ok=True)
    contract = packet_task_contract(packet)
    packet_sha = sha256_file(packet_path)
    write_packet_metadata(task_dir, packet_path, packet_sha, task_id)
    write_json(task_dir / "task_contract.json", contract)
    return contract, packet_sha


def evaluate_workspace(
    *,
    workspace: Path,
    base: Path,
    task: dict[str, Any],
    packet: dict[str, Any],
    task_dir: Path,
    contract: dict[str, Any],
    sidecar_audit: Path,
) -> tuple[int, list[str], list[dict[str, Any]], int, dict[str, Any]]:
    validation = run_validation(workspace, packet_validation_command(packet, task), task_dir / "final-validation.log")
    changed = changed_files(base, workspace)
    diffs = [line_delta(base, workspace, rel) for rel in changed]
    self_audit = workspace / ".codex" / "zcode" / "runs" / "zcode_self_audit.json"
    audit_path = self_audit if self_audit.exists() else sidecar_audit
    acceptance_rc, acceptance = strict_acceptance(
        contract=contract,
        self_audit=audit_path,
        normalized_audit=task_dir / "normalized-self-audit.json",
        changed=changed,
        diffs=diffs,
        validation_rc=validation.rc,
    )
    write_json(task_dir / "strict-acceptance.json", acceptance)
    return validation.rc, changed, diffs, acceptance_rc, acceptance


def run_worker_row(
    *,
    repo: Path,
    comparison_root: Path,
    row: dict[str, Any],
    schema: Path,
    worker_timeout: int,
) -> dict[str, Any]:
    task = row["task"]
    packet = row["packet"]
    task_id = safe_task_id(task.get("task_id"))
    task_dir = comparison_root / "tasks" / task_id
    workspace, base = clean_workspace(repo, task, comparison_root)
    contract, packet_sha = write_initial_task_artifacts(
        task_dir=task_dir, packet=packet, packet_path=row["packet_path"], task_id=task_id
    )
    audit_output = worker_audit_output_path(task_dir)
    repo_status_before = git_status_snapshot(repo)
    result, exec_row = run_codex(
        worker_prompt(task, packet, audit_output=audit_output, workspace=workspace), workspace, task_dir,
        task=task_id, arm=ROUTE_USED, phase="implementation",
        component="codex_worker_implementation", schema=schema,
        timeout=worker_timeout,
        sandbox=CODEX_WORKER_SANDBOX,
        writable_dirs=codex_worker_writable_dirs(task_dir),
    )
    repo_status_after = git_status_snapshot(repo)
    isolation = workspace_isolation_result(repo_status_before, repo_status_after)
    validation_rc, changed, diffs, acceptance_rc, acceptance = evaluate_workspace(
        workspace=workspace,
        base=base,
        task=task,
        packet=packet,
        task_dir=task_dir,
        contract=contract,
        sidecar_audit=audit_output,
    )
    output = worker_row_payload(
        task=task,
        packet_path=row["packet_path"],
        packet_sha=packet_sha,
        task_dir=task_dir,
        workspace=workspace,
        result=result,
        exec_row=exec_row,
        validation_rc=validation_rc,
        acceptance_rc=acceptance_rc,
        acceptance=acceptance,
        changed=changed,
        diffs=diffs,
        isolation=isolation,
    )
    write_json(task_dir / "row.json", output)
    return output


def run_comparison(args: argparse.Namespace) -> dict[str, Any]:
    repo = repo_root()
    if shutil.which("codex") is None:
        return runner_unavailable_report(args, "codex_cli_unavailable")
    comparison_root = args.comparison_root.resolve()
    comparison_root.mkdir(parents=True, exist_ok=True)
    tasks = load_zcode_task_packets(args.zcode_evidence_root, args.task_count)
    schema = ensure_codex_output_schema(comparison_root)
    rows: list[dict[str, Any]] = []
    for task_packet in tasks:
        rows.append(
            run_worker_row(
                repo=repo,
                comparison_root=comparison_root,
                row=task_packet,
                schema=schema,
                worker_timeout=args.worker_timeout,
            )
        )
        report = build_report(
            comparison_root=comparison_root,
            evidence_root=args.zcode_evidence_root,
            archive_root=args.archive_root,
            rows=rows,
            min_task_count=args.task_count,
            repair_attempted=args.repair_attempted,
        )
        write_json(comparison_root / "final-outcome.json", report)
    report = build_report(
        comparison_root=comparison_root,
        evidence_root=args.zcode_evidence_root,
        archive_root=args.archive_root,
        rows=rows,
        min_task_count=args.task_count,
        repair_attempted=args.repair_attempted,
    )
    write_json(comparison_root / "final-outcome.json", report)
    write_markdown_report(comparison_root / "comparison-report.md", report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zcode-evidence-root", type=Path, required=True)
    parser.add_argument("--archive-root", type=Path)
    parser.add_argument("--comparison-root", type=Path, default=DEFAULT_COMPARISON_ROOT)
    parser.add_argument("--task-count", type=int, default=20)
    parser.add_argument("--worker-timeout", type=int, default=1800)
    parser.add_argument("--repair-attempted", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = run_comparison(args)
    except FileNotFoundError as exc:
        report = {
            "schema_version": SCHEMA_VERSION,
            "final_outcome": OUTCOME_EVIDENCE_MISSING,
            "route_used": ROUTE_USED,
            "claim_family": CLAIM_FAMILY,
            "task_count": 0,
            "codex_worker_rows": 0,
            "codex_worker_usage_status": "unavailable",
            "codex_worker_total_tokens": None,
            "total_workflow_savings_claimable": False,
            "blocker": str(exc),
            "rows": [],
        }
        write_json(args.comparison_root / "final-outcome.json", report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["final_outcome"] in {OUTCOME_MEASURED, OUTCOME_USAGE_UNAVAILABLE} else 1


if __name__ == "__main__":
    raise SystemExit(main())
