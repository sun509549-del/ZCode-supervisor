# Windows: Codex → ZCode API → Codex

This workflow keeps Codex responsible for planning, scope selection, audit, and
final acceptance. ZCode runs the bounded implementation through the headless
CLI bundled with ZCode Desktop. The implementation model is selected from an
API profile already configured in ZCode Desktop.

## Requirements

- Windows PowerShell 7
- ZCode Desktop installed and an API profile configured
- Node.js 22 or newer
- Python 3.11 or newer, available as `python`
- Git

The controller discovers standard ZCode installations and custom install
locations recorded in the Windows uninstall registry. `ZCODE_CLI_PATH` remains
available as an explicit override.

## 1. Inspect ZCode API choices

```powershell
pwsh -File .\scripts\setup-windows.ps1 `
  -Repo C:\path\to\project `
  -ListApis
```

The command prints provider IDs, display names, protocol kinds, and model IDs.
It does not print API keys or base URLs.

## 2. Install routing and select the worker model

Pass the exact provider and model IDs shown by `-ListApis`:

```powershell
pwsh -File .\scripts\setup-windows.ps1 `
  -Repo C:\path\to\project `
  -Provider tokenrhythm `
  -Model glm-5.3-flash
```

Omit `-Provider` and `-Model` to choose interactively. Setup merges the selected
ZCode API into `~\.zcode\cli\config.json`, preserving existing plugin and MCP
configuration. The API key is copied locally from ZCode Desktop and is never
printed or written into the target repository.

Use `-Force` when refreshing routing files that were created by an older
version of this toolkit.

Setup writes the routing contract into the target repository:

- `AGENTS.md`
- `.codex\zcode-routing.json`
- `.codex\ZCODE_DELEGATION.md`
- `.agents\mcp.json`, unless `-SkipVisionMcp` is used

## 3. Delegate one bounded implementation

Codex first decides the objective, exact allowed files, and validation command.
Then run one ZCode worker turn:

```powershell
pwsh -File .\scripts\invoke-zcode-windows.ps1 `
  -Repo C:\path\to\project `
  -Objective "Fix the ledger summary rounding bug" `
  -Allowed src\ledger.py,tests\test_ledger.py `
  -Validation "python -m pytest tests/test_ledger.py"
```

On Windows, validation commands must use `python` rather than `python3`. The
wrapper creates a bounded task packet, invokes ZCode once, audits every changed
file, reruns validation, and records compact JSON under
`.codex\zcode\runs`. It defaults to one attempt so Codex does not stay active
as a polling supervisor. After 60 seconds, the wrapper may accept and stop a
still-running worker once the scope audit and validation both pass; override
this with `-AcceptValidatedArtifactAfterMs` when a task needs a longer quiet
window.

## 4. Review in Codex

After the worker exits, Codex checks the run JSON, Git diff, validation result,
and acceptance criteria. A failed scope audit or validation result is not a
successful delivery. Send a narrower repair packet to ZCode when needed.

## Troubleshooting

- `ZCode CLI not found`: set `ZCODE_CLI_PATH` to the installed
  `resources\glm\zcode.cjs` file.
- `prompt_ready: false`: rerun setup without `-SkipCliBootstrap`.
- Provider error `1113`: the selected ZCode API profile has no usable balance or
  resource package; select another configured API profile or replenish it.
- Validation exit code `9009`: a command was not found. In particular, replace
  `python3` with `python` on Windows.
