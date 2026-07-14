# Strict Contract V3 — Contract Packet Specification

## Contract authority

The `task_contract` is the authoritative acceptance standard for ZCode. ZCode's own "pass" is not sufficient.

## Required fields

A task contract should contain:

- `contract_id`
- `task_id`
- `rubric_id`
- `rubric_sha256`
- `risk_level`
- `ambiguity_score`
- `allowed_files`
- `forbidden_files`
- `goal`
- `non_goals`
- `requirements`
- `edge_cases`
- `forbidden_actions`
- `implementation_plan`
- `expected_diff_budget`
- `validation_commands`
- `acceptance_rules`
- `failure_protocol`
- `self_audit_schema_ref`

## Requirement format

Each requirement must have:

```json
{
  "id": "REQ-001",
  "must": "The implementation must preserve the billing cent-rounding contract.",
  "verification": "Validation command passes and changed file evidence exists.",
  "evidence_required": ["changed_file", "validation_result"],
  "blocking": true,
  "negative_checks": ["Do not change tests", "Do not add broad unrelated helper"]
}
```

## Failure protocol

The contract must tell ZCode to return `blocked` rather than guess when any blocking requirement is ambiguous, unverifiable, or impossible under the allowed file scope.

## Diff budget

Contracts should include a soft expected diff budget. Exceeding it should not automatically fail, but it must add a risk flag and require audit.

Example:

```json
{
  "files_changed_max": 1,
  "insertions_soft_max": 10,
  "deletions_soft_max": 10,
  "if_exceeded": "add_risk_flag_and_explain"
}
```

## Green-path acceptance input

Codex should see only the compact trace summary on green path, not full diff/log/stdout/stderr/image data.
