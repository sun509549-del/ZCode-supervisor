# Strict Contract V3 — Experiment Protocol

## Arms

Run or scaffold these arms:

| arm | purpose |
| --- | --- |
| `baseline_latest` | reproduce current result |
| `logging_only` | prove logging has no behavior regression |
| `contract_packet_normal_acceptance` | measure strict spec effect |
| `contract_packet_manifest_acceptance` | reduce green-path context |
| `rubric_expanded_manifest_acceptance` | reduce contract-generation token |
| `green_path_non_llm_acceptance_shadow` | test skipping Codex acceptance safely |
| `deterministic_planner_benchmark` | skip Codex planning for well-specified benchmark tasks |

## Phase-split measurement

Use separate phases only in measurement mode:

1. `contract_generation`
2. `zcode_launch`
3. `zcode_self_audit_parse`
4. `deterministic_acceptance`
5. `codex_acceptance_audit`
6. `shadow_codex_audit`
7. `summary_render`

Only Codex phases get Codex usage. Non-LLM phases record wall-clock and bytes.

## Success criteria

Primary:

- Lower ZCode-delegated Codex-side effective tokens.

Quality guards:

- final validation pass
- allowed files only
- artifact quality pass
- no hidden repair
- no hidden retry
- no missing usage treated as zero
- shadow audit agreement before production green-path skip

## Task-specific expectation

- Billing should benefit most from strict L2 contract.
- Vision should benefit from no image resend and visual rubric.
- Policy should benefit from exact label/routing requirements.
- Ledger should stay lightweight; avoid over-contracting.
