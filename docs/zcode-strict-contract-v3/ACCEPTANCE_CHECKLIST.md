# Strict Contract V3 — Acceptance Checklist

## Correctness

- [ ] `effective_codex_work` is primary metric.
- [ ] `uncached_plus_reasoning` is secondary only.
- [ ] Missing usage is unavailable, not zero.
- [ ] ZCode worker/model tokens are excluded.

## Contract packet

- [ ] `task_contract` schema exists.
- [ ] Requirement IDs are stable.
- [ ] Requirements include verification and evidence.
- [ ] Forbidden actions are represented.
- [ ] Expected diff budget is represented.
- [ ] Failure/blocked protocol is represented.
- [ ] Contract is included in ZCode packet.

## Rubrics

- [ ] Seed rubrics exist for billing, ledger, policy, vision.
- [ ] Non-LLM expansion works.
- [ ] Rubric SHA is recorded.
- [ ] Risk-level detail policy is tested.

## ZCode self-audit

- [ ] Self-audit schema exists.
- [ ] Requirement trace matrix is required.
- [ ] Evidence is required for satisfied requirements.
- [ ] Missing evidence fails deterministic acceptance.
- [ ] Blocked status is handled safely.
- [ ] Deviations and risk flags trigger audit.

## Acceptance

- [ ] Manifest-only acceptance uses trace summary.
- [ ] Green path does not pass full diff/log/stdout/stderr/image to Codex.
- [ ] Green-path non-LLM acceptance is opt-in.
- [ ] Shadow Codex audit is recorded in measurement mode.
- [ ] Production default is not changed prematurely.

## Ledgers

- [ ] Contract ledger exists.
- [ ] Trace coverage ledger exists.
- [ ] Acceptance audit ledger exists.
- [ ] Contract ROI ledger exists.
- [ ] Estimates are not reported as measured facts.

## UX and safety

- [ ] Default workflow is not heavier.
- [ ] Aki does not need to inspect raw logs.
- [ ] Raw prompts/logs/JSONL are not stored in normal ledgers.
- [ ] Artifact paths are path-safe.
- [ ] Quality gates remain fail-closed.

## Tests

- [ ] Unit tests pass.
- [ ] Existing zcode_eval tests pass.
- [ ] Hard benchmark fixture checks pass.
- [ ] `git diff --check` passes.
