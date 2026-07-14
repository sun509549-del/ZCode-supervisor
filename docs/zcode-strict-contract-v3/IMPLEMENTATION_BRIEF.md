# Strict Contract V3 — Implementation Brief

## Purpose

ZCode delegation is already saving Codex-side tokens, but the remaining cost is still high in billing and vision. The next improvement is not to trust ZCode more blindly. It is to make the Codex-defined acceptance criteria explicit before ZCode starts, then make ZCode return evidence against those criteria.

## Current data

- Codex-only: 278,601 effective Codex tokens
- ZCode delegated: 180,776 Codex-side effective tokens
- Reduction: 35.11%
- Quality: 4/4 pass on both arms
- Delegated rows: `codex_repair_size=none`
- ZCode path: 1.326x slower overall

Largest remaining delegated Codex-side consumers:

| task | tokens |
| --- | ---: |
| billing-credit-contract | 83,939 |
| vision-card-latest | 53,530 |
| policy-reason-contract | 30,034 |
| ledger-summary-contract | 13,273 |

## Core strategy

Codex should spend a small, measured amount of token up front to define the strict contract, and then avoid spending large tokens later on full diff/log/code review.

```text
Codex strict contract -> ZCode implementation + self-audit -> deterministic acceptance -> Codex audit only on anomaly/shadow sample
```

## Why this is needed

ZCode may otherwise accept work according to its own model's 기준. That can force Codex to perform a heavy final review or repair. A contract packet makes Codex's criteria authoritative and lets the harness check trace coverage mechanically.

## Avoiding token blow-up

Do not generate long bespoke contracts every run. Use:

- rubric library
- compact `rubric_id + overrides`
- non-LLM rubric expansion
- risk-based detail levels
- structured JSON outputs

## Non-regression

- Quality gates stay intact.
- Green-path skip starts as measurement-only with shadow audit.
- Missing usage remains unavailable, not zero.
- Raw prompts/logs/secrets are not stored in ledgers.
