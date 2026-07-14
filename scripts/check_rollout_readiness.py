#!/usr/bin/env python3
"""Write strict-contract rollout readiness evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.zcode_eval.rollout_readiness import DEFAULT_EVIDENCE_PATH, assess_rollout_readiness, write_readiness


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_EVIDENCE_PATH)
    live = parser.add_mutually_exclusive_group()
    live.add_argument("--live-benchmark-summary", type=Path)
    live.add_argument("--live-evidence-manifest", type=Path)
    parser.add_argument("--manual-approval-status", choices=("pending", "approved"), default="pending")
    parser.add_argument("--production-green-path-enabled", action="store_true")
    parser.add_argument("--require-production-ready", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print the readiness JSON to stdout.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = assess_rollout_readiness(
        live_benchmark_summary=args.live_benchmark_summary,
        live_evidence_manifest=args.live_evidence_manifest,
        manual_approval_status=args.manual_approval_status,
        production_green_path=args.production_green_path_enabled,
    )
    write_readiness(args.output, payload)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(args.output)
    if args.require_production_ready and payload["rollout_status"] != "ready":
        return 1
    if payload["rollout_status"] == "blocked":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
