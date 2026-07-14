# Strict Contract V3 Comparison Harness

Tracked runner:

```bash
python3 tools/zcode_eval/strict_contract_comparison.py
```

Goal E expands the harness-visible benchmark registry to 20 runnable task slugs
across multiple benchmark categories. The registry in
`tools/zcode_eval/strict_contract_tasks.py` is the count source of truth; see
`docs/zcode-strict-contract-v3/BENCHMARK_TASKS.md` for the supporting manifest.

Useful targeted run:

```bash
python3 tools/zcode_eval/strict_contract_comparison.py \
  --only billing-credit-contract,vision-card-latest \
  --reuse-codex-baseline-from artifacts/reports/<previous-report>/summary.json
```

Dry run without Codex/ZCode execution:

```bash
python3 tools/zcode_eval/strict_contract_comparison.py --dry-run
```

Direct delegated dry run:

```bash
bash scripts/run_direct_delegated_strict_contract.sh \
  --only billing-credit-contract,vision-card-latest \
  --dry-run
```

Direct delegated four-task comparison:

```bash
python3 tools/zcode_eval/strict_contract_comparison.py \
  --delegation-execution direct \
  --only billing-credit-contract,ledger-summary-contract,policy-reason-contract,vision-card-latest
```

Direct mode remains opt-in; direct mode remains explicit opt-in. It reports
`direct_orchestrated_delegation_savings`, not
`codex_mediated_delegation_savings`.

Expanded dry-run/fixture-only checks validate benchmark breadth and harness
selection only. They do not prove production savings.

The harness writes compact outputs under
`artifacts/reports/<timestamp>__codex__strict-contract-v3-comparison/`:

- `summary.md`
- `summary.json`
- `task-plan.json`
- per-task `route.rc`, `acceptance.rc`, `final-validation.rc`

`summary.json` also includes `end_to_end_accounting` with three separate
layers:

- `delegated_arm_codex_savings`: Codex work inside the delegated arm only.
- `total_codex_side_savings`: all Codex-side supervisor/orchestration,
  acceptance, and repair work required by the workflow.
- `total_workflow_savings`: total Codex-side work plus measured ZCode
  worker/model work.

Direct delegated-arm `0 effective_codex_work` belongs only to
`delegated_arm_codex_savings`. It is not a total Codex-side or total workflow
claim. Total layers remain `unavailable` with null token fields when required
usage is missing.

Goal F adds a bounded repair policy to each row and to `summary.json` under
`repair_policy`. The default repair attempt budget for benchmark rows is `1`.
Repair decisions are recorded as `skipped`, `allowed`, `attempted`, or
`blocked`, with blocker, reason, failure classification, size class,
allowed/forbidden files, changed files/lines, stop reason, evidence paths, and
post-repair validation fields.

Repair does not weaken acceptance. A failed run is not acceptable unless
post-repair strict validation passes, and the original failure classification
remains visible. Repaired success is distinguishable from first-pass success by
`repair_attempts_used`, `post_repair_validation_status`, and
`post_repair_strict_accepted`.

## Rollout Readiness

Goal G adds a conservative readiness command:

```bash
bash scripts/check_rollout_readiness.sh
```

It writes machine-readable readiness evidence and keeps production rollout
blocked unless live 20+ provider benchmark evidence, measured worker token
usage, total workflow savings availability, bounded repair policy, and manual
approval are all present. Dry-run and fixture evidence remain non-production
evidence. Direct mode remains explicit opt-in, and production green-path skip
remains disabled.

See [Rollout Readiness](ROLLOUT_READINESS.md).

## Durable Conditions

- Primary metric:
  `effective_codex_work = uncached_input_tokens + output_tokens + reasoning_output_tokens`
- ZCode worker/model tokens are excluded.
- Missing usage is `unavailable`, not zero.
- Quality gates remain fail-closed: token reduction does not override failed
  validation, scope, timeout, artifact acceptance, or strict acceptance.
- Repair status never overrides strict acceptance or hides original failure
  evidence.
- Production defaults are not changed. This runner is opt-in.
- Production green-path skip remains disabled.
- Rollout readiness is conservative and auditable.

The follow-up lessons from the 2026-06-23 artifacts are now encoded in the
tracked task plan:

- `billing-credit-contract` uses a `600000ms` ZCode timeout and explicitly asks
  the worker to write strict self-audit before final response.
- `vision-card-latest` uses deterministic color samples:
  `accentColor=#2563EB` and `statusColor=#16A34A`.
- `vision-card-latest` is treated as a bounded `small-fix` task so it reaches
  strict self-audit reliably.
