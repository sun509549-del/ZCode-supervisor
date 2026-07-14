#!/usr/bin/env python3
"""Goal N-FINAL-GATED non-live readiness gate.

This checker intentionally does not run provider work. It proves that the
supervisor path can expose and preserve row-scoped worker token evidence, then
fails closed unless a provider/wrapper hook is actually available to append
measured token JSONL to that row-scoped ledger.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ZCODE_CLI = Path("/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs")
REPORT_DIR = ROOT / ".local" / "goal-n-final-gate1-dry-run"
TASK_SLUG = "policy-reason-contract"

sys.path.insert(0, str(ROOT))

from tools.zcode_eval.strict_contract_comparison import (  # noqa: E402
    PROVIDER_SUCCESS_WITHOUT_USAGE_PAYLOAD_REASON,
    WORKER_USAGE_EMPTY_SIDECAR_REASON,
    preserve_worker_usage_sidecar,
    zcode_worker_usage_fields,
)


def check(ok: bool, check_id: str, evidence: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "check_id": check_id,
        "ok": bool(ok),
        "evidence": evidence,
    }
    payload.update(extra)
    return payload


def run_direct_dry_run() -> tuple[Path | None, dict[str, Any]]:
    command = [
        sys.executable,
        "tools/zcode_eval/strict_contract_comparison.py",
        "--report-dir",
        str(REPORT_DIR),
        "--only",
        TASK_SLUG,
        "--delegation-execution",
        "direct",
        "--dry-run",
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    evidence = {
        "command": command,
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-2000:],
        "stderr_tail": result.stderr[-2000:],
    }
    if result.returncode != 0:
        return None, check(False, "direct_dry_run_generates_launcher", "dry-run command failed", **evidence)
    scripts = sorted((REPORT_DIR / "tasks").glob("*/zcode-*/run_zcode_launcher.sh"))
    if not scripts:
        return None, check(False, "direct_dry_run_generates_launcher", "no direct launcher script generated", **evidence)
    return scripts[0], check(True, "direct_dry_run_generates_launcher", str(scripts[0].relative_to(ROOT)), **evidence)


def check_launcher(script: Path | None) -> list[dict[str, Any]]:
    if script is None:
        return [
            check(False, "direct_launcher_exports_row_scoped_provider_ledger", "launcher unavailable"),
            check(False, "provider_ledger_path_inside_delegated_row_dir", "launcher unavailable"),
            check(False, "launcher_preserves_worker_usage_sidecar", "launcher unavailable"),
        ]

    text = script.read_text(encoding="utf-8")
    export_match = re.search(r"^export ZCODE_PROVIDER_USAGE_LEDGER=(.+/worker-usage\.jsonl)$", text, re.MULTILINE)
    unset_match = re.search(r"^unset (.+)$", text, re.MULTILINE)
    unset_vars = unset_match.group(1).split() if unset_match else []
    export_path = Path(export_match.group(1)).resolve() if export_match else None
    expected_parent = script.parent.resolve()
    return [
        check(
            export_match is not None and "ZCODE_PROVIDER_USAGE_LEDGER" not in unset_vars,
            "direct_launcher_exports_row_scoped_provider_ledger",
            "launcher exports ZCODE_PROVIDER_USAGE_LEDGER and does not unset it",
            export_path=str(export_path) if export_path else None,
            unset_vars=unset_vars,
        ),
        check(
            export_path is not None and export_path.parent == expected_parent,
            "provider_ledger_path_inside_delegated_row_dir",
            "worker-usage.jsonl is placed inside the generated delegated row directory",
            export_path=str(export_path) if export_path else None,
            delegated_row_dir=str(expected_parent),
        ),
        check(
            "preserve_worker_usage_sidecar" in text and "worker_usage_source_path" in text,
            "launcher_preserves_worker_usage_sidecar",
            "launcher invokes preserve_worker_usage_sidecar after zcodectl run-packet",
        ),
    ]


def check_preservation_contract() -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory() as tmp:
        run_json = Path(tmp) / "tasks" / TASK_SLUG / "zcode-delegated" / "zcode-run.json"
        run_json.parent.mkdir(parents=True)
        run_json.write_text(json.dumps({"usage_accounting": {"usage_available": False}}), encoding="utf-8")
        ledger = run_json.parent / "worker-usage.jsonl"
        ledger.write_text(
            json.dumps(
                {
                    "source_type": "provider_usage_ledger",
                    "provider": "zai",
                    "model": "glm-live",
                    "unit": "tokens",
                    "usage": {"total_tokens": 23, "input_tokens": 17, "output_tokens": 6},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        sidecar = preserve_worker_usage_sidecar(run_json, {"ZCODE_PROVIDER_USAGE_LEDGER": str(ledger)})
        payload = json.loads(run_json.read_text(encoding="utf-8"))
        fields = zcode_worker_usage_fields(payload, run_json)

    with tempfile.TemporaryDirectory() as tmp:
        run_json = Path(tmp) / "tasks" / TASK_SLUG / "zcode-delegated" / "zcode-run.json"
        run_json.parent.mkdir(parents=True)
        run_json.write_text(
            json.dumps(
                {
                    "usage_accounting": {
                        "usage_available": False,
                        "no_usage_reason": PROVIDER_SUCCESS_WITHOUT_USAGE_PAYLOAD_REASON,
                    }
                }
            ),
            encoding="utf-8",
        )
        empty_sidecar = preserve_worker_usage_sidecar(run_json)
        empty_payload = json.loads(run_json.read_text(encoding="utf-8"))
        empty_fields = zcode_worker_usage_fields(empty_payload, run_json)

    return [
        check(
            sidecar == ledger
            and fields["worker_usage_status"] == "measured"
            and fields["worker_usage_unit"] == "tokens"
            and fields["worker_total_tokens"] == 23
            and fields["worker_usage_source_path"] == str(ledger.resolve()),
            "expected_jsonl_measured_tokens_positive_total",
            "row-scoped provider ledger JSONL is preserved as measured token usage",
            worker_usage_status=fields.get("worker_usage_status"),
            worker_usage_unit=fields.get("worker_usage_unit"),
            worker_total_tokens=fields.get("worker_total_tokens"),
            worker_usage_source_path=fields.get("worker_usage_source_path"),
        ),
        check(
            empty_sidecar.name == "worker-usage.json"
            and empty_fields["worker_usage_status"] == "unavailable"
            and empty_fields["worker_total_tokens"] is None,
            "empty_usage_still_blocks",
            "missing or empty usage remains unavailable and does not become zero",
            worker_usage_status=empty_fields.get("worker_usage_status"),
            worker_total_tokens=empty_fields.get("worker_total_tokens"),
            no_usage_reason=empty_fields.get("worker_usage_no_usage_reason"),
        ),
        check(
            empty_fields.get("worker_usage_empty_sidecar") is True
            and empty_fields.get("worker_usage_no_usage_reason")
            in {PROVIDER_SUCCESS_WITHOUT_USAGE_PAYLOAD_REASON, WORKER_USAGE_EMPTY_SIDECAR_REASON},
            "provider_success_without_usage_payload_still_blocks",
            "provider success without token payload remains a blocking no-usage reason",
            no_usage_reason=empty_fields.get("worker_usage_no_usage_reason"),
            worker_usage_empty_sidecar=empty_fields.get("worker_usage_empty_sidecar"),
        ),
    ]


def wrapper_append_configured(zcodectl_text: str, zcode_cli_text: str) -> tuple[bool, dict[str, Any]]:
    zcode_cli_hook = any(
        needle in zcode_cli_text
        for needle in (
            "ZCODE_PROVIDER_USAGE_LEDGER",
            "ZCODE_WORKER_USAGE_LEDGER",
            "worker-usage.jsonl",
            "provider_usage_ledger",
        )
    )
    repo_wrapper_append = (
        "ZCODE_PROVIDER_USAGE_LEDGER" in zcodectl_text
        and "appendFile" in zcodectl_text
        and "provider_usage_ledger" in zcodectl_text
    )
    return zcode_cli_hook or repo_wrapper_append, {
        "zcode_cli_hook": zcode_cli_hook,
        "repo_wrapper_append": repo_wrapper_append,
    }


def check_upstream_emission() -> dict[str, Any]:
    zcodectl_text = (ROOT / "tools/zcode_control/zcodectl.mjs").read_text(encoding="utf-8")
    zcode_cli_exists = DEFAULT_ZCODE_CLI.exists()
    zcode_cli_text = DEFAULT_ZCODE_CLI.read_text(encoding="utf-8", errors="ignore") if zcode_cli_exists else ""
    configured, details = wrapper_append_configured(zcodectl_text, zcode_cli_text)
    evidence = (
        "provider/wrapper ledger append hook is configured"
        if configured
        else "local ZCode CLI and repo-local zcodectl do not append measured token JSONL to ZCODE_PROVIDER_USAGE_LEDGER"
    )
    return check(
        configured,
        "provider_or_wrapper_expected_to_append_token_jsonl",
        evidence,
        zcode_cli_path=str(DEFAULT_ZCODE_CLI),
        zcode_cli_exists=zcode_cli_exists,
        **details,
    )


def build_payload(checks: list[dict[str, Any]]) -> tuple[dict[str, Any], int]:
    quality_check_ids = {
        "direct_dry_run_generates_launcher",
        "direct_launcher_exports_row_scoped_provider_ledger",
        "provider_ledger_path_inside_delegated_row_dir",
        "launcher_preserves_worker_usage_sidecar",
        "expected_jsonl_measured_tokens_positive_total",
        "empty_usage_still_blocks",
        "provider_success_without_usage_payload_still_blocks",
    }
    quality_ok = all(item["ok"] for item in checks if item["check_id"] in quality_check_ids)
    emission = next(item for item in checks if item["check_id"] == "provider_or_wrapper_expected_to_append_token_jsonl")
    canary_allowed = quality_ok and emission["ok"]
    final_outcome = None
    blocker = None
    exit_code = 0
    if not quality_ok:
        final_outcome = "blocked_quality"
        blocker = "Gate 1 quality readiness failed before provider canary."
        exit_code = 1
    elif not emission["ok"]:
        final_outcome = "blocked_upstream_usage_emission_missing"
        blocker = (
            "provider/wrapper token emission is not available from this repo: "
            "no inspected provider or wrapper appends measured token JSONL to "
            "ZCODE_PROVIDER_USAGE_LEDGER"
        )
    return (
        {
            "ok": exit_code == 0,
            "gate1_quality_ready": quality_ok,
            "gate1_passed": canary_allowed,
            "canary_allowed": canary_allowed,
            "final_outcome": final_outcome,
            "blocker": blocker,
            "checks": checks,
        },
        exit_code,
    )


def main() -> int:
    script, dry_run = run_direct_dry_run()
    checks = [dry_run]
    checks.extend(check_launcher(script))
    checks.extend(check_preservation_contract())
    checks.append(check_upstream_emission())
    payload, exit_code = build_payload(checks)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
