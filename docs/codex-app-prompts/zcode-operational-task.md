# ZCode Operational Task Prompt

Paste this into Codex App with a normal development task in the task block.

```text
You are working in this repo. Turn the task below into a reviewable draft PR
without asking Aki to choose CLI vs app or to babysit ZCode failures.

Task:
<PASTE NORMAL DEV TASK HERE>

Operational policy:
- Codex plans, inspects, chooses repairs, validates, commits, pushes, and opens
  a draft PR.
- ZCode is only a bounded implementation worker.
- Do not run a 20+ live benchmark.
- Do not enable production green-path.
- Do not make direct mode or app-backed mode default.
- Keep GLM-5.2 fixed. Do not add GLM-4.7 fallback.
- Do not add a time-of-day gate.
- Do not weaken tests, validation, strict gates, or review gates.
- Do not treat unavailable ZCode usage as zero.
- Do not commit artifacts/reports.

Start:
1. unset GIT_DIR and GIT_WORK_TREE for git commands.
2. Check `git status -sb`, branch, remote, and `gh auth status`.
3. Read `.ai/HANDOFF.md` if present, then read the smallest relevant code and
   docs.
4. If the task has no issue, create one when feasible. Use branch
   `codex/issue-<number>-<slug>` unless already on a scoped branch.

Route policy:
1. Decide the allowed files, forbidden files, validation command, and strict
   acceptance surface before implementation.
2. Prefer the ZCode CLI route for safe bounded implementation:

   python3 tools/zcode_supervisor/zcode_supervisor.py auto-route \
     --workspace . \
     --objective "<specific task outcome>" \
     --allowed "<file>" \
     --validation "<command>" \
     --execute \
     --max-attempts 1 \
     --usage-snapshot-source none \
     --continue-with-codex-fallback

3. Use app-backed CDP only if all safety facts are already proven or can be
   proven without submit: ZCode target reachable, editable textbox reachable,
   expected packet workspace known, and workspace binding matches. If not
   proven, skip app-backed route and classify `workspace_not_bound`.
4. If ZCode blocks, continue with Codex fallback in the same allowed surface.
   Do not stop only because CLI/app is blocked.
5. Classify fallback as one of:
   `provider_timeout`, `provider_overload`, `workspace_not_bound`,
   `usage_unavailable`, `validation_failure`, `strict_failure`,
   `zcode_unavailable`, `skipped_by_policy`, or `routing_config_missing`.

Required result schema:
- `route_used = zcode_cli | zcode_app_cdp | codex_fallback`
- `zcode_usage_status = measured | unavailable | skipped`
- `fallback_reason`
- `strict_accepted`
- `claim_family`

Usage rules:
- If ZCode reports positive measured tokens, report them.
- If usage is unavailable or skipped, report token fields as null, never zero.
- Keep CLI, app-backed, direct, and Codex-fallback claims separate.

Fallback rules:
- Codex fallback may implement only after recording why ZCode did not complete.
- Fallback must still pass the same validation and strict gates.
- Fallback must not claim ZCode implemented the task.

Before PR:
1. Run targeted tests and validation.
2. Run strict gate checks relevant to the task.
3. Run `git diff --check`.
4. Inspect `git status -sb` and avoid unrelated files.
5. Check that no secrets, `.env`, credentials, or `artifacts/reports` are
   staged.
6. Commit with a Conventional Commit message, push, and open a draft PR.

Final response must include:
- branch
- commit SHA
- draft PR URL
- route_used
- zcode_usage_status
- fallback_reason
- strict_accepted
- claim_family
- files changed
- tests/checks run
- live count
- 20+ count
- production disabled
- direct non-default
- usage-zero guard
- whether Aki can paste a normal dev task now
- Aki action needed
```
