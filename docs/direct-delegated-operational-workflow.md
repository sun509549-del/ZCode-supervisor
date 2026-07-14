# Direct Delegated Operational Workflow

Direct delegated mode is an opt-in operational path for the Strict Contract
harness. It keeps Codex in charge of judgment while ZCode performs bounded
implementation.

## Operational Entrypoint

Use the wrapper when you want the shortest direct-mode command:

```bash
bash scripts/run_direct_delegated_strict_contract.sh \
  --only billing-credit-contract,vision-card-latest \
  --dry-run
```

The wrapper always calls the existing harness with
`--delegation-execution direct`. It rejects a user-supplied
`--delegation-execution` flag so direct and codex-mediated runs cannot be
accidentally blended through the wrapper. All other harness defaults remain in
the harness.

Run the four-task direct strict-contract comparison with:

```bash
bash scripts/run_direct_delegated_strict_contract.sh \
  --only billing-credit-contract,ledger-summary-contract,policy-reason-contract,vision-card-latest
```

Use the harness directly when you want to see or script every flag yourself:

```bash
python3 tools/zcode_eval/strict_contract_comparison.py \
  --delegation-execution direct \
  --only billing-credit-contract,ledger-summary-contract,policy-reason-contract,vision-card-latest
```

For a no-spend scaffold check:

```bash
python3 tools/zcode_eval/strict_contract_comparison.py \
  --delegation-execution direct \
  --only billing-credit-contract,vision-card-latest \
  --dry-run
```

## Role Split

Codex owns:

- planning
- task packet/spec creation
- acceptance inspection
- repair decision
- final report

ZCode owns:

- bounded implementation inside the task packet

Codex should inspect route status, acceptance status, final validation,
strict summaries, changed files, and usage provenance before making any final
claim. If a row fails, Codex decides whether to narrow the packet, request a
bounded repair, or fall back. This document does not enable a large repair loop.

## Post-Merge Direct Evidence

PR #10 merged direct delegated measurement mode into `main` at:

```text
c83f409c3de0f31119a6be8c2b18a423802fbab7
```

The current post-merge evidence artifact is:

```text
artifacts/reports/20260624-verify-direct-4task-visionfix-rerun2/summary.json
```

The tracked redacted summary used for clean-checkout verification is:

```text
docs/goals/current/post-merge-direct-evidence.json
```

Recorded result:

```text
4/4 strict-green
claim family: direct_orchestrated_delegation_savings
222,962 -> 0 effective_codex_work
100.0% Codex-side delegated-arm reduction
```

Claim boundaries:

- This is direct mode only.
- This is not `codex_mediated_delegation_savings`.
- `effective_codex_work` remains
  `uncached_input_tokens + output_tokens + reasoning_output_tokens`.
- This is a Codex-side delegated-arm reduction only.
- This is not a total workflow cost claim.
- `0` in the direct delegated arm means Codex-side delegated launcher work only.
- `0` does not mean total Codex-side orchestration cost is zero.
- `0` does not mean total workflow cost is zero.
- `0` does not mean ZCode worker/model usage is zero.
- ZCode worker usage is reported through `worker_*` fields as measured token
  usage, partial quota/credit usage, or unavailable/null with
  `worker_usage_no_usage_reason`.
- Direct and codex-mediated rows must not be blended into one deployable claim.
- Production green-path skip remains disabled.
- End-to-end Codex token reduction is not measured until Goal C.
- Total workflow token savings require measured worker token usage; quota
  percent or credits alone do not count as token usage.

## How To Confirm The Run

- Direct mode was used if `summary.json` has `measurement_mode: "direct"` and
  direct rows use `claim_family: "direct_orchestrated_delegation_savings"`.
- Strict gates ran if each delegated row has `strict_accepted: true` and the
  report includes route, acceptance, final-validation, and strict summary
  artifacts.
- Direct claims are clean only when `mixed_measurement_modes` is false for the
  direct claim family and no `codex_mediated_delegation_savings` rows are folded
  into the same deployable claim.
- Goal C accounting is present when `summary.json` includes
  `end_to_end_accounting`. In that section, `delegated_arm_codex_savings` is
  separate from `total_codex_side_savings` and `total_workflow_savings`.
- Total Codex-side or total workflow savings are claimable only when their
  layer has `usage_status: "measured"` and `deployable_claim_allowed: true`.
  If usage is missing, the layer must keep `arm_work: null`,
  `reduction_percent: null`, and a `no_usage_reason`.
- Worker comparison is visible through `worker_kind`, which currently
  distinguishes `zcode`, `codex`, and `unknown` rows.

## Operational Checklist

1. Confirm the branch is based on current `main`.
2. Choose direct mode explicitly with `--delegation-execution direct`.
3. Keep the task packet bounded to allowed files and validation commands.
4. Let ZCode perform implementation only inside the packet.
5. Let Codex inspect acceptance, strict summaries, final validation, changed
   files, and usage status.
6. Report direct-mode claims under `direct_orchestrated_delegation_savings`.
7. Keep unavailable ZCode worker/model usage as unavailable with a reason.
8. Keep delegated-arm Codex savings separate from total Codex-side and total
   workflow savings.

## Remaining Roadmap

- Goal E: benchmark expansion to 20+ tasks.
- Goal F: bounded repair policy.
- Goal G: opt-in rollout readiness.
