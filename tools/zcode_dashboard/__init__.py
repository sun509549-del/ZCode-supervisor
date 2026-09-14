"""Python launcher for the local ZCode Supervisor dashboard."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    server = Path(__file__).with_name("server.mjs")
    try:
        return subprocess.call(["node", str(server), *sys.argv[1:]])
    except FileNotFoundError:
        print("zcode-dashboard: Node.js 22 or newer is required on PATH.", file=sys.stderr)
        return 1

