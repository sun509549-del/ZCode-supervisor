# Strict Contract V3 — Token Ledger and Contract ROI

## Metrics

Primary metric:

```text
effective_codex_work = uncached_input_tokens + output_tokens + reasoning_output_tokens
```

Secondary metrics:

- raw input
- cached input
- uncached input
- output
- reasoning
- uncached plus reasoning

## Contract ledger

Record:

- contract id
- task id
- rubric id and sha
- risk level
- ambiguity score
- Codex generation run id, if any
- generation usage
- codex-visible contract bytes
- zcode-visible contract bytes
- whether expanded by non-LLM
- requirement counts
- reuse status

## Trace coverage ledger

Record:

- requirements total
- blocking requirements total
- requirements claimed satisfied
- requirements with evidence
- edge cases total
- edge cases with evidence
- deviation count
- unresolved question count
- risk flag count
- blocked status

## Acceptance audit ledger

Record:

- acceptance mode
- codex-visible bytes
- whether full diff/log was read
- requirements checked
- trace matrix checked
- repair size

## Contract ROI ledger

Record the investment and payoff:

```json
{
  "added_spec_effective_tokens": 0,
  "acceptance_tokens_saved_estimate": null,
  "repair_tokens_saved_estimate": null,
  "retry_tokens_saved_estimate": null,
  "net_codex_token_delta_estimate": null,
  "estimate_method": "baseline_comparison|unavailable"
}
```

Do not represent estimates as measured facts.

## Total Workflow Savings Claimability

Total workflow savings are claimable only when Codex-side usage and worker-side
usage are both measured in compatible token units. Unavailable usage is not
zero. Quota percent and credits are not token counts, and percent-only evidence
must remain non-token evidence with an explicit `no_usage_reason`.

Keep direct-orchestrated and codex-mediated claim families separate:

- `direct_orchestrated_delegation_savings`
- `codex_mediated_delegation_savings`

Do not mix these families in one deployable savings claim.
