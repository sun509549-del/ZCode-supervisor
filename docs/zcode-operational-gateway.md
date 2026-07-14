# ZCode Operational Gateway

This document defines the safe operational route for Codex-managed development
tasks that may use ZCode as a bounded implementation worker.

## Route Policy

Codex owns planning, inspection, route selection, repairs, validation, final
acceptance, git, and PR creation. ZCode owns only bounded implementation in the
allowed file set.

Default route order:

1. Codex plans the task, allowed files, forbidden files, and validation command.
2. Codex tries the ZCode CLI route when the task is a safe bounded
   implementation:

```bash
python3 tools/zcode_supervisor/zcode_supervisor.py auto-route \
  --workspace . \
  --objective "<normal dev task>" \
  --allowed "<file>" \
  --validation "<command>" \
  --execute \
  --max-attempts 1 \
  --usage-snapshot-source none \
  --continue-with-codex-fallback
```

3. Codex uses app-backed CDP only when the ZCode app target, editable textbox,
   expected packet workspace, and workspace binding are proven before submit.
   App-backed mode is never the default. If binding is unknown or mismatched,
   classify `workspace_not_bound` and continue with Codex fallback.
4. If the ZCode route is blocked, Codex fallback continues within the same safe
   allowed surface. Fallback must still run validation and strict gates.

## Classifications

- `provider_timeout`: ZCode timed out or returned `run_timeout`.
- `provider_overload`: provider stderr/run JSON reports overload.
- `workspace_not_bound`: app-backed CDP cannot prove the active workspace.
- `usage_unavailable`: ZCode success or partial result has no positive measured
  token total; tokens stay `null`.
- `validation_failure`: validation failed under the supervisor.
- `strict_failure`: strict acceptance failed, strict violations exist, or a
  required implementation produced no changes.

## Result Schema

The stable fields are defined by
`docs/zcode-strict-contract-v3/schemas/operational_gateway_result.schema.json`.

Required reporting fields:

- `route_used = zcode_cli | zcode_app_cdp | codex_fallback`
- `zcode_usage_status = measured | unavailable | skipped`
- `fallback_reason`
- `strict_accepted`
- `claim_family`

When `zcode_usage_status` is `unavailable` or `skipped`, `zcode_total_tokens`
must be `null`, never `0`.

## Safe Claims

`zcode_cli`:
Codex may say ZCode CLI implemented the bounded worker portion only when
`delegation_ok=true`, the run JSON is present, validation passes, and strict
acceptance is true or not required by the task. Token usage may be claimed only
when `zcode_usage_status=measured` and `zcode_total_tokens` is a positive
integer.

`zcode_app_cdp`:
Codex may say app-backed ZCode implemented only when workspace binding was
proven before submit and the app runner reports `worker_execution_backend` or
`backend` as `zcode_app_cdp`. App-backed mode remains explicit and non-default.

`codex_fallback`:
Codex may say Codex implemented after ZCode was skipped or blocked. It must not
claim ZCode implementation, ZCode token savings, or direct/app-backed evidence.
Fallback uses `claim_family=codex_fallback_no_zcode_claim`.
This claim family means no ZCode token-saving claim is made.

Direct mode:
Direct/direct-launcher claims remain separate from this gateway. Direct mode is
not the default, and operational fallback is not direct-mode evidence.

## Guards

- No 20+ live benchmark.
- No production green-path.
- Direct mode and app-backed mode are non-default.
- GLM-5.2 remains fixed.
- No GLM-4.7 fallback.
- No time-of-day gate.
- Strict gates stay fail-closed.
- `artifacts/reports` are not committed.
