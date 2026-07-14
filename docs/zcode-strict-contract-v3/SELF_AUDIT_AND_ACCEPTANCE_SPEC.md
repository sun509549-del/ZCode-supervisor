# Strict Contract V3 — ZCode Self-Audit and Acceptance

## Principle

ZCode self-pass is not trusted by itself. The required artifact is a requirement trace matrix with evidence.

## Required self-audit output

ZCode should return:

```json
{
  "schema_version": "zcode_self_audit.v1",
  "contract_id": "billing-credit-contract@v1",
  "task_id": "billing-credit-contract",
  "overall_status": "pass",
  "requirements": [
    {
      "id": "REQ-001",
      "status": "satisfied",
      "evidence": [
        {
          "type": "validation_result",
          "ref": "validation_manifest.json",
          "summary": "validation passed"
        }
      ]
    }
  ],
  "edge_cases": [],
  "deviations_from_plan": [],
  "unresolved_questions": [],
  "risk_flags": [],
  "blocked_reasons": []
}
```

## Deterministic checks

The harness must check:

- all blocking requirements have evidence
- no requirement is merely claimed without evidence
- validation evidence matches actual validation result
- changed files are allowed
- no forbidden files or path escapes
- diff budget is met or risk-flagged
- deviations trigger audit
- risk flags trigger audit
- blocked status cannot also claim pass

## Acceptance modes

| mode | when |
| --- | --- |
| `manifest_only` | Codex sees compact manifest/trace summary |
| `green_path_skip` | deterministic gates all pass; Codex skipped |
| `shadow_codex_audit` | measurement-only audit of green-path skip |
| `codex_anomaly_audit` | risk/failure/deviation/blocked case |
