# ZCode/Codex Token Reduction V2 — Experiment Protocol

## Modes

Use canonical modes internally:

- `codex_only`
- `zcode_nested_codex`
- `zcode_direct_launcher`
- `zcode_direct_harness`

Use display modes only in reports:

- `Codex-only`
- `ZCode delegated`

## Experiment arms

| arm | purpose |
| --- | --- |
| `baseline_latest` | reproduce the latest baseline |
| `baseline_plus_logging_only` | verify logging does not regress behavior |
| `manifest_only_acceptance` | stop passing full diff/log/stdout/stderr to Codex |
| `green_path_non_llm_acceptance` | skip Codex acceptance when deterministic gates all pass |
| `zcode_direct_harness_non_llm_planner` | skip Codex planning for well-specified benchmark tasks |
| `slim_automation_profile` | reduce config/rules/profile context after safety is proven |

## Green-path deterministic acceptance

A run can be accepted without Codex only when all are true:

- allowed files only
- validation pass
- diffstat within budget
- artifact manifest valid
- no risk flags
- no secret scan failure
- no path safety violation
- no unexpected changed files
- no unexpected untracked files
- no visible context budget violation

In measurement mode, run shadow Codex audit and record agreement. Do not make this production default until shadow audits show no quality regression.

## Manifest-only acceptance

On pass, Codex sees only:

- task id
- changed file list
- allowed-file check result
- diffstat
- validation result summary
- patch hash
- artifact manifest hash
- risk flags, if any
- raw artifact paths/hashes

Codex must not see full diff, full validation log, ZCode stdout/stderr, or image content on green path.

## Non-LLM planner arm

For benchmark tasks that already define expected behavior, allowed files, and validation commands, generate the ZCode packet deterministically. Call Codex only on anomaly.

This arm is not the default for ambiguous real user tasks.

## Phase-split measurement

Use opt-in phase split only for measurement:

- plan
- acceptance
- validation_audit
- report if LLM-based
- noop_baseline

Production mode should remain compact because phase splitting adds invocation overhead.

## Cache and run order

Record:

- `run_order_index`
- `run_order_seed`
- `cache_condition`
- `previous_run_id`
- `cache_sensitive`

Rotate run order when comparing arms to avoid cache/order bias.

## Success criteria

Primary:

- lower ZCode-delegated Codex-side `effective_codex_work`

Guards:

- quality remains pass
- no larger repair burden
- no manual intervention increase
- no hidden retries
- no raw log/prompt leakage
- default workflow remains no heavier
