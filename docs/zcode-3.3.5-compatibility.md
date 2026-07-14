# ZCode 3.3.5 Compatibility Evidence

## Decision

The supervisor's hermetic compatibility baseline is `3.3.5`. The authoritative
source is the [official ZCode changelog](https://zcode.z.ai/en/changelog), which
listed `3.3.5` as released on 2026-07-13 when checked on 2026-07-14.

This baseline means the repository's stable fixture-driven contracts remain
compatible with the release notes and expected interfaces. It is not evidence
that a live `3.3.5` app or provider task was executed.

## Static Installed Metadata

Non-interactive inspection on 2026-07-14 found:

- app: `/Applications/ZCode.app`, version `3.3.4`
- bundled CLI: `/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs`,
  version `0.15.2`

The app was not launched or focused. The installed app is one patch behind the
official release, so this metadata does not validate the `3.3.5` runtime.

## Hermetic Evidence

The compatibility decision is covered by fixture-driven checks for:

- release parsing and baseline comparison;
- provider error classification;
- packet orchestration, audit, retry, and timeout handling;
- token usage extraction and sidecar normalization;
- Python/Node packaging and console entry points;
- the full repository validation command, `bash scripts/check.sh`.

The `3.3.5` changelog adds ZIP URL plugin installation and fixes request
reliability, model-unavailable prompts, plugin lifecycle behavior, background
tasks, plan retries, and other desktop behavior. None requires a change to the
supervisor's bounded packet, provider error, usage, or packaging contracts.

## UNVERIFIED Boundaries

The following remain **UNVERIFIED** because they require a newer live app,
provider credentials, a paid task, visible GUI interaction, or another
platform:

- live `3.3.5` headless delegation and provider behavior;
- live plugin installation, background tasks, plan retry, and model fallback;
- live Usage Stats UI/database shape and quota reporting;
- visible desktop/CDP behavior;
- Windows and Linux runtime/path behavior.

ZCode therefore remains optional/experimental, and the last recorded live
delegation validation remains `3.1.2`. Rollback is a normal revert of the
compatibility commit; no public release, package publication, or production
change is part of this decision.
