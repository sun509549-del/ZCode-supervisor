#!/usr/bin/env python3
"""Check whether a canary artifact is eligible for a live 20+ retry."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.zcode_eval.live_retry_preflight import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
