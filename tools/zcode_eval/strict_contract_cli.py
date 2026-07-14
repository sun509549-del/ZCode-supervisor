"""CLI registration for Strict Contract V3 evaluation commands."""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    from .strict_contract import (
        ACCEPTANCE_MODES,
        DEFAULT_ACCEPTANCE_LEDGER,
        DEFAULT_CONTRACT_LEDGER,
        DEFAULT_ROI_LEDGER,
        DEFAULT_RUBRIC_DIR,
        DEFAULT_TRACE_LEDGER,
        REPAIR_SIZES,
        RISK_LEVELS,
        command_accept_strict_contract,
        command_build_strict_contract,
        command_summarize_strict_contract,
    )
except ImportError:  # pragma: no cover - direct script execution
    from strict_contract import (  # type: ignore[no-redef]
        ACCEPTANCE_MODES,
        DEFAULT_ACCEPTANCE_LEDGER,
        DEFAULT_CONTRACT_LEDGER,
        DEFAULT_ROI_LEDGER,
        DEFAULT_RUBRIC_DIR,
        DEFAULT_TRACE_LEDGER,
        REPAIR_SIZES,
        RISK_LEVELS,
        command_accept_strict_contract,
        command_build_strict_contract,
        command_summarize_strict_contract,
    )


def add_strict_contract_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    build = subparsers.add_parser("build-strict-contract", help="Expand a Strict Contract V3 rubric into a task contract.")
    build.add_argument("--rubric-id", required=True)
    build.add_argument("--task-id", required=True)
    build.add_argument("--out", type=Path, required=True)
    build.add_argument("--allowed-file", action="append", default=[])
    build.add_argument("--forbidden-file", action="append", default=[])
    build.add_argument("--validation-command", action="append", default=[])
    build.add_argument("--goal")
    build.add_argument("--contract-id")
    build.add_argument("--risk-level", choices=RISK_LEVELS)
    build.add_argument("--ambiguity-score", type=float, default=0)
    build.add_argument("--override-json", type=Path)
    build.add_argument("--rubric-dir", type=Path, default=DEFAULT_RUBRIC_DIR)
    build.add_argument("--experiment-id", default="strict-contract-v3")
    build.add_argument("--codex-run-id")
    build.add_argument("--generation-effective-codex-work", type=int)
    build.add_argument("--contract-ledger", type=Path, default=DEFAULT_CONTRACT_LEDGER)
    build.set_defaults(func=command_build_strict_contract)

    accept = subparsers.add_parser("accept-strict-contract-audit", help="Check a ZCode self-audit against a strict contract.")
    accept.add_argument("--contract", type=Path, required=True)
    accept.add_argument("--self-audit", type=Path, required=True)
    accept.add_argument("--acceptance-out", type=Path)
    accept.add_argument("--mode", choices=ACCEPTANCE_MODES, default="manifest_only")
    accept.add_argument("--validation-result", choices=("pass", "fail", "skipped", "unknown"))
    accept.add_argument("--validation-exit-code", type=int)
    accept.add_argument("--changed-file", action="append", default=[])
    accept.add_argument("--files-changed", type=int)
    accept.add_argument("--insertions", type=int, default=0)
    accept.add_argument("--deletions", type=int, default=0)
    accept.add_argument("--codex-repair-size", choices=REPAIR_SIZES, default="none")
    accept.add_argument("--full-diff-read", action="store_true")
    accept.add_argument("--full-log-read", action="store_true")
    accept.add_argument("--green-path-non-llm", action="store_true")
    accept.add_argument("--shadow-codex-audit-enabled", action="store_true")
    accept.add_argument("--shadow-codex-audit-result", choices=("agree", "disagree", "unavailable"), default="unavailable")
    accept.add_argument("--missed-risk-flag", action="append", default=[])
    accept.add_argument("--experiment-id", default="strict-contract-v3")
    accept.add_argument("--added-spec-effective-tokens", type=int)
    accept.add_argument("--trace-ledger", type=Path, default=DEFAULT_TRACE_LEDGER)
    accept.add_argument("--acceptance-ledger", type=Path, default=DEFAULT_ACCEPTANCE_LEDGER)
    accept.add_argument("--roi-ledger", type=Path, default=DEFAULT_ROI_LEDGER)
    accept.set_defaults(func=command_accept_strict_contract)

    summary = subparsers.add_parser("summarize-strict-contract", help="Summarize Strict Contract V3 ledgers.")
    summary.add_argument("--experiment-id", default="strict-contract-v3")
    summary.add_argument("--contract-ledger", type=Path, default=DEFAULT_CONTRACT_LEDGER)
    summary.add_argument("--trace-ledger", type=Path, default=DEFAULT_TRACE_LEDGER)
    summary.add_argument("--acceptance-ledger", type=Path, default=DEFAULT_ACCEPTANCE_LEDGER)
    summary.add_argument("--roi-ledger", type=Path, default=DEFAULT_ROI_LEDGER)
    summary.add_argument("--out", type=Path, required=True)
    summary.set_defaults(func=command_summarize_strict_contract)
