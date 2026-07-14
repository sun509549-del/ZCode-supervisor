#!/usr/bin/env python3
"""Check final evidence for the ZCode-required one-task goal."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.zcode_supervisor.one_task_loop import validate_final_outcome


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, required=True)
    args = parser.parse_args()

    payload = json.loads(args.path.read_text(encoding="utf-8"))
    validate_final_outcome(payload)
    print(f"final_outcome={payload['final_outcome']}")
    print(f"route_used={payload.get('route_used')}")
    print(f"claim_family={payload.get('claim_family')}")
    print(f"worker_usage_status={payload.get('worker_usage_status')}")
    print(f"worker_total_tokens={payload.get('worker_total_tokens')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
