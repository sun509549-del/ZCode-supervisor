# ZCode Measurement and Claims Spec

This document defines how to report token savings without mixing incompatible experiment modes.

## Canonical primary metric

The current primary Codex-side token metric is:

```text
effective_codex_work = uncached_input_tokens + output_tokens + reasoning_output_tokens
```

Keep this metric stable unless the repository already has a newer canonical definition. If additional metrics are added, do not replace this one silently.

## Measurement modes

Every comparison row and aggregate claim must include a measurement mode.

| mode | meaning | deployable with old results? |
| --- | --- | --- |
| `codex-mediated` | Codex executes/observes delegated launcher and reports rc/artifact results | yes, comparable to prior 51.64% quality-green claim |
| `direct` | Python harness executes delegated launcher and reads artifacts without Codex orchestration | no, new claim family |
| `measurement-only` | shadow/dry-run/audit mode, not production behavior | no |

Rules:

- Never mix `codex-mediated` and `direct` rows in the same savings numerator/denominator without labeling the aggregate as mixed/experimental.
- Existing default behavior should remain `codex-mediated` until a separate migration decision is made.
- Direct mode may reduce token usage dramatically, but it must be reported as `direct_orchestrated_delegation_savings`, not as an update to the old `codex_mediated_delegation_savings` series.
- Goal E benchmark expansion and 20-task dry-run/fixture-only checks validate
  benchmark breadth only. They do not prove production savings.
- Goal F bounded repair policy metadata is diagnostic/control evidence. Repair
  status must not move a row into a stronger claim scope unless the row also
  passes post-repair strict validation and final strict acceptance.

## Claim scopes

| scope | includes | allowed wording |
| --- | --- | --- |
| `apparent-all` | all rows, including failures | reference-only, not deployable |
| `quality-green` | rows where quality passed under the relevant strict criteria | quality-preserving claim |
| `strict-green` | rows with `strict_accepted=true`, validation pass, and no fail-closed flags | strongest deployable claim |
| `measurement-only` | shadow/dry-run rows | diagnostic only |

Rules:

- If any delegated task fails strict acceptance, all-row savings must be labeled apparent/reference-only.
- Deployable savings must exclude failed rows or be withheld until all rows pass strict quality.
- Latency must be reported separately from token savings.

## Row-level schema additions

Add these fields where practical. If the repo already has equivalent names, use existing names and document the mapping.

```json
{
  "task_id": "policy-reason-contract",
  "measurement_mode": "codex-mediated",
  "claim_scope": "quality-green",
  "quality_passed": true,
  "strict_accepted": true,
  "route_rc": 0,
  "acceptance_rc": 0,
  "final_validation_rc": 0,
  "codex_effective_work": 64806,
  "codex_usage_status": "measured",
  "codex_orchestration_tokens": null,
  "codex_orchestration_usage_status": "unavailable",
  "codex_acceptance_tokens": null,
  "codex_acceptance_usage_status": "unavailable",
  "codex_repair_tokens": null,
  "codex_repair_usage_status": "unavailable",
  "worker_kind": "zcode",
  "worker_provider": "zai",
  "worker_model": "glm-5.2",
  "worker_usage_status": "unavailable",
  "worker_usage_source": "usage_accounting",
  "worker_usage_source_path": "artifacts/reports/.../zcode-run.json",
  "worker_input_tokens": null,
  "worker_output_tokens": null,
  "worker_reasoning_tokens": null,
  "worker_total_tokens": null,
  "worker_usage_unit": "unknown",
  "worker_usage_no_usage_reason": "zcode_cli_usage_missing",
  "worker_usage_capture_method": "usage_accounting",
  "worker_quota_percent_used": null,
  "worker_credits_used": null,
  "zcode_worker_tokens": null,
  "zcode_worker_usage_status": "unavailable",
  "no_usage_reason": "zcode_cli_usage_missing",
  "fresh_run": true,
  "baseline_cache_hit": false,
  "baseline_cache_key": null,
  "strict_failure_classification": [],
  "repair_policy_version": "strict_contract_bounded_repair_policy.v1",
  "repair_policy_enabled": true,
  "repair_attempt_budget": 1,
  "repair_attempts_used": 0,
  "repair_decision": "skipped",
  "repair_blocker": null,
  "repair_reason": "strict_accepted_no_repair_needed",
  "repair_failure_classification": [],
  "repair_size_class": "none",
  "repair_allowed_files": ["src/policy.js"],
  "repair_forbidden_files": ["test", "README.md", "package.json"],
  "repair_changed_files": ["src/policy.js"],
  "repair_changed_lines": 4,
  "repair_stop_reason": "no_repair_needed",
  "post_repair_validation_status": "not_run",
  "post_repair_strict_accepted": false,
  "repair_evidence_paths": ["artifacts/reports/.../workspaces/policy-reason-contract/zcode-delegated"]
}
```

## Bounded repair policy

Goal F records bounded repair decisions without making repair execution the
default path. The default benchmark repair attempt budget is `1`.

Repair may be allowed only for small deterministic issues such as missing
required artifacts, malformed JSON or Markdown output, small schema mismatch,
path/reference normalization, deterministic contract mismatch with a clear
expected fix, or small test repair inside the task workspace.

Repair is blocked for broad refactors, forbidden-file changes, expected fixture
weakening, strict gate weakening, acceptance-criteria weakening,
claim/accounting weakening, unavailable usage as zero, quota/credit/percent
conversion into tokens, hidden network dependencies, secret handling,
production rollout changes, direct mode becoming default, or repeated attempts
after the budget is exhausted.

Repair must preserve the original failure classification and original evidence.
Post-repair strict validation is recorded separately and cannot bypass strict
acceptance. Repaired success remains distinguishable from first-pass success by
repair attempt and post-repair fields.

## Usage unavailable handling

Never report unavailable usage as zero. Use one of these statuses:

```text
measured
unavailable
partial
excluded
not_applicable
```

Recommended reasons:

```text
zcode_cli_usage_missing
codex_exec_phase_usage_missing
strict_summary_has_deterministic_status_only
worker_usage_excluded_by_metric_definition
worker_token_usage_unavailable_quota_percent_only
worker_token_usage_unavailable_credits_only
not_run_due_to_cache_hit
not_applicable_for_dry_run
```

For aggregate reporting:

- A measured Codex-side denominator/numerator may still be valid if ZCode worker tokens are unavailable or excluded by definition.
- Worker usage with `worker_usage_unit: "quota_percent"` or `"credits"` is partial/non-token usage and must not be converted into token usage.
- Cost/ROI estimates must remain unavailable if worker usage is unavailable or non-token only and no reliable price model exists.
- Per-phase token charts must omit unavailable phases or mark them explicitly; do not fill with zeros.

## Baseline reuse reporting

Automatic baseline reuse changes experiment cost, not the theoretical workload savings. Report both.

Recommended aggregate fields:

```json
{
  "fresh_run_codex_only_work": 313352,
  "fresh_run_delegated_codex_work": 132315,
  "baseline_reused_codex_only_work": 313352,
  "cache_adjusted_experiment_codex_work": 132315,
  "baseline_cache_hits": 4,
  "baseline_cache_misses": 0,
  "claim_scope": "quality-green",
  "measurement_mode": "codex-mediated"
}
```

This example is illustrative. Use actual measured/reused values from the run.

## Cache key requirements

Cache keys must include enough to prevent stale or non-comparable baselines:

```text
repo_head
dirty_tree_sha256
task_id
fixture_hash
direct_prompt_hash
system_prompt_hash
allowed_files_hash
validation_command_hash
strict_acceptance_version
model_id
codex_cli_version
harness_version
environment_fingerprint
```

Cache invalidation rules:

- Any changed key component is a miss.
- Missing key component is a miss unless the repo has a stronger compatibility proof.
- Dirty tree mismatch is a miss.
- Strict acceptance version mismatch is a miss.
- Allowed files policy mismatch is a miss.

## Current reviewed claim families

### Codex-mediated delegation

`codex-mediated` is the existing/default comparison mode. Codex runs the
delegated launcher, observes route and artifact outputs, and reports the
delegated result through the existing harness flow.

The current fresh codex-mediated strict-green claim is only the strict-green
subset:

```text
128,376 -> 106,505 effective_codex_work
17.04% Codex-side reduction
claim family: codex_mediated_delegation_savings
```

Do not use old all-row failed results as deployable. In particular, the old
`57.77%` all-row codex-mediated result is not deployable because it included a
strict-failed row.

### Direct orchestrated delegation

`direct` is an opt-in measurement mode:

```text
--delegation-execution direct
```

The operational workflow and role split are documented in
[`docs/direct-delegated-operational-workflow.md`](docs/direct-delegated-operational-workflow.md).
The practical entrypoint is:

```bash
bash scripts/run_direct_delegated_strict_contract.sh \
  --only billing-credit-contract,vision-card-latest \
  --dry-run
```

The expanded Goal E dry run can exercise 20 fixture-backed task slugs without
live providers:

```bash
python3 tools/zcode_eval/strict_contract_comparison.py --dry-run
```

That expanded dry-run/fixture-only result is diagnostic coverage evidence, not
production savings evidence.

In direct mode, the Python harness runs the delegated launcher script with
`subprocess`, then reads the same deterministic artifacts: route rc, acceptance
rc, final-validation rc, `zcode-run.json`, strict summaries, and task-plan
artifacts. The same route, acceptance, final-validation, and strict gates apply.

The current final direct rerun is:

```text
artifacts/reports/20260624-verify-direct-4task-visionfix-rerun2/summary.json
4/4 strict-green
222,962 -> 0 effective_codex_work
100.0% Codex-side delegated-arm reduction
claim family: direct_orchestrated_delegation_savings
```

The `0` in the direct delegated arm means Codex-side work for direct subprocess
launcher mode only. It does not mean total workflow cost is zero, and it does
not mean ZCode worker/model tokens are zero. ZCode worker usage must be
reported through the `worker_*` fields as measured token usage, partial
quota/credit usage, or unavailable/null with a reason.

### End-to-end accounting layers

Goal C adds explicit accounting layers so direct delegated-arm Codex savings
are not mistaken for total savings:

| layer | meaning | deployable when |
| --- | --- | --- |
| `delegated_arm_codex` | Codex work inside the delegated arm only. In direct mode this can be `0` because Python runs the launcher subprocess without a nested Codex exec. | The delegated arm and Codex baseline are measured and quality/strict scope allows a claim. |
| `total_codex_side` | All Codex-side supervisor/orchestration/acceptance/repair work required around the delegated workflow. | Every required Codex-side phase is measured. |
| `total_workflow` | Total Codex-side work plus measured ZCode worker/model token work. | Total Codex-side work and ZCode worker/model usage are measured in token units. |

`summary.json` records these under `end_to_end_accounting` for all rows,
quality-green rows, and strict-green rows. If required usage is missing, the
corresponding total layer must stay `usage_status: "unavailable"`, with
`arm_work: null`, `reduction_percent: null`, and a `no_usage_reason`.

Direct mode may still show:

```text
delegated_arm_codex: 222,962 -> 0 effective_codex_work
```

That is only the direct delegated-arm Codex launcher claim. It is not a total
Codex-side orchestration claim and not a total workflow claim. If worker/model
usage is unavailable, or only quota percent/credits are available, total
workflow token savings remain unavailable.

### Baseline reuse

Reused Codex-only baselines affect experiment cost, not the workload
definition. Reused rows must be marked `fresh_run=false` and
`baseline_cache_hit=true`. Cache keys and invalidation must remain strict:
missing or changed key components are misses.

## Summary wording templates

Use wording like:

```text
Codex-mediated, quality-green subset: X -> Y effective_codex_work, Z% reduction.
```

Use wording like this for fail-containing all-row totals:

```text
All-row apparent reduction: X -> Y, Z%. This is not a deployable claim because TASK failed strict acceptance.
```

Use wording like this for direct mode:

```text
Direct-orchestrated delegated mode: X -> Y effective_codex_work, Z% reduction. This is a new measurement mode and is not directly comparable to prior codex-mediated results.
```

For Goal D total layers, use wording like:

```text
Direct delegated-arm Codex savings are measured, but total workflow savings are unavailable because ZCode worker/model token usage is unavailable or non-token only.
```

## Non-goals

- Do not introduce a single blended “ZCode savings” number that mixes direct and codex-mediated modes.
- Do not infer worker cost from missing usage.
- Do not turn shadow green-path skip into default production behavior.
- Do not weaken strict acceptance in order to improve the savings percentage.
- Do not use repair status to hide an original failure or bypass strict
  acceptance.
- Do not treat Goal E dry-run/fixture-only benchmark breadth as proof of
  production savings.
- Do not implement Goal G rollout readiness as part of a savings-claim update.
