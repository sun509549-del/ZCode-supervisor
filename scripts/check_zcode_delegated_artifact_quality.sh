#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-.}"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/zcode-artifact-quality.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

WORKSPACE="$TMP/workspace"
mkdir -p "$WORKSPACE/src"
printf 'def answer():\n    return 41\n' > "$WORKSPACE/src/app.py"
printf 'fixture\n' > "$WORKSPACE/README.md"

python3 "$ROOT/tools/zcode_supervisor/zcode_supervisor.py" packet \
  --workspace "$WORKSPACE" \
  --objective "Fix src/app.py so answer() returns 42." \
  --allowed src/app.py \
  --forbidden README.md \
  --validation "python3 -m py_compile src/app.py" \
  --expected-output "src/app.py contains a valid answer() implementation returning 42." \
  --acceptance-criterion "Codex audit reports artifact_quality=pass." \
  --acceptance-criterion "Scope safety and validation both pass." \
  --max-changed-files 1 \
  --out "$TMP/packet.json" >/dev/null

python3 "$ROOT/tools/zcode_supervisor/zcode_supervisor.py" snapshot \
  --workspace "$WORKSPACE" \
  --out "$TMP/before.json" >/dev/null

# Simulate the bounded artifact that ZCode should produce; the script validates
# the Codex-side review contract without spending live model/API quota.
printf 'def answer():\n    return 42\n' > "$WORKSPACE/src/app.py"

python3 "$ROOT/tools/zcode_supervisor/zcode_supervisor.py" audit \
  --workspace "$WORKSPACE" \
  --snapshot "$TMP/before.json" \
  --packet "$TMP/packet.json" > "$TMP/audit.json"

python3 - "$TMP/audit.json" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
expected = {
    "ok": True,
    "artifact_quality": "pass",
    "scope_safety": "pass",
    "validation_result": "pass",
    "codex_repair_size_recommendation": "none",
}
for key, value in expected.items():
    actual = payload.get(key)
    if actual != value:
        raise SystemExit(f"{key}: expected {value!r}, got {actual!r}")
ids = {item.get("id"): item.get("status") for item in payload.get("artifact_review_checklist", [])}
for item_id in ("scope_safety", "validation_passed", "secret_scan_clear"):
    if ids.get(item_id) != "pass":
        raise SystemExit(f"checklist {item_id}: expected pass, got {ids.get(item_id)!r}")
PY

python3 "$ROOT/tools/zcode_eval/zcode_eval.py" accept-zcode-artifact \
  --zcode-run-json "$TMP/audit.json" \
  --label artifact-quality-fixture > "$TMP/acceptance.json"

python3 - "$TMP/acceptance.json" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("ok") is not True:
    raise SystemExit(f"acceptance gate failed: {payload.get('violations')!r}")
PY

python3 "$ROOT/tools/zcode_eval/zcode_eval.py" append-result \
  --path "$TMP/eval-results.jsonl" \
  --run-id artifact-quality-fixture \
  --tool zcode \
  --task-id artifact-quality-fixture \
  --task-name "bounded delegated artifact quality fixture" \
  --status pass \
  --artifact-quality pass \
  --scope-safety pass \
  --validation-result pass \
  --codex-repair-size none \
  --codex-token-usage-status unavailable \
  --codex-token-usage-reason "local fixture without Codex exec usage events" >/dev/null

python3 - "$TMP/eval-results.jsonl" <<'PY'
import json
import sys
from pathlib import Path

records = [json.loads(line) for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines() if line.strip()]
if len(records) != 1:
    raise SystemExit(f"expected one eval record, got {len(records)}")
record = records[0]
for key in ("artifact_quality", "scope_safety", "validation_result", "codex_repair_size", "codex_token_usage_status"):
    if key not in record:
        raise SystemExit(f"missing eval field: {key}")
PY

echo "ok: delegated artifact quality fixture"
