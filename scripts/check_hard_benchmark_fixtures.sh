#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-.}"
FIXTURE_ROOT="$ROOT/benchmarks/hard-token-fixtures"

check_expected_failure() {
  local name="$1"
  local needle="$2"
  local output
  local status

  set +e
  output="$(cd "$FIXTURE_ROOT/$name" && npm test 2>&1)"
  status="$?"
  set -e

  if [[ "$status" -eq 0 ]]; then
    echo "unexpected pass: $name is no longer an unsolved benchmark fixture" >&2
    return 1
  fi

  if [[ "$output" != *"$needle"* ]]; then
    echo "missing contract evidence for $name: expected output to mention '$needle'" >&2
    printf '%s\n' "$output" | tail -n 40 >&2
    return 1
  fi

  echo "ok: $name fails with deterministic contract evidence"
}

check_expected_failure "billing-credit-contract" "447.66"
check_expected_failure "policy-reason-contract" "default triage"
