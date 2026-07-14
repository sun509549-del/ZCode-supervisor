# ZCode/Codex Token Reduction V2 — Acceptance Checklist

Use this after Codex implements the changes.

## P0 correctness

- [ ] Report metric label matches formula.
- [ ] `effective_codex_work = uncached_input + output + reasoning`.
- [ ] `uncached_plus_reasoning` remains available only as secondary metric.
- [ ] Missing/unavailable values are not recorded as zero.
- [ ] Clean benchmark totals are separate from operator totals.
- [ ] Invalid/rerun attempts remain visible.

## Logging

- [ ] Context-intake ledger exists.
- [ ] Usage ledger and context-intake ledger join on run ids and metadata.
- [ ] Prompt/stdin bytes and hashes are recorded without raw prompt persistence.
- [ ] Diff/log/validation have captured/summarized/model-visible byte layers.
- [ ] In-turn command output bytes are parsed from Codex JSONL where possible.
- [ ] Unknown/malformed JSONL events do not crash the parser.
- [ ] Raw JSONL is treated as sensitive and gitignored.

## Token reduction

- [ ] Manifest-only acceptance is implemented for green pass path.
- [ ] Green path does not show full diff to Codex.
- [ ] Green path does not show full validation logs to Codex.
- [ ] Green path does not show ZCode stdout/stderr to Codex.
- [ ] Green path does not resend images to Codex acceptance.
- [ ] Non-LLM summary generation exists.
- [ ] Codex final implementation response is short.

## Experimental arms

- [ ] `baseline_plus_logging_only` exists.
- [ ] `manifest_only_acceptance` exists.
- [ ] `green_path_non_llm_acceptance` exists as opt-in.
- [ ] Shadow Codex audit exists for green-path non-LLM acceptance in measurement mode.
- [ ] `zcode_direct_harness` exists or is clearly scaffolded.
- [ ] Default workflow did not change unless explicitly enabled.

## Safety

- [ ] Quality gates are not weakened.
- [ ] Env allowlist is used for launcher/validation where relevant.
- [ ] Secret scan result is recorded.
- [ ] Path traversal and symlink safety are checked for artifact paths.
- [ ] Raw logs are not included in summary.
- [ ] Prompt injection boundary is included when manifest/log/diff data is presented to Codex.

## UX

- [ ] Aki does not need to read raw JSONL/logs.
- [ ] Manual intervention count is recorded.
- [ ] Wall-clock breakdown is recorded.
- [ ] Routing metadata records urgent/background suitability.
- [ ] Summary has a clear next optimization target.

## Tests

- [ ] Unit tests pass.
- [ ] Existing zcode_eval tests pass.
- [ ] Hard benchmark fixture check passes.
- [ ] `git diff --check` passes.
