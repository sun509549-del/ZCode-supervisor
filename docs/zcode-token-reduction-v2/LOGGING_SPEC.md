# ZCode/Codex Token Reduction V2 — Logging Specification

## Ledgers

Keep the existing usage ledger. Add a context-intake ledger that explains why usage happened.

Usage ledger answers:

```text
How many Codex tokens were used?
```

Context-intake ledger answers:

```text
What bytes were visible to Codex before and during that invocation?
```

## Join keys

Every context-intake record must be joinable with the usage ledger by:

- `experiment_id`
- `task_id`
- `canonical_mode`
- `mode_detail`
- `phase`
- `attempt_index`
- `run_id`

## Required metric fields

```json
{
  "primary_metric": "effective_codex_work",
  "primary_metric_formula": "uncached_input_tokens + output_tokens + reasoning_output_tokens",
  "metric_formula_version": "codex_usage_metrics.v1",
  "usage": {
    "input_tokens": 0,
    "cached_input_tokens": 0,
    "uncached_input_tokens": 0,
    "output_tokens": 0,
    "reasoning_output_tokens": 0,
    "uncached_plus_reasoning": 0,
    "effective_codex_work": 0,
    "usage_missing": false
  }
}
```

## Clean benchmark vs operator total

Record both:

| field | meaning |
| --- | --- |
| `benchmark_clean_usage` | valid accepted benchmark runs only |
| `operator_total_usage` | all attempts, including invalid harness bugs, retries, timeouts |
| `excluded_attempt_usage` | usage excluded from clean benchmark |
| `exclusion_reason` | why the attempt was excluded |
| `invalid_measurement` | whether the attempt should not count as a benchmark datapoint |

## Context intake layers

For logs, diff, validation, and tool output, record three layers:

| layer | meaning |
| --- | --- |
| `captured_bytes_total` | raw artifact/log size saved outside the model context |
| `summarized_bytes` | amount read by deterministic summarizer/parser |
| `model_visible_bytes` | bytes actually sent to Codex |

## Initial invocation intake

Record:

- prompt/template/stdin bytes and sha256
- argv structure
- prompt source: `argument | stdin | file | unknown`
- image arg style: `none | repeated_flags | comma_list | mixed | unknown`
- image count and total bytes
- task packet bytes
- repo hint bytes shown
- AGENTS/rules/config bytes shown
- output schema path/hash
- CLI/profile/sandbox/model metadata

Do not store raw prompt text.

## In-turn tool-output intake

Parse Codex JSONL item events and record likely model-visible tool output:

```json
{
  "command_count": 0,
  "command_stdout_bytes_total": 0,
  "command_stderr_bytes_total": 0,
  "command_stdout_bytes_visible_to_model": 0,
  "command_stderr_bytes_visible_to_model": 0,
  "large_command_output_count": 0,
  "largest_command_output_bytes": 0,
  "file_change_event_count": 0,
  "plan_update_event_count": 0,
  "web_search_event_count": 0,
  "mcp_tool_event_count": 0,
  "green_path_forbidden_command_count": 0,
  "green_path_forbidden_commands": []
}
```

Unknown JSONL item shapes must not crash parsing. Record unknown counts.

## Raw JSONL safety

Treat raw Codex JSONL as sensitive:

```json
{
  "raw_jsonl_sensitivity": "sensitive",
  "raw_jsonl_file_mode": "0600",
  "raw_jsonl_gitignored": true,
  "raw_jsonl_retention_days": 14,
  "raw_jsonl_secret_scan_result": "pass|fail|skipped|unknown"
}
```

## Budget guard

Suggested section budgets:

```json
{
  "prompt_max_bytes": 12000,
  "task_packet_max_bytes": 6000,
  "zcode_manifest_max_bytes": 4000,
  "diff_excerpt_max_bytes": 2048,
  "validation_excerpt_max_bytes": 4096,
  "command_output_max_bytes": 4096,
  "image_resend_allowed": false
}
```

On green path, over-budget content should fail closed or route to anomaly audit. It must not silently enter Codex context.
