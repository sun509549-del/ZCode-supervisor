#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  cat <<'USAGE'
Usage:
  bash scripts/run_direct_delegated_strict_contract.sh [strict-contract args]

Opt-in direct delegated strict-contract entrypoint.

Examples:
  bash scripts/run_direct_delegated_strict_contract.sh \
    --only billing-credit-contract,vision-card-latest \
    --dry-run

  bash scripts/run_direct_delegated_strict_contract.sh \
    --only billing-credit-contract,ledger-summary-contract,policy-reason-contract,vision-card-latest

This wrapper always calls:
  python3 tools/zcode_eval/strict_contract_comparison.py --delegation-execution direct

Claim family:
  direct_orchestrated_delegation_savings

Important:
  Direct mode is opt-in and separate from codex_mediated_delegation_savings.
  The direct delegated-arm 0 effective_codex_work value means Codex-side
  launcher work only. It is not total workflow cost and it is not ZCode
  worker/model usage.
  Production green-path skip remains disabled.
USAGE
}

for arg in "$@"; do
  case "$arg" in
    -h|--help)
      usage
      exit 0
      ;;
    --delegation-execution|--delegation-execution=*)
      echo "error: this wrapper always uses --delegation-execution direct; do not pass --delegation-execution" >&2
      exit 2
      ;;
  esac
done

echo "Direct delegated strict-contract mode: opt-in"
echo "Claim family: direct_orchestrated_delegation_savings"
echo "Reports: artifacts/reports/<timestamp>__codex__strict-contract-v3-comparison/"
echo "Caveat: direct delegated-arm 0 effective_codex_work is Codex-side launcher work only, not total cost or ZCode worker usage."

cd "$ROOT"
exec python3 tools/zcode_eval/strict_contract_comparison.py \
  --delegation-execution direct \
  "$@"
