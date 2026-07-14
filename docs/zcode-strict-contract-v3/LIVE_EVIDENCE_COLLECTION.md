# Live Evidence Collection

Goal H defines the safe operational layer for collecting live rollout evidence
later. It does not authorize running the expensive live 20+ provider benchmark.

## Operator Rule

Do not run the live benchmark unless Aki explicitly approves it in a separate
message. Without that approval, the expected state remains `fixture_ready` and
`dry_run_only`.

## Live Benchmark Command Path

After explicit approval, the operator should run the 20-task strict-contract
comparison harness from a clean branch and preserve compact artifacts:

```bash
python3 tools/zcode_eval/strict_contract_comparison.py \
  --report-dir artifacts/reports/<run-id>
```

`tools/zcode_eval/strict_contract_tasks.py` is the benchmark count source of
truth and must expose at least 20 task slugs before this command is run. Do not
use `scripts/run_hard_fixture_candidate_gate.sh` as the live 20+ benchmark
command unless it is changed and tested to cover at least 20 tasks; as of Goal
I it is only a two-fixture hard gate.

The command must produce or reference a live evidence manifest before any
production rollout claim is considered. Store compact reports, manifests,
hashes, and usage ledgers under:

- `artifacts/reports/<run-id>/`
- `artifacts/evals/`

Do not commit large generated benchmark artifacts or raw sensitive logs.

## Required Manifest

Validate the manifest before feeding it to rollout readiness:

```bash
python3 scripts/check_live_evidence_manifest.py \
  artifacts/reports/<run-id>/live-evidence-manifest.json \
  --json
```

Then produce rollout readiness evidence:

```bash
python3 scripts/check_rollout_readiness.py \
  --live-evidence-manifest artifacts/reports/<run-id>/live-evidence-manifest.json \
  --output artifacts/reports/rollout-readiness/<run-id>.json \
  --json
```

The schema is documented in
[`schemas/live_evidence_manifest.schema.json`](schemas/live_evidence_manifest.schema.json).
A fixture-ready example is available at
[`examples/live-evidence-manifest.fixture-ready.example.json`](examples/live-evidence-manifest.fixture-ready.example.json).

## Required Metadata

The manifest must include:

- `benchmark_run_id`
- `benchmark_run_kind`
- `task_count`
- `task_slugs`
- `provider_models`
- `claim_family`
- `strict_result_status`
- `quality_result_status`
- `worker_usage`
- `codex_usage`
- `total_workflow_savings`
- `artifact_paths`
- `operator`
- `created_at`
- `approval_status`

`claim_family` must be exactly one of:

- `direct_orchestrated_delegation_savings`
- `codex_mediated_delegation_savings`

Direct-orchestrated and codex-mediated claim families must never be mixed in
one deployable savings claim.

## Worker Token Usage

Worker usage can count as measured only when all of these are true:

- `worker_usage.status` is `measured`
- `worker_usage.unit` is `tokens`
- `worker_usage.total_tokens` is a positive integer
- `worker_usage.source_path` points to the preserved usage source

Unavailable usage is not zero; quota percent and credits are not token counts.
Percent-only, credit-only, quota-only, balance-only, or empty sidecar evidence
must be recorded as unavailable or partial with `no_usage_reason`.

For ZCode/zcodectl/provider integrations, the required per-row sidecar contract
is:

- sidecar path lives beside the delegated row's `zcode-run.json`
- `source_type` is accepted by strict-contract comparison
- top-level `unit` is `tokens`
- `usage.total_tokens` is a positive integer
- `provider` and `model` are present when available
- `usage.input_tokens`, `usage.output_tokens`, and
  `usage.reasoning_tokens` are present when available

`usage={}` is never token evidence. It is classified as
`worker_usage_empty_sidecar`, keeps `worker_total_tokens=null`, and blocks live
20+ continuation.

The strict-contract comparison harness reads worker token evidence from these
preserved sources:

- `usage_accounting` inside each delegated `zcode-run.json`
- an explicit path in `usage_accounting.worker_usage_source_path`,
  `usage_accounting.token_usage_source_path`,
  `usage_accounting.tokens_source_path`,
  `usage_accounting.provider_usage_ledger_path`,
  `usage_accounting.usage_ledger_path`, or
  `usage_accounting.usage_source_path`
- a task-directory sidecar named `worker-usage.json`,
  `worker-usage.jsonl`, `worker-usage-ledger.json`,
  `worker-usage-ledger.jsonl`, `usage-ledger.json`, or
  `usage-ledger.jsonl`

Sidecar paths must live beside the delegated row's `zcode-run.json`; copy
environment-provided provider ledgers into that task directory before manifest
building. Accepted token source types are `zcode_cli_json_usage`,
`provider_usage_ledger`, `zcode_worker_usage_ledger`, `worker_usage_sidecar`,
`wrapper_json_usage`, or `artifact_sidecar`.

## Goal M Sidecar Writer And Scoping Contract

Generated strict-contract delegated launchers now run a sidecar preservation
hook immediately after `zcodectl run-packet` writes `zcode-run.json`.

The hook supports these environment variables for a compact provider or worker
usage ledger:

- `ZCODE_WORKER_USAGE_SIDECAR`
- `ZCODE_WORKER_USAGE_LEDGER`
- `ZCODE_PROVIDER_USAGE_LEDGER`
- `ZCODE_USAGE_LEDGER`

If one of those variables points to a JSON or JSONL file inside the delegated
row/task directory, the launcher copies that file beside `zcode-run.json` as
`worker-usage.json` or `worker-usage.jsonl`. It then records the stable relative
path in:

`usage_accounting.worker_usage_source_path`

External ledger paths outside the delegated row/task directory are not accepted
as row-scoped token evidence. They are recorded as unavailable with
`worker_usage_source_out_of_scope` so a shared/global ledger cannot be copied
into every task and double-counted.

If no external ledger is provided but `zcode-run.json` already contains
measured `zcode_cli_json_usage`, the hook writes `worker-usage.json` from that
measured token usage and records the same relative reference. If measured token
usage is unavailable, it writes an unavailable sidecar with
`no_usage_reason`; this is explicit evidence of missing worker usage, not a
token count.

Goal N7 hardened the `zcode_cli_json_usage` source parser: ZCode CLI stdout may
be a single JSON object or progress/prose mixed with a JSON usage line. The
wrapper now scans JSON object lines and preserves token fields into
`usage_accounting.tokens_*`, including `reasoning_tokens` when present. This
does not make quota, credit, balance, or percent-only evidence eligible for
token accounting.

Goal N9 hardens the provider-shaped parser contract: ZCode CLI stdout may also
emit OpenAI/Responses-style JSONL such as
`{"type":"response.completed","response":{"usage":{...}}}`. `zcodectl` now
scans nested `response`, `result`, `data`, `message`, and `payload` objects for
measured token fields and writes them to `usage_accounting`. If
`total_tokens` is absent but measured input/prompt and output/completion token
splits are present, `zcodectl` derives the canonical total from those token
splits. It does not derive tokens from quota percent, credits, balances, or
percent-only evidence.

The required provider or wrapper token schema is one of:

```json
{
  "usage": {
    "total_tokens": 42,
    "input_tokens": 31,
    "output_tokens": 11,
    "reasoning_tokens": 5
  }
}
```

or:

```json
{
  "response": {
    "usage": {
      "total_tokens": 42,
      "prompt_tokens": 31,
      "completion_tokens": 11,
      "completion_tokens_details": {
        "reasoning_tokens": 5
      }
    }
  }
}
```

`total_tokens`, `input_tokens`/`prompt_tokens`, and
`output_tokens`/`completion_tokens` may be snake_case or camelCase. The provider
or wrapper should include `provider` and `model` when available. The measured
row is accepted only after the sidecar contract records `source_type`,
`unit=tokens`, and positive `usage.total_tokens` beside `zcode-run.json`.

Goal N8's provider canary still emitted no measured token payload in the saved
evidence: `stdout` and `stderr` were empty, `usage` was `null`,
`usage_normalized` was `null`, `usage_accounting.tokens_source` was `null`, and
`worker-usage.json` contained `usage={}`. That evidence means the real provider
path did not expose token usage to `zcodectl`; it was not a token value that the
sidecar writer converted to zero.

The exact measured sidecar source path contract is:

`<report-dir>/tasks/<task-id>/<delegation-dir>/zcode-run.json`

and sibling:

`<report-dir>/tasks/<task-id>/<delegation-dir>/worker-usage.json`

or:

`<report-dir>/tasks/<task-id>/<delegation-dir>/worker-usage.jsonl`

Before any future live run, run the dry-run preflight and verify all generated
20-task launchers contain the sidecar hook:

```bash
python3 tools/zcode_eval/strict_contract_comparison.py \
  --report-dir artifacts/reports/<preflight-id> \
  --dry-run

python3 -c "from pathlib import Path; root=Path('artifacts/reports/<preflight-id>/tasks'); scripts=sorted(root.glob('*/zcode-delegated/run_zcode_launcher.sh')); print(len(scripts)); assert len(scripts) >= 20; missing=[str(p) for p in scripts if 'worker-usage.json' not in p.read_text() or 'worker_usage_source_path' not in p.read_text()]; assert not missing, missing[:3]"
```

After a future approved canary, inspect the canary artifact before deciding
whether the live 20+ retry is eligible:

```bash
python3 scripts/check_live_retry_preflight.py \
  artifacts/reports/<canary-run-id>/summary.json \
  --json
```

The checker must report `eligible_for_live_20_retry=true` before a live 20+
retry can proceed. `worker_usage_empty_sidecar`, quota/credit/percent-only
usage, provider overload, auth failures, timeouts, production green-path enablement,
or direct-mode defaults all block continuation.

Goal I's preserved successful delegated rows did not contain any of these token
sources: `stdout` and `stderr` were empty, `usage_accounting.tokens_source` was
`null`, `usage_accounting.tokens_used` was `null`, and quota snapshots were
disabled with `--usage-snapshot-source none`. The worker token usage was
therefore truly unavailable, not merely unparsed.

Goal I produced 4 completed delegated rows because the run stopped after the
worker-usage stop rule was reached. The next task had only a `codex_only` row in
`summary.json`, and its delegated `zcode-run.json` was aborted with
`exit_code=143`. The default strict-contract task registry still exposes 20+
task slugs; a 4-task run is only an explicit subset and is not a valid 20+
live-evidence run.

## Goal N2 Provider Overload Blocker

Goal N consumed exactly one approved live direct delegated run and ended blocked
by provider overload. Its local evidence is referenced from:

- `$EVIDENCE_ROOT/goal-n-approved-live-20-direct-run/summary.json`
- `$EVIDENCE_ROOT/goal-n-approved-live-20-direct-run/tasks/policy-reason-contract/zcode-direct-launcher/route.stderr.log`
- `$EVIDENCE_ROOT/goal-n-approved-live-20-direct-run/tasks/policy-reason-contract/zcode-direct-launcher/zcode-run.json`

`ProviderBusinessError` provider code `1305` from provider `zai` is classified
as `provider_overload` with `blocker_kind=infrastructure_blocker`. That
classification must not be counted as strict quality failure, worker token
failure, total workflow savings evidence, or 20+ benchmark evidence.

Provider-overload partial runs must not produce live evidence manifests. The
manifest builder rejects summaries with fewer than 20 task slugs,
`provider_error_kind=provider_overload`, `blocker_kind=infrastructure_blocker`,
or `benchmark_evidence_qualified=false`.

When provider overload occurs:

- keep `usage_available=false` and worker usage unavailable unless measured
  token evidence exists
- keep total workflow savings blocked
- keep rollout readiness `fixture_ready` / `dry_run_only` or blocked
- require fresh Aki approval and provider availability recovery before another
  live retry

## Goal N4 Compatibility And Retry Readiness

Compatibility decision: baseline remains `3.1.2` for now. The release monitor
reported official latest ZCode `3.2.0` on `2026-06-30`, but Goal N4 does not
validate newer ZCode provider routing, plugin/sub-agent behavior, usage
surfaces, or direct launcher compatibility. Keep `config/zcode-release-baseline.json`
at `3.1.2` until separate compatibility work lands with tests.

Next-live-retry checklist:

- provider availability recovered
- fresh Aki approval recorded
- explicit direct mode selected for the run
- direct mode remains non-default
- 20+ strict-contract tasks selected
- per-row worker token sidecar preserved
- no `provider_overload`
- live evidence manifest validation passes
- rollout readiness output generated
- production-ready: false until later explicit approval
- production green-path disabled

Issues #24, #26, #27, and #31 should not be closed automatically by Goal N4.
Use the recommendation table in
`docs/goals/current/zcode-compat-retry-readiness.md`.

## Goal N6 Empty Sidecar Blocker

Goal N5 ran exactly one provider canary and correctly did not proceed to live
20+. The provider path completed without provider overload, auth failure, or
timeout, but the worker token source was unavailable. The preserved sidecar:

```json
{
  "source_type": "worker_usage_sidecar",
  "status": "unavailable",
  "unit": "unknown",
  "usage": {}
}
```

This is classified as `worker_usage_empty_sidecar`. It is not measured token
evidence, does not become zero, and blocks:

- live retry preflight eligibility
- live evidence manifest construction for `live_provider_benchmark`
- rollout readiness progression beyond fixture/dry-run or blocked states

## Total Workflow Savings

Total workflow savings are claimable only when:

- Codex-side usage is measured in tokens
- worker-side usage is measured in tokens
- both sides use compatible token units
- strict and quality results pass
- task count is at least 20
- the claim uses exactly one claim family
- manual approval is still handled separately by rollout readiness

If any required usage layer is unavailable, keep
`total_workflow_savings.status` as `blocked` and include `no_claim_reason`.

## Stop Rules

Stop and report instead of proceeding if:

- Aki has not separately approved live benchmark execution
- provider authentication fails
- the benchmark run is partial
- fewer than 20 tasks run
- strict gates fail
- worker token usage is missing
- usage evidence is quota percent, credits, or percent-only
- total workflow savings would require inventing or converting token counts
- direct-orchestrated and codex-mediated claim families are mixed
- production green-path skip is enabled
- direct mode becomes default
- manual approval is missing for rollout

## Manual Approval Checklist

Manual approval remains pending until Aki explicitly approves rollout after
reviewing live evidence. Approval requires:

- live 20+ provider benchmark manifest validates
- strict and quality results pass
- worker token usage is measured in tokens
- Codex-side usage is measured in tokens
- total workflow savings are measured with compatible units
- production green-path skip is disabled
- direct mode remains explicit opt-in
- remaining blockers are accepted or resolved

Until then, the safe expected state remains `fixture_ready` / `dry_run_only`.
