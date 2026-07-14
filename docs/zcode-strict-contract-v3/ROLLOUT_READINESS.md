# Rollout Readiness

Goal G adds a conservative readiness gate for the strict-contract direct
delegation system. It tells maintainers whether direct delegated mode is
blocked, fixture-ready, candidate-ready, or production-ready without enabling
production behavior.

Run:

```bash
bash scripts/check_rollout_readiness.sh
```

By default this writes:

```text
artifacts/reports/rollout-readiness/latest.json
```

For CI or local inspection:

```bash
bash scripts/check_rollout_readiness.sh \
  --output .local/rollout-readiness.json \
  --json
```

To attach live evidence after separate Aki approval, validate a manifest first:

```bash
python3 scripts/check_live_evidence_manifest.py \
  artifacts/reports/<run-id>/live-evidence-manifest.json \
  --json
```

Then pass it into readiness:

```bash
bash scripts/check_rollout_readiness.sh \
  --live-evidence-manifest artifacts/reports/<run-id>/live-evidence-manifest.json \
  --output artifacts/reports/rollout-readiness/<run-id>.json \
  --json
```

Goal H documents the live evidence process in
[Live Evidence Collection](LIVE_EVIDENCE_COLLECTION.md). The expensive live 20+
provider benchmark must not be run unless Aki separately approves it.

## Readiness States

- `blocked`: a fatal guard failed, such as direct mode becoming default,
  production green-path skip being enabled, benchmark count below 20, strict
  gates weakened, claim boundaries weakened, or bounded repair policy missing.
- `fixture_ready`: dry-run and fixture evidence can be inspected, but
  production rollout remains blocked.
- `candidate`: live 20+ benchmark evidence, worker token usage, and total
  workflow savings are measured, but manual approval is still pending.
- `ready`: all required evidence is present and manual approval is recorded.

`ready` does not enable any production path by itself.

## Required Production Evidence

Production rollout remains blocked unless all required checks pass:

- benchmark task count is at least 20
- bounded repair policy is present and enabled
- direct mode remains explicit opt-in
- production green-path skip remains disabled
- strict gates remain intact
- claim boundaries remain protected
- live 20+ provider benchmark evidence is present
- delegated worker usage is measured in tokens
- total workflow savings are measured
- explicit maintainer approval is recorded

## Claim Boundaries

Dry-run and fixture evidence are not live production evidence. They must not be
used to claim production savings.

Unavailable usage is not zero. Quota percent and credits are not token counts.
Total workflow savings stay blocked unless delegated worker token usage is
measured. Direct and codex-mediated claim families remain separate.

Live evidence must preserve the claim family as either
`direct_orchestrated_delegation_savings` or
`codex_mediated_delegation_savings`. Direct-orchestrated and codex-mediated
claims must not be mixed in one deployable savings claim.

## Manual Approval

Manual approval remains pending until Aki explicitly approves rollout after
reviewing live evidence. A draft PR, skipped review, fixture evidence, or
dry-run evidence does not count as approval.

## Defaults

Direct mode remains explicit opt-in through:

```bash
bash scripts/run_direct_delegated_strict_contract.sh
```

The default harness behavior remains codex-mediated. Production green-path skip
remains disabled.

## Rollback Plan

- Keep direct mode behind the explicit wrapper and `--delegation-execution
  direct`.
- Do not enable production green-path skip.
- Revert the readiness command and docs if any readiness guard fails.
