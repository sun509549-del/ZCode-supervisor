# Strict Contract V3 — Rubric Library Specification

## Goal

Avoid spending Codex tokens on long repeated contracts by storing reusable rubrics and expanding them deterministically.

## Seed rubrics

Start with benchmark rubrics:

- `billing_cent_rounding.v1`
- `ledger_summary_state_aggregation.v1`
- `policy_exact_label_routing.v1`
- `vision_card_layout_match.v1`

## Rubric expansion

Input:

- `rubric_id`
- `task_id`
- allowed files
- validation commands
- task overrides
- risk level

Output:

- complete `task_contract.json`
- `rubric_sha256`
- contract visible byte counts
- self-audit schema reference

## Risk levels

| level | use case | detail |
| --- | --- | --- |
| L0 | fully specified benchmark | rubric id plus overrides |
| L1 | simple single-file fix | compact contract |
| L2 | billing/policy/security/precision | detailed strict contract |
| L3 | ambiguous larger task | Codex planning retained |
| L4 | high risk with weak validation | no default delegation |

## Drift control

Record:

- rubric id
- rubric version
- rubric sha256
- last shadow audit result
- known failures
