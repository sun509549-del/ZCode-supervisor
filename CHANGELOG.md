# Changelog

## Unreleased

## v0.0.2 - 2026-06-20

- Make the default delegated-work contract a thin Codex launcher/auditor flow:
  Codex plans, chooses allowed files and validation, launches ZCode, then uses
  run JSON, scope audit, changed-file caps, and validation as the first
  acceptance evidence.
- Use `codex-autoreview --mode branch` against the real PR base as the final
  closeout gate after the local commit and before push.
- Add `zcode-eval compare-codex-runs` so direct Codex, normal supervision, and
  thin ZCode launcher runs can be compared later using saved
  `codex exec --json` event logs plus optional ZCode run JSON.
- Harden auto-delegation reliability: prefer authoritative `run-packet --out`
  JSON, fail missing/no-change implementation results, pass bounded timeouts,
  and accept `auto-route --json` as a compatibility flag.

- Add ZCode Usage Stats snapshots for token and quota percent logging.
- Add `tokens_before`, `tokens_after`, `tokens_used`,
  `quota_percent_before`, `quota_percent_after`, and `quota_percent_used` to
  evaluation records and summaries.
- Add `zcode_eval show-log` for later ledger inspection.
- Add `zcodectl open-usage` and `zcodectl usage` helpers for collecting visible
  ZCode Settings usage values.
- Add bundled ZCode CLI discovery and headless control commands:
  `cli-path`, `cli-preflight`, `cli-doctor`, `cli-version`, `cli-prompt`, and
  `run-packet`.
- Add official ZCode release monitoring against the checked compatibility
  baseline and a scheduled GitHub Actions workflow that opens an update Issue
  when ZCode moves.
- Update the hermetic ZCode compatibility baseline to `3.3.5`, refresh
  release-monitor tests, and keep live `3.3.5` app/provider behavior explicitly
  unverified.
- Complete the same-packet ZCode/Codex worker comparison: Codex passed 20/20
  strict tasks versus ZCode 18/20 while using about 5.16x fewer worker tokens.
  Keep ZCode optional/experimental and do not claim total workflow savings
  while compatible Codex orchestration usage remains unavailable.
- Add Homebrew release preparation: formula template, formula updater,
  release-artifact workflow with GitHub artifact attestations, release-prep CI,
  and release documentation.
- Add local temporary-tap Homebrew install validation guidance for the planned
  public tap path.
- Switch the active distribution plan to `uvx` / PyPI first, with TestPyPI/PyPI
  Trusted Publishing workflows and GitHub Release verified installer artifacts.
- Archive Homebrew as optional historical packaging instead of the primary
  setup path.
- Document the public distribution model across the product repository, PyPI,
  verified GitHub Release assets, and the archived Formula-only tap.

## v0.0.1 - 2026-06-17

- Add `zcode_supervisor` packet, snapshot, and audit commands.
- Add GLM-5.2 task class, effort, and context policy fields to task packets.
- Add low-babysitting guardrails for Full Access, destructive validation
  commands, risk budget, and changed-file limits.
- Add `zcodectl` helper for ZCode desktop/CDP control.
- Add workspace-local ZCode templates and subagent definitions.
- Add GLM-5.2/ZCode operator guide and specialized long-context subagents.
- Add benchmark fixtures and strict supervisor tests.
- Add public repository metadata and release checks.
