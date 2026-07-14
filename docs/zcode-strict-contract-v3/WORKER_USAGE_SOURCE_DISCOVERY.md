# Worker Usage Source Discovery

Goal W3-DISCOVERY result: discover and choose the worker token usage source
route without running a provider canary, live pass, or 20+ live benchmark.

## Summary

- final_recommendation: `zcode_cli_model_usage_db_delta`
- exact command/path:
  `sqlite3 "file:$HOME/.zcode/cli/db/db.sqlite?mode=ro" ...`
- recommended route type: before/after delta using `model_usage.rowid`
- row scope: possible when exactly one delegated ZCode row runs between the
  before and after markers and other ZCode/ZAI usage is paused
- unit=tokens: yes, from `model_usage.computed_total_tokens` and component token
  columns
- percent/credit/balance kept separate: yes
- remaining blocker before next canary: implement non-live capture plumbing that
  records the DB before marker, queries positive token rows after the row,
  writes a row-local `provider_usage_ledger` JSONL, and fails closed on
  concurrent/unisolated usage
- provider_canary_count=0
- live_20_plus_count=0
- production_green_path_enabled=false
- direct_mode_default=false
- fallback if isolation fails: `external_zcode_usage_export_required`

## W4 Implementation Note

Goal W4 added non-live plumbing for this route in
`tools/zcode_eval/zcode_model_usage_db_delta.py`. Goal N refined attribution so
the strict-contract launcher can aggregate multiple DB rows when they are all
compatible with one isolated delegated row: contiguous row ids, identical
provider/model, timestamps inside the delegated DB marker window, positive
aggregate token total, preserved per-row totals, and no existing provider ledger
record to double count. Missing DB/table/columns, no new rows, unsafe
provider/model mixes, missing/out-of-window timestamps for multi-row attribution,
zero/null aggregate totals, or an existing provider ledger record stay
unavailable/null.

## Source Table

| source_id | source_name | command_or_path | machine_readable yes/no | unit: tokens / percent / credits / balance / unknown | value_type: per_call / cumulative_used / remaining / delta_possible / unknown | row_scoped yes/no/possible | before_after_delta_possible yes/no | isolation_risk low/medium/high | accepted_for_token_claim yes/no | reason | next_validation_step |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `zcode_cli_model_usage_db_delta` | ZCode CLI local model usage DB | `~/.zcode/cli/db/db.sqlite`, table `model_usage` | yes | tokens | delta_possible | possible | yes | medium | yes | Schema has `input_tokens`, `output_tokens`, `reasoning_tokens`, cache token columns, `provider_total_tokens`, and `computed_total_tokens`; local read-only schema/count checks found positive token rows for `zai` / `glm-5.2`. It is global to the local ZCode CLI, so the row is isolated only by a before/after marker and no concurrent ZCode usage. | Add non-live helper/test for rowid marker capture and JSONL projection; in the next approved canary, capture `max(rowid)` before the delegated row and query new rows after it, requiring positive total tokens and no concurrent rows outside the delegated window. |
| `zcode_app_server_usage_stats` | ZCode app-server Usage Stats RPC | `/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs app-server`, method `usage/stats` | yes | tokens | cumulative_used | no | no | high | no | Bundle exposes `usage/stats` returning an `agent-db` aggregate with token totals, models, heatmap, and daily usage. It is an app-wide aggregate, not a delegated-row source, and no stable CLI command for row-scoped capture is exposed. | Prefer direct DB rowid delta; use app-server only as a cross-check if a supported row/session-scoped usage command appears. |
| `zcode_app_server_session_usage` | ZCode app-server session usage RPC | `/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs app-server`, method `session/usage` with `sessionId` | yes | tokens | cumulative_used | possible | no | medium | no | Bundle exposes a `session/usage` result with total/input/output/reasoning/cache tokens, but current supervisor capture does not preserve the ZCode `sessionId` for the delegated row. Without that id it cannot be used as evidence. | If future CLI output exposes `sessionId`, validate this route against the DB rowid delta and preserve session id in `zcode-run.json`. |
| `zcode_usage_tab_visible_cdp` | ZCode Usage tab visible scrape | `node tools/zcode_control/zcodectl.mjs usage` | yes | unknown | unknown | no | no | high | no | Repo CDP script reads `document.body.innerText` and extracts visible token strings or quota percent. In this session CDP was not reachable without launching ZCode/CUA; even when reachable, this is display text, not a preserved backing source. | Do not use for token claims. If used operationally, preserve only as diagnostic display evidence separate from measured token ledgers. |
| `zcode_usage_tab_local_app_data` | ZCode Application Support backing data | `~/Library/Application Support/ZCode`, `~/Library/Logs`, `~/Library/Preferences` | no | unknown | unknown | no | no | high | no | Read-only filename/key/schema search found Chromium storage, rum store keys, preferences, and Homebrew logs, but no readable token/quota backing source. No database schema with Usage-tab token rows was found under Application Support. | Keep local app-data search as a diagnostic check; do not dump rows or secrets. |
| `codexbar_zai_usage_json` | CodexBar ZAI usage JSON | `codexbar usage --provider zai --format json --status --no-credits` | yes | percent | cumulative_used | no | yes | high | no | Safe CodexBar status returned provider `zai` with `usage.primary.usedPercent` / `usage.secondary.usedPercent` and no token-like paths. This is quota percent, not token-unit evidence. | Keep as non-token quota health evidence only. Do not convert percent to tokens. |
| `codexbar_local_data` | CodexBar local data files | `~/Library/Application Support/CodexBar`, `~/Library/Application Support/com.steipete.codexbar` | yes | unknown | unknown | no | no | high | no | Local files found were Codex/OpenAI dashboard/history data, not ZAI token-unit worker usage. No ZAI token snapshot file was found by safe filename/keyword search. | Do not use for ZCode worker token claims. |
| `zai_quota_api_snapshot` | Z.AI quota API snapshot | `https://api.z.ai/api/monitor/usage/quota/limit` via `zcodectl` internals | yes | percent | cumulative_used | no | yes | high | no | Existing repo code normalizes `TOKENS_LIMIT.percentage` as quota percent and intentionally leaves `token_candidates` empty. Test fixtures may include `usage` and `remaining`, but the current command path does not expose row-scoped `remaining_tokens` or `used_tokens`, and account-level quota can be affected by unrelated usage. | If Z.AI exposes explicit `remaining_tokens` / `used_tokens` with documented units, add a separate non-live parser and require isolated before/after validation before accepting. |
| `provider_stdout_json_usage` | ZCode CLI stdout/stderr JSON usage parser | `tools/zcode_control/zcodectl.mjs` `normalizedZcodeUsageFromStdout()` | yes | tokens | per_call | possible | no | low | no | Parser accepts JSON token usage fields such as `totalTokens`, `inputTokens`, `outputTokens`, and `reasoningTokens`, and can append `ZCODE_PROVIDER_USAGE_LEDGER` when present. The known live blocker is provider success without any usage payload, so this route is unavailable for the current worker runs. | Keep parser tests. If future ZCode stdout includes positive usage, it can be accepted directly and mirrored into the provider ledger. |
| `zcode_provider_usage_ledger_env` | Row-scoped provider usage ledger env | `ZCODE_PROVIDER_USAGE_LEDGER=<delegated-row-dir>/worker-usage.jsonl` | yes | tokens | per_call | yes | no | low | no | Strict-contract and preflight code accept this as row-scoped measured token evidence, but current repo wrapper only writes it when stdout usage is already available. It was missing in the blocked live run. | Use this as the preservation format for the DB delta route, writing one compact `provider_usage_ledger` JSONL record beside the delegated row. |
| `zcode_worker_usage_sidecar_env` | Worker usage sidecar/ledger envs | `ZCODE_WORKER_USAGE_SIDECAR`, `ZCODE_WORKER_USAGE_LEDGER`, `ZCODE_USAGE_LEDGER` | yes | tokens | per_call | yes | no | low | no | These env paths are accepted only when task-scoped and containing positive token-unit sidecar/ledger payloads. They do not discover tokens by themselves. Empty `usage={}` remains unavailable. | Preserve DB delta output through the existing task-local sidecar/ledger path. |
| `quota_credit_percent_sidecars` | Quota, credit, percent sidecars | task-local `worker-usage.json` / `worker-usage.jsonl` with non-token units | yes | percent / credits / balance | delta_possible | possible | yes | medium | no | Existing strict-contract tests classify quota percent, credits, and percent as partial/non-token evidence and keep `worker_total_tokens=null`. | Continue to reject for token claims. |

## Recommended Capture Sketch

Before a future approved canary row:

```bash
ZCODE_USAGE_DB="file:$HOME/.zcode/cli/db/db.sqlite?mode=ro"
BEFORE_ROWID="$(sqlite3 "$ZCODE_USAGE_DB" "select coalesce(max(rowid), 0) from model_usage;")"
```

After the delegated row finishes:

```bash
sqlite3 -json "$ZCODE_USAGE_DB" "
  select
    provider_id,
    model_id,
    count(*) as provider_call_count,
    sum(input_tokens) as input_tokens,
    sum(output_tokens) as output_tokens,
    sum(reasoning_tokens) as reasoning_tokens,
    sum(cache_creation_input_tokens) as cache_write_tokens,
    sum(cache_read_input_tokens) as cache_read_tokens,
    sum(computed_total_tokens) as total_tokens,
    min(started_at) as started_at,
    max(completed_at) as completed_at
  from model_usage
  where rowid > $BEFORE_ROWID
    and status = 'completed'
  group by provider_id, model_id;
"
```

The future canary should fail closed unless:

- exactly one delegated row was running between markers
- no other ZCode/ZAI session was active
- the query returns positive `total_tokens`
- provider/model are preserved
- the JSON evidence is copied into the delegated row directory as
  `worker-usage.jsonl`
- the row-local payload uses `source_type=provider_usage_ledger` and
  `unit=tokens`

## Sources Inspected

- ZCode Usage tab/local data:
  `/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs`,
  `~/Library/Application Support/ZCode`, `~/Library/Logs`,
  `~/Library/Preferences`, and `~/.zcode/cli/db/db.sqlite`
- CodexBar CLI/local data:
  `command -v codexbar`, `codexbar --help`,
  `codexbar usage --provider zai --format json --status --no-credits`,
  CodexBar Application Support paths
- ZAI quota/remaining token source:
  repo `zcodectl` Z.AI quota snapshot code and tests
- provider stdout/stderr:
  `normalizedZcodeUsageFromStdout()` and provider error/usage parser tests
- provider ledger/sidecar:
  `ZCODE_PROVIDER_USAGE_LEDGER`, `ZCODE_WORKER_USAGE_SIDECAR`,
  `ZCODE_WORKER_USAGE_LEDGER`, and `ZCODE_USAGE_LEDGER` strict-contract paths

## Final Classification

The selected source is `zcode_cli_model_usage_db_delta`.

It is not a direct per-call stdout payload. It is a before/after delta over the
local ZCode CLI `model_usage` table. It gives unit=tokens and has positive token
values in existing local usage rows, but it must be isolated by rowid markers and
operator discipline during the future canary. Percent, credit, balance, and
display-only evidence remain separate and are not accepted for token claims.
