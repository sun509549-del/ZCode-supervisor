# Strict-Contract Benchmark Tasks

Goal E expands the strict-contract benchmark suite from 4 tasks to 20 runnable
task slugs.

This page is supporting documentation. The harness-visible registry in
`tools/zcode_eval/strict_contract_tasks.py` is the task-count source of truth.

## Claim Boundary

The expanded suite is dry-run/fixture-only evidence unless a future live
comparison is explicitly executed and verified. It does not prove production savings.

Direct mode remains explicit opt-in. Automated claim check phrase: direct mode remains explicit opt-in. Default behavior remains codex-mediated.
Direct and codex-mediated claim families must not be mixed. Worker token usage
and quota/credit/percent usage are not interchangeable, and total workflow
savings stay blocked unless worker token usage is measured and strict gates
pass.

Goal F bounded repair policy and Goal G rollout readiness remain future work.

## Benchmark Categories

The expanded benchmark categories are:

- `billing_credit_contract`
- `cli_argument_behavior`
- `data_extraction`
- `date_time_windowing`
- `deterministic_failure_detection`
- `file_path_normalization`
- `json_schema_compliance`
- `ledger_numeric_consistency`
- `markdown_documentation_transformation`
- `missing_input_handling`
- `policy_reasoning`
- `refusal_safety_boundary`
- `small_code_edit`
- `strict_artifact_acceptance`
- `test_failure_repair`
- `vision_card_multimodal_adjacent`

## Task Manifest

| Slug | Category | Fixture | Dry-run safe | Future live comparison |
|---|---|---|---|---|
| `billing-credit-contract` | `billing_credit_contract` | hard fixture | yes | yes |
| `policy-reason-contract` | `policy_reasoning` | hard fixture | yes | yes |
| `ledger-summary-contract` | `ledger_numeric_consistency` | repo fixture | yes | yes |
| `vision-card-latest` | `vision_card_multimodal_adjacent` | generated vision fixture | yes | yes |
| `path-normalization-contract` | `file_path_normalization` | generated JS fixture | yes | yes |
| `json-schema-contract` | `json_schema_compliance` | generated JS fixture | yes | yes |
| `markdown-table-contract` | `markdown_documentation_transformation` | generated JS fixture | yes | yes |
| `small-code-edit-contract` | `small_code_edit` | generated JS fixture | yes | yes |
| `test-repair-contract` | `test_failure_repair` | generated JS fixture | yes | yes |
| `data-extraction-contract` | `data_extraction` | generated JS fixture | yes | yes |
| `cli-argument-contract` | `cli_argument_behavior` | generated JS fixture | yes | yes |
| `refusal-safety-contract` | `refusal_safety_boundary` | generated JS fixture | yes | yes |
| `missing-input-contract` | `missing_input_handling` | generated JS fixture | yes | yes |
| `deterministic-failure-contract` | `deterministic_failure_detection` | generated JS fixture | yes | yes |
| `artifact-acceptance-contract` | `strict_artifact_acceptance` | generated JS fixture | yes | yes |
| `currency-format-contract` | `ledger_numeric_consistency` | generated JS fixture | yes | yes |
| `yaml-frontmatter-contract` | `markdown_documentation_transformation` | generated JS fixture | yes | yes |
| `dedupe-records-contract` | `data_extraction` | generated JS fixture | yes | yes |
| `timezone-window-contract` | `date_time_windowing` | generated JS fixture | yes | yes |
| `error-message-contract` | `missing_input_handling` | generated JS fixture | yes | yes |

Hard fixtures are only:

- `billing-credit-contract`
- `policy-reason-contract`

The hard fixture check intentionally expects those two fixtures to fail with
deterministic contract evidence before a worker attempts them.

## Safe Dry Run

Run all expanded tasks without Codex/ZCode provider execution:

```bash
python3 tools/zcode_eval/strict_contract_comparison.py --dry-run
```

Run the core four direct-mode dry run:

```bash
python3 tools/zcode_eval/strict_contract_comparison.py \
  --delegation-execution direct \
  --only billing-credit-contract,ledger-summary-contract,policy-reason-contract,vision-card-latest \
  --dry-run
```

The expanded dry run writes `task-plan.json`, local fixture workspaces, and
launcher scripts. It does not run live providers and must not be reported as a
production savings result.
