#!/usr/bin/env python3
"""Validate a Goal H live evidence manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.zcode_eval.live_evidence import validate_live_evidence_manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--json", action="store_true", help="Print validation JSON to stdout.")
    return parser


def _load_manifest(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"missing manifest: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON manifest: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("manifest root must be a JSON object")
    return payload


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = validate_live_evidence_manifest(_load_manifest(args.manifest))
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        status = "valid" if summary["valid"] else "invalid"
        print(f"{status}: {args.manifest}")
        for error in summary["errors"]:
            print(f"error: {error}")
        for warning in summary["warnings"]:
            print(f"warning: {warning}")
    return 0 if summary["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
