#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-.}"
STAMP="$(date +%Y%m%d-%H%M%S)"
REPORT_DIR="$ROOT/artifacts/reports/${STAMP}__codex__hard-fixture-candidate-gate"
TMPROOT="$(mktemp -d -t zcode-hard-fixture-candidate.XXXXXX)"
ACCEPT_VALIDATED_ARTIFACT_AFTER_MS="${ACCEPT_VALIDATED_ARTIFACT_AFTER_MS:-60000}"
ZCODE_CANDIDATE_GATE_MANIFEST_ACCEPTANCE="${ZCODE_CANDIDATE_GATE_MANIFEST_ACCEPTANCE:-0}"
ZCODE_CANDIDATE_GATE_GREEN_PATH_NON_LLM="${ZCODE_CANDIDATE_GATE_GREEN_PATH_NON_LLM:-0}"
ZCODE_CANDIDATE_GATE_SECRET_SCAN_RESULT="${ZCODE_CANDIDATE_GATE_SECRET_SCAN_RESULT:-unknown}"
ZCODE_CANDIDATE_GATE_STRICT_CONTRACT="${ZCODE_CANDIDATE_GATE_STRICT_CONTRACT:-0}"
ZCODE_CANDIDATE_GATE_STRICT_SHADOW="${ZCODE_CANDIDATE_GATE_STRICT_SHADOW:-0}"
ZCODE_CANDIDATE_GATE_TIMEOUT_MS="${ZCODE_CANDIDATE_GATE_TIMEOUT_MS:-180000}"
ZCODE_CANDIDATE_GATE_STRICT_TIMEOUT_MS="${ZCODE_CANDIDATE_GATE_STRICT_TIMEOUT_MS:-300000}"
if [[ "$ZCODE_CANDIDATE_GATE_GREEN_PATH_NON_LLM" != "0" ]]; then
  ZCODE_CANDIDATE_GATE_MANIFEST_ACCEPTANCE=1
fi
if [[ "$ZCODE_CANDIDATE_GATE_STRICT_SHADOW" != "0" ]]; then
  ZCODE_CANDIDATE_GATE_STRICT_CONTRACT=1
  ZCODE_CANDIDATE_GATE_MANIFEST_ACCEPTANCE=1
  ZCODE_CANDIDATE_GATE_GREEN_PATH_NON_LLM=1
fi

mkdir -p "$REPORT_DIR"
printf '%s\n' "$TMPROOT" > "$REPORT_DIR/tmp-root.txt"

build_manifest_acceptance() {
  local task="$1"
  local allowed="$2"
  local diff_path="$REPORT_DIR/$task.source.diff"
  local manifest_dir="$REPORT_DIR/manifests"
  local run_json
  local insertions
  local deletions
  local files_changed
  local numstat

  mkdir -p "$manifest_dir"
  run_json="$(find "$REPORT_DIR/$task.run-files" -type f -name '*.zcode.json' | sort | head -n 1 || true)"
  if [[ -z "$run_json" ]]; then
    printf '%s\n' "zcode_run_json_missing" > "$manifest_dir/$task.manifest.skipped"
    return 0
  fi

  numstat="$(git apply --numstat < "$diff_path" 2>/dev/null || true)"
  insertions="$(printf '%s\n' "$numstat" | awk '{sum += $1} END {print sum + 0}')"
  deletions="$(printf '%s\n' "$numstat" | awk '{sum += $2} END {print sum + 0}')"
  files_changed="$(printf '%s\n' "$numstat" | awk 'NF {count += 1} END {print count + 0}')"

  python3 "$ROOT/tools/zcode_eval/zcode_eval.py" build-zcode-result-manifest \
    --task-id "$task" \
    --zcode-run-json "$run_json" \
    --manifest-out "$manifest_dir/$task.manifest.json" \
    --validation-log "$REPORT_DIR/$task.route.stderr.log" \
    --diff-path "$diff_path" \
    --changed-file "$allowed" \
    --allowed-files-only \
    --insertions "$insertions" \
    --deletions "$deletions" \
    --files-changed "$files_changed" \
    > "$manifest_dir/$task.manifest.stdout.json" \
    2> "$manifest_dir/$task.manifest.stderr.log"

  set +e
  if [[ "$ZCODE_CANDIDATE_GATE_GREEN_PATH_NON_LLM" != "0" ]]; then
    python3 "$ROOT/tools/zcode_eval/zcode_eval.py" accept-zcode-manifest \
      --manifest "$manifest_dir/$task.manifest.json" \
      --acceptance-out "$manifest_dir/$task.acceptance.json" \
      --workspace-root "$ROOT" \
      --secret-scan-result "$ZCODE_CANDIDATE_GATE_SECRET_SCAN_RESULT" \
      --max-changed-files 1 \
      --green-path-non-llm \
      --shadow-codex-audit-enabled \
      --shadow-codex-audit-result unavailable \
      > "$manifest_dir/$task.acceptance.stdout.json" \
      2> "$manifest_dir/$task.acceptance.stderr.log"
  else
    python3 "$ROOT/tools/zcode_eval/zcode_eval.py" accept-zcode-manifest \
      --manifest "$manifest_dir/$task.manifest.json" \
      --acceptance-out "$manifest_dir/$task.acceptance.json" \
      --workspace-root "$ROOT" \
      --secret-scan-result "$ZCODE_CANDIDATE_GATE_SECRET_SCAN_RESULT" \
      --max-changed-files 1 \
      > "$manifest_dir/$task.acceptance.stdout.json" \
      2> "$manifest_dir/$task.acceptance.stderr.log"
  fi
  printf '%s\n' "$?" > "$manifest_dir/$task.acceptance.rc"
  set -e
}

strict_rubric_for_task() {
  case "$1" in
    billing-credit-contract) printf '%s\n' "billing_cent_rounding.v1" ;;
    policy-reason-contract) printf '%s\n' "policy_exact_label_routing.v1" ;;
    ledger-summary-contract) printf '%s\n' "ledger_summary_state_aggregation.v1" ;;
    vision-card-latest) printf '%s\n' "vision_card_layout_match.v1" ;;
    *) printf '%s\n' "" ;;
  esac
}

strict_risk_for_task() {
  case "$1" in
    ledger-summary-contract) printf '%s\n' "L1" ;;
    *) printf '%s\n' "L2" ;;
  esac
}

task_class_for_task() {
  case "$1" in
    billing-credit-contract) printf '%s\n' "small-fix" ;;
    policy-reason-contract) printf '%s\n' "small-fix" ;;
    ledger-summary-contract) printf '%s\n' "small-fix" ;;
    vision-card-latest) printf '%s\n' "small-fix" ;;
    *) printf '%s\n' "root-cause" ;;
  esac
}

task_timeout_ms() {
  if [[ "$ZCODE_CANDIDATE_GATE_STRICT_CONTRACT" != "0" ]]; then
    printf '%s\n' "$ZCODE_CANDIDATE_GATE_STRICT_TIMEOUT_MS"
  else
    printf '%s\n' "$ZCODE_CANDIDATE_GATE_TIMEOUT_MS"
  fi
}

build_strict_contract_acceptance() {
  local task="$1"
  local allowed="$2"
  local strict_dir="$REPORT_DIR/strict-contract"
  local packet_json
  local self_audit

  mkdir -p "$strict_dir"
  packet_json="$(find "$TMPROOT/$task/.codex/zcode/packets" -type f -name '*.json' | sort | head -n 1 || true)"
  self_audit="$TMPROOT/$task/.codex/zcode/runs/zcode_self_audit.json"
  if [[ -z "$packet_json" ]]; then
    printf '%s\n' "packet_missing" > "$strict_dir/$task.strict.skipped"
    return 0
  fi
  python3 - "$packet_json" "$strict_dir/$task.task_contract.json" <<'PY'
import json
import sys
from pathlib import Path

packet = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
contract = packet.get("strict_contract", {}).get("task_contract")
if not isinstance(contract, dict):
    raise SystemExit("strict task_contract missing")
Path(sys.argv[2]).write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
  if [[ ! -f "$self_audit" ]]; then
    printf '%s\n' "self_audit_missing" > "$strict_dir/$task.strict.skipped"
    return 0
  fi
  set +e
  if [[ "$ZCODE_CANDIDATE_GATE_STRICT_SHADOW" != "0" ]]; then
    python3 "$ROOT/tools/zcode_eval/zcode_eval.py" accept-strict-contract-audit \
      --contract "$strict_dir/$task.task_contract.json" \
      --self-audit "$self_audit" \
      --acceptance-out "$strict_dir/$task.strict.acceptance.json" \
      --mode manifest_only \
      --validation-result pass \
      --validation-exit-code 0 \
      --changed-file "$allowed" \
      --files-changed 1 \
      --trace-ledger "$strict_dir/trace-ledger.jsonl" \
      --acceptance-ledger "$strict_dir/acceptance-ledger.jsonl" \
      --roi-ledger "$strict_dir/roi-ledger.jsonl" \
      --green-path-non-llm \
      --shadow-codex-audit-enabled \
      --shadow-codex-audit-result unavailable \
      > "$strict_dir/$task.strict.acceptance.stdout.json" \
      2> "$strict_dir/$task.strict.acceptance.stderr.log"
  else
    python3 "$ROOT/tools/zcode_eval/zcode_eval.py" accept-strict-contract-audit \
      --contract "$strict_dir/$task.task_contract.json" \
      --self-audit "$self_audit" \
      --acceptance-out "$strict_dir/$task.strict.acceptance.json" \
      --mode manifest_only \
      --validation-result pass \
      --validation-exit-code 0 \
      --changed-file "$allowed" \
      --files-changed 1 \
      --trace-ledger "$strict_dir/trace-ledger.jsonl" \
      --acceptance-ledger "$strict_dir/acceptance-ledger.jsonl" \
      --roi-ledger "$strict_dir/roi-ledger.jsonl" \
      > "$strict_dir/$task.strict.acceptance.stdout.json" \
      2> "$strict_dir/$task.strict.acceptance.stderr.log"
  fi
  printf '%s\n' "$?" > "$strict_dir/$task.strict.acceptance.rc"
  set -e
}

run_task() {
  local task="$1"
  local allowed="$2"
  local objective

  cp -R "$ROOT/benchmarks/hard-token-fixtures/$task" "$TMPROOT/$task"
  python3 "$ROOT/tools/zcode_supervisor/zcode_supervisor.py" install-repo \
    --repo "$TMPROOT/$task" \
    > "$REPORT_DIR/$task.install.json"

  objective="Fix the $task hard benchmark fixture so npm test passes. Only edit the implementation file. Preserve the acceptance tests and README contract."

  run_auto_route() {
    if [[ "$ZCODE_CANDIDATE_GATE_STRICT_CONTRACT" != "0" ]]; then
      python3 "$ROOT/tools/zcode_supervisor/zcode_supervisor.py" auto-route \
        --workspace "$TMPROOT/$task" \
        --objective "$objective" \
        --allowed "$allowed" \
        --validation "npm test" \
        --task-class "$(task_class_for_task "$task")" \
        --execute \
        --max-attempts 1 \
        --timeout-ms "$(task_timeout_ms)" \
        --validation-timeout 30 \
        --usage-snapshot-source none \
        --no-repair-validation \
        --strict-contract-rubric-id "$(strict_rubric_for_task "$task")" \
        --strict-contract-task-id "$task" \
        --strict-contract-risk-level "$(strict_risk_for_task "$task")" \
        "$@" \
        --result-verbosity compact
      return
    fi
    python3 "$ROOT/tools/zcode_supervisor/zcode_supervisor.py" auto-route \
      --workspace "$TMPROOT/$task" \
      --objective "$objective" \
      --allowed "$allowed" \
      --validation "npm test" \
      --task-class "$(task_class_for_task "$task")" \
      --execute \
      --max-attempts 1 \
      --timeout-ms "$(task_timeout_ms)" \
      --validation-timeout 30 \
      --usage-snapshot-source none \
      --no-repair-validation \
      "$@" \
      --result-verbosity compact
  }

  set +e
  if [[ "$ACCEPT_VALIDATED_ARTIFACT_AFTER_MS" != "0" ]]; then
    run_auto_route \
      --accept-validated-artifact-after-ms "$ACCEPT_VALIDATED_ARTIFACT_AFTER_MS" \
      > "$REPORT_DIR/$task.route.stdout.json" \
      2> "$REPORT_DIR/$task.route.stderr.log"
  else
    run_auto_route \
      > "$REPORT_DIR/$task.route.stdout.json" \
      2> "$REPORT_DIR/$task.route.stderr.log"
  fi
  local rc="$?"
  set -e

  printf '%s\n' "$rc" > "$REPORT_DIR/$task.route.rc"
  find "$TMPROOT/$task/.codex/zcode" -maxdepth 4 -type f -print 2>/dev/null \
    | sort > "$REPORT_DIR/$task.zcode-files.txt" || true
  mkdir -p "$REPORT_DIR/$task.run-files"
  find "$TMPROOT/$task/.codex/zcode" -maxdepth 4 -type f \
    -exec cp {} "$REPORT_DIR/$task.run-files/" \; 2>/dev/null || true
  diff -u "$ROOT/benchmarks/hard-token-fixtures/$task/$allowed" "$TMPROOT/$task/$allowed" \
    > "$REPORT_DIR/$task.source.diff" 2>&1 || true

  if [[ "$ZCODE_CANDIDATE_GATE_MANIFEST_ACCEPTANCE" != "0" ]]; then
    build_manifest_acceptance "$task" "$allowed"
  fi
  if [[ "$ZCODE_CANDIDATE_GATE_STRICT_CONTRACT" != "0" ]]; then
    build_strict_contract_acceptance "$task" "$allowed"
  fi
}

run_task "billing-credit-contract" "src/credits.js"
run_task "policy-reason-contract" "src/policy.js"

printf '%s\n' "$REPORT_DIR"
