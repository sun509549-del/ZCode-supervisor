#!/usr/bin/env python3
"""Run the ZCode-required 20+ resumable benchmark."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.zcode_supervisor.zcode_20_resumable import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
