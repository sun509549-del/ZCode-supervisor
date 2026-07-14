#!/usr/bin/env python3
"""Check Goal O accounting against existing evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.zcode_eval.goal_o_accounting import build_goal_o_accounting_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--archive-root", type=Path)
    parser.add_argument("--codex-usage-ledger", type=Path, action="append", default=[])
    parser.add_argument("--goal-usage-json", type=Path, action="append", default=[])
    parser.add_argument("--expect-final-outcome", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_goal_o_accounting_report(
        args.evidence_root,
        archive_root=args.archive_root,
        codex_usage_ledgers=args.codex_usage_ledger,
        goal_usage_json=args.goal_usage_json,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    if report.get("final_outcome") != args.expect_final_outcome:
        raise SystemExit(
            f"expected final_outcome={args.expect_final_outcome}, "
            f"got {report.get('final_outcome')}"
        )
    if report.get("codex_orchestration_total_tokens") == 0:
        raise SystemExit("Codex orchestration tokens must not be zero-filled")
    if report.get("total_workflow_savings_claimable") and report.get("blocker"):
        raise SystemExit("claimable report must not have a blocker")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
