#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-.}"

missing=0
need_file() {
  local path="$1"
  if [[ ! -f "$ROOT/$path" ]]; then
    echo "missing: $path" >&2
    missing=1
  else
    echo "ok: $path"
  fi
}

need_file "goal-prompt-zcode-v2.txt"
need_file "docs/goals/current/zcode-fresh-clone-readiness-v2.md"
need_file "docs/goals/current/zcode-implementation-policy.md"
need_file "docs/goals/current/goal-spec.md"
need_file "docs/goals/current/progress.md"
need_file "docs/goals/current/eval-results.jsonl"
need_file "docs/goals/current/retrospective.md"
need_file "docs/goals/current/knowledge-card.md"
need_file "docs/goals/current/zcode-delegation-quality-rails.md"
need_file "benchmarks/hard-token-fixtures/README.md"
need_file "benchmarks/hard-token-fixtures/billing-credit-contract/README.md"
need_file "benchmarks/hard-token-fixtures/billing-credit-contract/test/credits.test.js"
need_file "benchmarks/hard-token-fixtures/policy-reason-contract/README.md"
need_file "benchmarks/hard-token-fixtures/policy-reason-contract/test/policy.test.js"
need_file "scripts/check_hard_benchmark_fixtures.sh"
need_file "scripts/run_hard_fixture_candidate_gate.sh"
need_file "scripts/check_zcode_delegated_artifact_quality.sh"
need_file "FRESH_CLONE_LOG.template.md"

if [[ "$missing" -ne 0 ]]; then
  exit 1
fi

python3 - "$ROOT/docs/goals/current/eval-results.jsonl" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
required = {
    "task_id",
    "task_complexity",
    "route_decision",
    "allowed_files",
    "codex_token_evidence",
    "zcode_usage_evidence",
    "tests_run",
    "quality_verdict",
    "scope_violations",
    "audit_result",
}
for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
    if not line.strip():
        continue
    record = json.loads(line)
    missing_keys = sorted(required - record.keys())
    if missing_keys:
        raise SystemExit(f"{path}:{line_number}: missing keys: {', '.join(missing_keys)}")
metric_keys = {
    "artifact_quality",
    "scope_safety",
    "validation_result",
    "codex_repair_size",
    "codex_token_usage_status",
}
if not any(metric_keys <= set(json.loads(line).keys()) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()):
    raise SystemExit(f"{path}: missing at least one record with delegated artifact quality metrics")
print(f"ok: {path} valid JSONL")
PY
