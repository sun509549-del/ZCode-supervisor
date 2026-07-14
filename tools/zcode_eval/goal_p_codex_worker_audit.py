"""Self-audit normalization for Goal P Codex worker rows."""

from __future__ import annotations

import copy
import subprocess
from pathlib import Path
from typing import Any


def requirement_status(value: Any) -> Any:
    text = str(value or "").strip().lower()
    if text in {"pass", "passed", "satisfied", "satisfy", "covered"}:
        return "satisfied"
    if text in {"fail", "failed", "not_satisfied", "not satisfied"}:
        return "not_satisfied"
    return value


def edge_status(value: Any) -> Any:
    text = str(value or "").strip().lower()
    if text in {"pass", "passed", "satisfied", "covered"}:
        return "covered"
    if text in {"fail", "failed", "not_satisfied", "not covered", "not_covered"}:
        return "not_covered"
    return value


def validation_result(value: Any) -> Any:
    text = str(value or "").strip().lower()
    if text in {"pass", "passed", "success", "succeeded"}:
        return "pass"
    if text in {"fail", "failed", "failure"}:
        return "fail"
    return value


def validation_summary(item: dict[str, Any]) -> str:
    summary = item.get("summary")
    if isinstance(summary, str) and summary:
        return summary
    evidence = item.get("evidence")
    if isinstance(evidence, list):
        for entry in evidence:
            if isinstance(entry, dict) and isinstance(entry.get("summary"), str):
                return entry["summary"]
    command = item.get("command")
    if isinstance(command, str) and command:
        return f"{command} {validation_result(item.get('result'))}"
    return "validation result recorded"


def normalize_validation(value: Any) -> dict[str, Any]:
    item = value[0] if isinstance(value, list) and value else value
    if not isinstance(item, dict):
        return {"result": "unknown", "summary": "validation result missing"}
    result = validation_result(item.get("result"))
    normalized = {
        "result": result,
        "summary": validation_summary(item),
    }
    if isinstance(item.get("manifest_ref"), str):
        normalized["manifest_ref"] = item["manifest_ref"]
    return normalized


def normalize_trace_statuses(items: Any, *, edge: bool = False) -> Any:
    if not isinstance(items, list):
        return items
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            normalized.append(item)
            continue
        clone = dict(item)
        clone["status"] = edge_status(clone.get("status")) if edge else requirement_status(clone.get("status"))
        normalized.append(clone)
    return normalized


def normalize_self_audit(audit: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(audit)
    normalized["requirements"] = normalize_trace_statuses(normalized.get("requirements"))
    normalized["edge_cases"] = normalize_trace_statuses(normalized.get("edge_cases"), edge=True)
    normalized["validation"] = normalize_validation(normalized.get("validation"))
    return normalized


def git_status_snapshot(repo: Path) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=repo,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        return {"ok": False, "lines": [], "error": type(exc).__name__}
    return {
        "ok": completed.returncode == 0,
        "lines": completed.stdout.splitlines(),
        "error": completed.stderr.strip() if completed.returncode else None,
    }


def workspace_isolation_result(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    changed = before.get("lines") != after.get("lines")
    available = before.get("ok") is True and after.get("ok") is True
    return {
        "workspace_isolation_ok": available and not changed,
        "workspace_isolation_before": before,
        "workspace_isolation_after": after,
        "workspace_isolation_violations": [] if available and not changed else ["main_repo_status_changed"],
    }
