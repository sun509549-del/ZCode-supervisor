#!/usr/bin/env python3
"""Check Goal P Codex worker comparison evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.zcode_eval.goal_p_codex_worker_report import (
    CLAIM_FAMILY,
    OUTCOME_EVIDENCE_MISSING,
    OUTCOME_MEASURED,
    OUTCOME_RUNNER_UNAVAILABLE,
    OUTCOME_STRICT_UNAVAILABLE_AFTER_REPAIR,
    OUTCOME_USAGE_UNAVAILABLE,
    OUTCOME_USAGE_UNAVAILABLE_AFTER_REPAIR,
    ROUTE_USED,
    ZCODE_CLAIM_FAMILY,
    zcode_summary,
)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zcode-evidence-root", type=Path, required=True)
    parser.add_argument("--archive-root", type=Path)
    parser.add_argument("--comparison-root", type=Path, required=True)
    parser.add_argument("--expect-min-task-count", type=int, default=20)
    return parser


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report_path = args.comparison_root / "final-outcome.json"
    require(report_path.exists(), f"missing comparison report: {report_path}")
    report = read_json(report_path)
    allowed_outcomes = {
        OUTCOME_MEASURED,
        OUTCOME_USAGE_UNAVAILABLE,
        OUTCOME_USAGE_UNAVAILABLE_AFTER_REPAIR,
        OUTCOME_STRICT_UNAVAILABLE_AFTER_REPAIR,
        OUTCOME_RUNNER_UNAVAILABLE,
        OUTCOME_EVIDENCE_MISSING,
    }
    require(report.get("final_outcome") in allowed_outcomes, "unexpected final_outcome")
    require(report.get("route_used") == ROUTE_USED, "route_used mismatch")
    require(report.get("claim_family") == CLAIM_FAMILY, "claim_family mismatch")
    if report.get("final_outcome") == OUTCOME_EVIDENCE_MISSING:
        require(report.get("task_count") == 0, "evidence-missing terminal must have zero tasks")
        require(report.get("codex_worker_rows", 0) == 0, "evidence-missing terminal must have zero rows")
        require(report.get("total_workflow_savings_claimable") is False, "workflow savings must remain unclaimable")
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    zcode = zcode_summary(args.zcode_evidence_root)
    if report.get("final_outcome") == OUTCOME_RUNNER_UNAVAILABLE:
        require(report.get("zcode_claim_family") == ZCODE_CLAIM_FAMILY, "zcode claim_family mismatch")
        require(report.get("zcode_worker_total_tokens") == zcode["zcode_worker_total_tokens"], "zcode token total changed")
        require(report.get("zcode_strict_green_count") == 18, "zcode strict-green count is not preserved")
        require(report.get("task_count") == 0, "runner-unavailable terminal must have zero tasks")
        require(report.get("codex_worker_rows", 0) == 0, "runner-unavailable terminal must have zero rows")
        require(report.get("codex_worker_usage_status") == "unavailable", "runner-unavailable usage status invalid")
        require(report.get("codex_worker_total_tokens") is None, "runner-unavailable tokens must remain null")
        require(report.get("total_workflow_savings_claimable") is False, "workflow savings must remain unclaimable")
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    require(report.get("zcode_claim_family") == ZCODE_CLAIM_FAMILY, "zcode claim_family mismatch")
    require(report.get("task_count", 0) >= args.expect_min_task_count, "task_count below required minimum")
    require(report.get("codex_worker_rows", 0) >= args.expect_min_task_count, "codex_worker_rows below required minimum")
    require(isinstance(report.get("strict_green_count"), int), "strict_green_count missing")
    require(report.get("final_validation_rc") is not None, "final_validation_rc missing")
    require(report.get("acceptance_rc") is not None, "acceptance_rc missing")
    require(report.get("codex_worker_usage_status") in {"measured", "partial", "unavailable"}, "usage status invalid")
    require(report.get("zcode_worker_total_tokens") == zcode["zcode_worker_total_tokens"], "zcode token total changed")
    require(report.get("zcode_worker_total_tokens") == 9729458, "zcode token total is not preserved")
    require(report.get("zcode_strict_green_count") == 18, "zcode strict-green count is not preserved")
    require(report.get("total_workflow_savings_claimable") is False, "workflow savings must remain unclaimable")
    require(report.get("codex_orchestration_total_tokens") is None, "orchestration tokens must remain null")
    if report.get("codex_worker_usage_status") != "measured":
        require(report.get("codex_worker_total_tokens") is None, "unavailable worker usage must not be zero-filled")
    else:
        require(isinstance(report.get("codex_worker_total_tokens"), int), "measured worker tokens missing")
        require(report["codex_worker_total_tokens"] > 0, "measured worker tokens must be positive")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
