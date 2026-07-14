#!/usr/bin/env python3
"""Check final evidence for the ZCode-required 20+ resumable benchmark."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.zcode_supervisor.zcode_20_resumable import validate_final_outcome


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, required=True)
    args = parser.parse_args()

    payload = json.loads(args.path.read_text(encoding="utf-8"))
    validate_final_outcome(payload)
    print(f"final_outcome={payload['final_outcome']}")
    print(f"task_count={payload.get('task_count')}")
    print(f"delegated_rows={payload.get('delegated_rows')}")
    print(f"strict_green_count={payload.get('strict_green_count')}")
    print(f"provider_pause_count={payload.get('provider_pause_count')}")
    print(f"provider_rate_limit_count={payload.get('provider_rate_limit_count')}")
    print(f"worker_token_measured_count={payload.get('worker_token_measured_count')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
