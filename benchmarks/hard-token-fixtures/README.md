# Hard Token-Saving Benchmark Fixtures

These fixtures are intentionally unsolved benchmark tasks. They exist so Codex
and ZCode can compare complex implementation routes against deterministic
acceptance contracts.

Do not treat the fixture tests as part of the repository's normal green suite.
The harness in `scripts/check_hard_benchmark_fixtures.sh` expects the current
fixtures to fail, proving they still represent benchmark work.

## Acceptance Rules

- A benchmark row must record Codex token usage as `measured`, `estimated`, or
  `unavailable`.
- Missing usage is never zero.
- A delegated row passes only when the fixture test passes, ZCode scope audit
  passes, and ZCode validation passes.
- Repair mode is diagnostic until it proves it improves quality without causing
  timeouts.

## Fixtures

- `billing-credit-contract`: cent-precision reconciliation with explicit final
  rounding. The Cyra expected value is `447.66`.
- `policy-reason-contract`: policy routing with exact human-readable reason
  labels such as `default triage`.
