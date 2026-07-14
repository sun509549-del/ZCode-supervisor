# ZCode/Codex Token Reduction V2 — Implementation Brief

## Purpose

Reduce Codex-side token usage in the ZCode delegated path while preserving quality and keeping Aki's normal workflow lightweight.

The current benchmark already shows useful savings, but the remaining cost is dominated by uncached input and orchestration context. The next improvement should therefore reduce what Codex sees, not remove quality gates.

## Baseline

Latest comparison:

| item | value |
| --- | --- |
| branch | `readme-fresh-clone-zcode-v2` |
| head | `5e02b4c33cfa` |
| Codex CLI | `codex-cli 0.140.0` |
| ZCode app | `3.1.2` |
| ZCode CLI | `0.14.8` |
| quality | Codex-only 4/4 pass, ZCode delegated 4/4 pass |
| Codex-only tokens | 278,601 |
| ZCode delegated Codex-side tokens | 180,776 |
| reduction | 35.11% |
| Codex-only time | 466.35s |
| ZCode delegated time | 618.356s |
| time ratio | 1.326x slower for ZCode delegated |

Largest remaining ZCode-delegated Codex-side consumers:

| task | tokens | next target |
| --- | ---: | ---: |
| `billing-credit-contract` | 83,939 | <= 50,000 |
| `vision-card-latest` | 53,530 | <= 35,000 |
| `policy-reason-contract` | 30,034 | <= 20,000 |
| `ledger-summary-contract` | 13,273 | <= 10,000 |

## Immediate P0 correction

The latest report labels the token metric as `uncached_plus_reasoning`, but totals match:

```text
effective_codex_work = uncached_input_tokens + output_tokens + reasoning_output_tokens
```

Fix the report/ledger to record `primary_metric`, `primary_metric_formula`, and `metric_formula_version`.

## Strategy

1. Observe exactly what bytes Codex sees.
2. Stop sending full diff/log/stdout/stderr/image data to Codex on green paths.
3. Move pass/fail acceptance to deterministic gates when safe.
4. Use Codex only for anomalies, shadow audit, and tasks that actually require judgment.
5. Keep default workflow compact and make detailed measurement opt-in.

## Non-regression rules

- Never weaken quality gates.
- Never hide failed or invalid attempts.
- Never treat unavailable values as zero.
- Never store raw prompts or secrets in ledgers.
- Never make Aki read raw JSONL/logs for normal usage.
- Never make default workflow heavier to support experiments.
