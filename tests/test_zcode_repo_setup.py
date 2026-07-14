import contextlib
import io
import json
import os
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from tools.zcode_supervisor import auto_route
from tools.zcode_supervisor.repo_setup import AGENTS_BEGIN
from tools.zcode_supervisor.zcode_supervisor import main


class ZCodeRepoSetupTests(unittest.TestCase):
    def _main_json(self, argv: list[str]) -> tuple[int, dict]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main(argv)
        return exit_code, json.loads(output.getvalue())

    def _wait_for_path(self, path: Path, timeout_seconds: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if path.exists():
                return True
            time.sleep(0.05)
        return path.exists()

    def test_install_repo_writes_routing_contract_and_vision_mcp_without_agents_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._fixture_workspace(Path(tmp))

            self.assertEqual(main(["install-repo", "--repo", str(repo)]), 0)

            routing = repo / ".codex/zcode-routing.json"
            delegation = repo / ".codex/ZCODE_DELEGATION.md"
            vision_mcp = repo / ".agents/mcp.json"
            self.assertTrue(routing.exists())
            self.assertTrue(delegation.exists())
            self.assertTrue(vision_mcp.exists())
            self.assertFalse((repo / "AGENTS.md").exists())
            payload = json.loads(routing.read_text(encoding="utf-8"))
            self.assertEqual(payload["orchestrator"], "codex")
            self.assertEqual(payload["implementation_worker"], "zcode")
            self.assertEqual(payload["routing_mode"], "auto")
            self.assertEqual(payload["defaults"]["max_attempts"], 1)
            self.assertEqual(payload["defaults"]["usage_snapshot_source"], "none")
            self.assertFalse(payload["defaults"]["repair_validation"])
            self.assertEqual(payload["defaults"]["result_verbosity"], "compact")
            self.assertIn("zcode_unavailable_recovery", payload["policy"]["codex_direct_edit_allowed"])
            self.assertIn("production_risk", payload["policy"]["ask_user_before"])
            self.assertEqual(payload["vision"]["service"], "zai-mcp-server")
            self.assertIn("bounded_implementation", payload["policy"]["zcode_owns"])
            delegation_text = delegation.read_text(encoding="utf-8")
            self.assertIn("auto-route", delegation_text)
            self.assertIn("zcodectl run-packet", delegation_text)
            self.assertIn("thin launcher/auditor", delegation_text)
            self.assertIn(
                "codex-autoreview --mode branch --base origin/main --engine codex --no-web-search",
                delegation_text,
            )
            mcp = json.loads(vision_mcp.read_text(encoding="utf-8"))
            self.assertEqual(mcp["mcpServers"]["zai-mcp-server"]["command"], "npx")
            self.assertEqual(mcp["mcpServers"]["zai-mcp-server"]["args"], ["-y", "@z_ai/mcp-server"])

    def test_install_repo_can_skip_vision_mcp(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._fixture_workspace(Path(tmp))

            self.assertEqual(main(["install-repo", "--repo", str(repo), "--skip-vision-mcp"]), 0)

            self.assertFalse((repo / ".agents/mcp.json").exists())

    def test_install_repo_can_add_agents_pointer_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._fixture_workspace(Path(tmp))
            (repo / "AGENTS.md").write_text("# Local Rules\n\nKeep changes small.\n", encoding="utf-8")

            self.assertEqual(main(["install-repo", "--repo", str(repo), "--write-agents"]), 0)
            self.assertEqual(main(["install-repo", "--repo", str(repo), "--write-agents"]), 0)

            agents = (repo / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn(".codex/ZCODE_DELEGATION.md", agents)
            self.assertIn("auto-route", agents)
            self.assertIn(
                "codex-autoreview --mode branch --base origin/main --engine codex --no-web-search",
                agents,
            )
            self.assertEqual(agents.count(AGENTS_BEGIN), 1)

    def test_install_repo_merges_vision_mcp_without_replacing_existing_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._fixture_workspace(Path(tmp))
            mcp = repo / ".agents/mcp.json"
            mcp.parent.mkdir(parents=True)
            mcp.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "existing": {"command": "custom-mcp"},
                            "zai-mcp-server": {"command": "already-configured"},
                        }
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(main(["install-repo", "--repo", str(repo)]), 0)

            payload = json.loads(mcp.read_text(encoding="utf-8"))
            self.assertEqual(payload["mcpServers"]["existing"]["command"], "custom-mcp")
            self.assertEqual(payload["mcpServers"]["zai-mcp-server"]["command"], "already-configured")

            self.assertEqual(main(["install-repo", "--repo", str(repo), "--force"]), 0)

            payload = json.loads(mcp.read_text(encoding="utf-8"))
            self.assertEqual(payload["mcpServers"]["existing"]["command"], "custom-mcp")
            self.assertEqual(payload["mcpServers"]["zai-mcp-server"]["command"], "npx")

    def test_install_repo_rejects_symlinked_output_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._fixture_workspace(root)
            outside = root / "outside"
            outside.mkdir()
            (repo / ".codex").symlink_to(outside, target_is_directory=True)

            self.assertEqual(main(["install-repo", "--repo", str(repo)]), 1)

            self.assertFalse((outside / "zcode-routing.json").exists())

    def test_install_repo_rejects_symlinked_agents_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._fixture_workspace(root)
            outside = root / "outside-agents.md"
            outside.write_text("outside\n", encoding="utf-8")
            (repo / "AGENTS.md").symlink_to(outside)

            self.assertEqual(main(["install-repo", "--repo", str(repo), "--write-agents"]), 1)

            self.assertEqual(outside.read_text(encoding="utf-8"), "outside\n")

    def test_auto_route_reports_missing_config_as_codex_direct(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._fixture_workspace(Path(tmp))

            exit_code, payload = self._main_json(
                ["auto-route", "--workspace", str(repo), "--objective", "fix src/app.js"]
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["route"], "codex_direct")
            self.assertEqual(payload["reason"], "routing_config_missing")

    def test_auto_route_accepts_json_flag_for_codex_cli_compatibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._fixture_workspace(Path(tmp))

            exit_code, payload = self._main_json(
                ["auto-route", "--workspace", str(repo), "--objective", "fix src/app.js", "--json"]
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["route"], "codex_direct")
            self.assertEqual(payload["reason"], "routing_config_missing")

    def test_auto_route_classifies_implementation_as_needing_codex_planning(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._fixture_workspace(Path(tmp))
            self.assertEqual(main(["install-repo", "--repo", str(repo)]), 0)

            exit_code, payload = self._main_json(
                ["auto-route", "--workspace", str(repo), "--objective", "fix src/app.js"]
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["route"], "needs_codex_planning")
            self.assertEqual(payload["reason"], "missing_allowed_or_validation")

    def test_auto_route_keeps_no_zcode_and_high_risk_with_codex(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._fixture_workspace(Path(tmp))
            self.assertEqual(main(["install-repo", "--repo", str(repo)]), 0)

            exit_code, payload = self._main_json(
                ["auto-route", "--workspace", str(repo), "--objective", "no-zcode fix src/app.js"]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["route"], "codex_direct")
            self.assertEqual(payload["reason"], "no_zcode_requested")

            exit_code, payload = self._main_json(
                ["auto-route", "--workspace", str(repo), "--objective", "no-zcode delete production data"]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["route"], "ask_user")
            self.assertEqual(payload["reason"], "high_risk_task")

            exit_code, payload = self._main_json(
                [
                    "auto-route",
                    "--workspace",
                    str(repo),
                    "--objective",
                    "Update README without reading secrets and deploy to production.",
                    "--allowed",
                    "README.md",
                    "--validation",
                    "python3 -c 'print(42)'",
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["route"], "ask_user")
            self.assertEqual(payload["reason"], "high_risk_task")

            exit_code, payload = self._main_json(
                [
                    "auto-route",
                    "--workspace",
                    str(repo),
                    "--objective",
                    "Do not read secrets\nUse ZCode to update production deploy script.",
                    "--allowed",
                    "README.md",
                    "--validation",
                    "python3 -c 'print(42)'",
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["route"], "ask_user")
            self.assertEqual(payload["reason"], "high_risk_task")

            exit_code, payload = self._main_json(
                [
                    "auto-route",
                    "--workspace",
                    str(repo),
                    "--objective",
                    "Do not ask, deploy to production.",
                    "--allowed",
                    "README.md",
                    "--validation",
                    "python3 -c 'print(42)'",
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["route"], "ask_user")
            self.assertEqual(payload["reason"], "high_risk_task")

            exit_code, payload = self._main_json(
                [
                    "auto-route",
                    "--workspace",
                    str(repo),
                    "--objective",
                    "Do not read secrets, delete data.",
                    "--allowed",
                    "README.md",
                    "--validation",
                    "python3 -c 'print(42)'",
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["route"], "ask_user")
            self.assertEqual(payload["reason"], "high_risk_task")

            exit_code, payload = self._main_json(
                ["auto-route", "--workspace", str(repo), "--objective", "deploy production migration"]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["route"], "ask_user")
            self.assertEqual(payload["reason"], "high_risk_task")

    def test_auto_route_does_not_treat_safety_guardrails_as_high_risk_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._fixture_workspace(Path(tmp))
            self.assertEqual(main(["install-repo", "--repo", str(repo)]), 0)

            exit_code, payload = self._main_json(
                [
                    "auto-route",
                    "--workspace",
                    str(repo),
                    "--objective",
                    "Update README without reading secrets, API keys, tokens, or production config.",
                    "--allowed",
                    "README.md",
                    "--validation",
                    "python3 -c 'print(42)'",
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["route"], "delegate_zcode")
            self.assertEqual(payload["reason"], "implementation_task")

            exit_code, payload = self._main_json(
                [
                    "auto-route",
                    "--workspace",
                    str(repo),
                    "--objective",
                    "Use ZCode to review README without editing.",
                    "--allowed",
                    "README.md",
                    "--validation",
                    "python3 -c 'print(42)'",
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["route"], "codex_direct")
            self.assertEqual(payload["reason"], "read_only_task")

            exit_code, payload = self._main_json(
                [
                    "auto-route",
                    "--workspace",
                    str(repo),
                    "--objective",
                    "Do not read secrets; use ZCode to deploy to production.",
                    "--allowed",
                    "README.md",
                    "--validation",
                    "python3 -c 'print(42)'",
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["route"], "ask_user")
            self.assertEqual(payload["reason"], "high_risk_task")

            exit_code, payload = self._main_json(
                [
                    "auto-route",
                    "--workspace",
                    str(repo),
                    "--objective",
                    "Fix billing reconciliation code only; do not deploy, migrate, charge, or touch production.",
                    "--allowed",
                    "README.md",
                    "--validation",
                    "python3 -c 'print(42)'",
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["route"], "delegate_zcode")
            self.assertEqual(payload["reason"], "implementation_task")

    def test_auto_route_treats_billing_domain_code_as_implementation_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._fixture_workspace(Path(tmp))
            self.assertEqual(main(["install-repo", "--repo", str(repo)]), 0)

            exit_code, payload = self._main_json(
                [
                    "auto-route",
                    "--workspace",
                    str(repo),
                    "--objective",
                    "Fix the billing reconciliation implementation in src/app.js.",
                    "--allowed",
                    "src/app.js",
                    "--validation",
                    "python3 -c 'print(42)'",
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["route"], "delegate_zcode")
            self.assertEqual(payload["reason"], "implementation_task")

    def test_auto_route_execute_fails_closed_when_route_blocks_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._fixture_workspace(Path(tmp))
            self.assertEqual(main(["install-repo", "--repo", str(repo)]), 0)

            exit_code, payload = self._main_json(
                [
                    "auto-route",
                    "--workspace",
                    str(repo),
                    "--objective",
                    "Deploy production migration.",
                    "--allowed",
                    "src/app.js",
                    "--validation",
                    "python3 -c 'print(42)'",
                    "--execute",
                ]
            )

            self.assertEqual(exit_code, 1)
            self.assertFalse(payload["ok"])
            self.assertFalse(payload["executed"])
            self.assertEqual(payload["route"], "ask_user")
            self.assertEqual(payload["failure_reason"], "execution_blocked_by_route")
            self.assertIn("run_json_present", payload["required_acceptance"])

    def test_auto_route_fallback_flag_does_not_override_high_risk_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._fixture_workspace(Path(tmp))
            self.assertEqual(main(["install-repo", "--repo", str(repo)]), 0)

            exit_code, payload = self._main_json(
                [
                    "auto-route",
                    "--workspace",
                    str(repo),
                    "--objective",
                    "Deploy production migration.",
                    "--allowed",
                    "src/app.js",
                    "--validation",
                    "python3 -c 'print(42)'",
                    "--execute",
                    "--continue-with-codex-fallback",
                ]
            )

            self.assertEqual(exit_code, 1)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["route"], "ask_user")
            self.assertEqual(payload["fallback_reason"], "skipped_by_policy")
            self.assertFalse(payload["task_acceptance_ready"])

    def test_auto_route_execute_creates_packet_and_runs_controller(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._fixture_workspace(root)
            self.assertEqual(main(["install-repo", "--repo", str(repo)]), 0)
            fake_controller = root / "fake-zcodectl.mjs"
            fake_controller.write_text(
                """#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
	const out = process.argv[process.argv.indexOf("--out") + 1];
	const retryDelayMs = process.argv[process.argv.indexOf("--retry-delay-ms") + 1];
	const timeoutMs = process.argv[process.argv.indexOf("--timeout-ms") + 1];
	const validationTimeout = process.argv[process.argv.indexOf("--validation-timeout") + 1];
	const usageSnapshotTimeoutMs = process.argv[process.argv.indexOf("--usage-snapshot-timeout-ms") + 1];
	const usageSnapshotSource = process.argv[process.argv.indexOf("--usage-snapshot-source") + 1];
	const repairValidation = process.argv.includes("--repair-validation")
		? "true"
		: (process.argv.includes("--no-repair-validation") ? "false" : "missing");
	const payload = {
		ok: true,
		supervisor_state: "success",
		validation_ok: true,
		retry_delay_ms: retryDelayMs,
		timeout_ms: timeoutMs,
		validation_timeout: validationTimeout,
		usage_snapshot_timeout_ms: usageSnapshotTimeoutMs,
		usage_snapshot_source: usageSnapshotSource,
		repair_validation: repairValidation,
		audit: { ok: true, changed_count: 1, validation: { ok: true } }
	};
	fs.mkdirSync(path.dirname(out), { recursive: true });
	fs.writeFileSync(out, JSON.stringify(payload) + "\\n");
	process.stdout.write("ZCode finished with a human-readable report.\\n");
""",
                encoding="utf-8",
            )
            routing = repo / ".codex/zcode-routing.json"
            payload = json.loads(routing.read_text(encoding="utf-8"))
            payload["paths"]["zcodectl"] = str(fake_controller)
            routing.write_text(json.dumps(payload), encoding="utf-8")
            (repo / "screenshots").mkdir()
            (repo / "screenshots/state.png").write_bytes(
                bytes([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])
            )

            exit_code, result = self._main_json(
                [
                    "auto-route",
                    "--workspace",
                    str(repo),
                    "--objective",
                    "fix src/app.js",
                    "--allowed",
                    "src/app.js",
                    "--validation",
                    "python3 -c 'print(42)'",
                    "--expected-output",
                    "src/app.js exports the corrected value",
                    "--acceptance-criterion",
                    "artifact_quality is pass after Codex audit",
                    "--what-not-to-do",
                    "Do not add dependencies",
                    "--execute",
                    "--max-attempts",
                    "1",
                    "--retry-delay-ms",
                    "0",
                    "--trusted-zcodectl",
                    str(fake_controller),
                    "--timeout-ms",
                    "1234",
                    "--validation-timeout",
                    "7",
                    "--usage-snapshot-timeout-ms",
                    "99",
                    "--vision-image",
                    "screenshots/state.png",
                    "--vision-required",
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(result["executed"])
            self.assertEqual(result["route"], "delegate_zcode")
            self.assertNotIn("run_result", result)
            self.assertEqual(result["run_result_summary"]["supervisor_state"], "success")
            self.assertEqual(result["run_result_source"], "run_file")
            self.assertEqual(result["run_result_summary"]["changed_count"], 1)
            self.assertEqual(result["zcode_changed_count"], 1)
            self.assertTrue(result["zcode_did_work"])
            run_payload = json.loads(Path(result["run"]).read_text(encoding="utf-8"))
            self.assertEqual(run_payload["retry_delay_ms"], "0")
            self.assertEqual(run_payload["timeout_ms"], "1234")
            self.assertEqual(run_payload["validation_timeout"], "7")
            self.assertEqual(run_payload["usage_snapshot_timeout_ms"], "99")
            self.assertEqual(run_payload["usage_snapshot_source"], "none")
            self.assertEqual(run_payload["repair_validation"], "false")
            self.assertTrue(Path(result["packet"]).exists())
            self.assertTrue(Path(result["run"]).exists())
            packet = json.loads(Path(result["packet"]).read_text(encoding="utf-8"))
            self.assertEqual(packet["max_changed_files"], 1)
            self.assertEqual(packet["expected_outputs"], ["src/app.js exports the corrected value"])
            self.assertEqual(packet["acceptance_criteria"], ["artifact_quality is pass after Codex audit"])
            self.assertIn("Do not add dependencies", packet["what_not_to_do"])
            self.assertTrue(packet["vision"]["required"])
            self.assertEqual(packet["vision"]["image_files"], ["screenshots/state.png"])

    def test_auto_route_operational_schema_reports_successful_cli_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._fixture_workspace(root)
            self.assertEqual(main(["install-repo", "--repo", str(repo)]), 0)
            fake_controller = root / "fake-zcodectl.mjs"
            fake_controller.write_text(
                """#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
const out = process.argv[process.argv.indexOf("--out") + 1];
const payload = {
  ok: true,
  supervisor_state: "success",
  validation_ok: true,
  worker_usage_status: "measured",
  worker_usage_unit: "tokens",
  worker_total_tokens: 42,
  strict_accepted: true,
  audit: { ok: true, changed_count: 1, validation: { ok: true } }
};
fs.mkdirSync(path.dirname(out), { recursive: true });
fs.writeFileSync(out, JSON.stringify(payload) + "\\n");
""",
                encoding="utf-8",
            )

            exit_code, result = self._main_json(
                [
                    "auto-route",
                    "--workspace",
                    str(repo),
                    "--objective",
                    "fix src/app.js",
                    "--allowed",
                    "src/app.js",
                    "--validation",
                    "python3 -c 'print(42)'",
                    "--execute",
                    "--max-attempts",
                    "1",
                    "--trusted-zcodectl",
                    str(fake_controller),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(result["ok"])
            self.assertTrue(result["delegation_ok"])
            self.assertEqual(result["operational_gateway_schema_version"], 1)
            self.assertEqual(result["route_used"], "zcode_cli")
            self.assertEqual(result["zcode_usage_status"], "measured")
            self.assertEqual(result["zcode_total_tokens"], 42)
            self.assertIsNone(result["fallback_reason"])
            self.assertTrue(result["strict_accepted"])
            self.assertFalse(result["strict_gate_weakened"])
            self.assertTrue(result["task_acceptance_ready"])
            self.assertEqual(result["claim_family"], "codex_mediated_zcode_cli")

    def test_auto_route_codex_fallback_for_provider_timeout_keeps_strict_unaccepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._fixture_workspace(root)
            self.assertEqual(main(["install-repo", "--repo", str(repo)]), 0)
            fake_controller = root / "fake-zcodectl.mjs"
            fake_controller.write_text(
                """#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
const out = process.argv[process.argv.indexOf("--out") + 1];
const payload = {
  ok: false,
  status: "run_timeout",
  supervisor_state: "run_timeout",
  timed_out: true,
  worker_usage_status: "unavailable",
  worker_total_tokens: null,
  strict_accepted: null,
  audit: { ok: false, changed_count: 0, validation: { ok: false } }
};
fs.mkdirSync(path.dirname(out), { recursive: true });
fs.writeFileSync(out, JSON.stringify(payload) + "\\n");
process.exit(1);
""",
                encoding="utf-8",
            )

            exit_code, result = self._main_json(
                [
                    "auto-route",
                    "--workspace",
                    str(repo),
                    "--objective",
                    "fix src/app.js",
                    "--allowed",
                    "src/app.js",
                    "--validation",
                    "python3 -c 'print(42)'",
                    "--execute",
                    "--trusted-zcodectl",
                    str(fake_controller),
                    "--continue-with-codex-fallback",
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(result["ok"])
            self.assertFalse(result["delegation_ok"])
            self.assertTrue(result["codex_fallback_required"])
            self.assertEqual(result["route_used"], "codex_fallback")
            self.assertEqual(result["zcode_usage_status"], "unavailable")
            self.assertIsNone(result["zcode_total_tokens"])
            self.assertEqual(result["fallback_reason"], "provider_timeout")
            self.assertIsNone(result["strict_accepted"])
            self.assertFalse(result["strict_gate_weakened"])
            self.assertFalse(result["task_acceptance_ready"])
            self.assertEqual(result["claim_family"], "codex_fallback_no_zcode_claim")
            self.assertTrue(result["usage_zero_guard"])

    def test_auto_route_codex_fallback_classifies_provider_overload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._fixture_workspace(root)
            self.assertEqual(main(["install-repo", "--repo", str(repo)]), 0)
            fake_controller = root / "fake-zcodectl.mjs"
            fake_controller.write_text(
                """#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
const out = process.argv[process.argv.indexOf("--out") + 1];
const payload = {
  ok: false,
  status: "provider_error",
  supervisor_state: "retryable_provider_error",
  provider_error_kind: "provider_overload",
  worker_usage_status: "unavailable",
  worker_total_tokens: null,
  strict_accepted: null,
  audit: { ok: false, changed_count: 0, validation: { ok: false } }
};
fs.mkdirSync(path.dirname(out), { recursive: true });
fs.writeFileSync(out, JSON.stringify(payload) + "\\n");
process.exit(1);
""",
                encoding="utf-8",
            )

            exit_code, result = self._main_json(
                [
                    "auto-route",
                    "--workspace",
                    str(repo),
                    "--objective",
                    "fix src/app.js",
                    "--allowed",
                    "src/app.js",
                    "--validation",
                    "python3 -c 'print(42)'",
                    "--execute",
                    "--trusted-zcodectl",
                    str(fake_controller),
                    "--continue-with-codex-fallback",
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(result["route_used"], "codex_fallback")
            self.assertEqual(result["fallback_reason"], "provider_overload")
            self.assertEqual(result["provider_error_kind"], "provider_overload")
            self.assertEqual(result["zcode_usage_status"], "unavailable")
            self.assertIsNone(result["zcode_total_tokens"])
            self.assertFalse(result["task_acceptance_ready"])

    def test_auto_route_codex_fallback_classifies_validation_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._fixture_workspace(root)
            self.assertEqual(main(["install-repo", "--repo", str(repo)]), 0)
            fake_controller = root / "fake-zcodectl.mjs"
            fake_controller.write_text(
                """#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
const out = process.argv[process.argv.indexOf("--out") + 1];
const payload = {
  ok: false,
  status: "audit_failed",
  supervisor_state: "audit_failed",
  validation_ok: false,
  worker_usage_status: "unavailable",
  worker_total_tokens: null,
  strict_accepted: false,
  audit: {
    ok: false,
    changed_count: 1,
    validation: { ok: false, returncode: 1, stderr_tail: "strict label mismatch" }
  }
};
fs.mkdirSync(path.dirname(out), { recursive: true });
fs.writeFileSync(out, JSON.stringify(payload) + "\\n");
process.exit(1);
""",
                encoding="utf-8",
            )

            exit_code, result = self._main_json(
                [
                    "auto-route",
                    "--workspace",
                    str(repo),
                    "--objective",
                    "fix src/app.js",
                    "--allowed",
                    "src/app.js",
                    "--validation",
                    "python3 -c 'print(42)'",
                    "--execute",
                    "--trusted-zcodectl",
                    str(fake_controller),
                    "--continue-with-codex-fallback",
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(result["route_used"], "codex_fallback")
            self.assertEqual(result["fallback_reason"], "validation_failure")
            self.assertFalse(result["strict_accepted"])
            self.assertFalse(result["strict_gate_weakened"])
            self.assertFalse(result["task_acceptance_ready"])

    def test_auto_route_compact_summary_includes_validation_failure_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._fixture_workspace(root)
            self.assertEqual(main(["install-repo", "--repo", str(repo)]), 0)
            fake_controller = root / "fake-zcodectl.mjs"
            fake_controller.write_text(
                """#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
const out = process.argv[process.argv.indexOf("--out") + 1];
const payload = {
  ok: false,
  status: "audit_failed",
  supervisor_state: "audit_failed",
  attempt_count: 1,
  max_attempts: 1,
  validation_ok: false,
  audit_ok: false,
  changed_count: 1,
  audit: {
    ok: false,
    changed_count: 1,
    validation: {
      ok: false,
      returncode: 1,
      stdout_tail: "",
      stderr_tail: "AssertionError: expected exact reason label"
    }
  }
};
fs.mkdirSync(path.dirname(out), { recursive: true });
fs.writeFileSync(out, JSON.stringify(payload) + "\\n");
process.exit(1);
""",
                encoding="utf-8",
            )

            exit_code, result = self._main_json(
                [
                    "auto-route",
                    "--workspace",
                    str(repo),
                    "--objective",
                    "fix src/app.js",
                    "--allowed",
                    "src/app.js",
                    "--validation",
                    "python3 -c 'print(42)'",
                    "--execute",
                    "--max-attempts",
                    "1",
                    "--trusted-zcodectl",
                    str(fake_controller),
                ]
            )

            self.assertEqual(exit_code, 1)
            self.assertFalse(result["ok"])
            self.assertNotIn("run_result", result)
            summary = result["run_result_summary"]
            self.assertEqual(summary["validation_returncode"], 1)
            self.assertIn("expected exact reason label", summary["validation_stderr_tail"])

    def test_auto_route_compact_summary_propagates_run_packet_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._fixture_workspace(root)
            self.assertEqual(main(["install-repo", "--repo", str(repo)]), 0)
            fake_controller = root / "fake-zcodectl.mjs"
            fake_controller.write_text(
                """#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
const out = process.argv[process.argv.indexOf("--out") + 1];
const payload = {
  ok: false,
  status: "run_timeout",
  supervisor_state: "run_timeout",
  timed_out: true,
  attempt_count: 1,
  max_attempts: 1,
  validation_ok: false,
  audit_ok: false,
  changed_count: 1,
  audit: {
    ok: false,
    changed_count: 1,
    validation: {
      ok: false,
      returncode: 1,
      stdout_tail: "",
      stderr_tail: "AssertionError: expected 447.66"
    }
  }
};
fs.mkdirSync(path.dirname(out), { recursive: true });
fs.writeFileSync(out, JSON.stringify(payload) + "\\n");
process.exit(1);
""",
                encoding="utf-8",
            )

            exit_code, result = self._main_json(
                [
                    "auto-route",
                    "--workspace",
                    str(repo),
                    "--objective",
                    "fix src/app.js",
                    "--allowed",
                    "src/app.js",
                    "--validation",
                    "python3 -c 'print(42)'",
                    "--execute",
                    "--trusted-zcodectl",
                    str(fake_controller),
                ]
            )

            self.assertEqual(exit_code, 1)
            self.assertFalse(result["ok"])
            self.assertTrue(result["timed_out"])
            self.assertEqual(result["failure_reason"], "run_timeout")
            summary = result["run_result_summary"]
            self.assertEqual(summary["supervisor_state"], "run_timeout")
            self.assertIn("expected 447.66", summary["validation_stderr_tail"])

    def test_auto_route_execute_prefers_run_file_when_stdout_is_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._fixture_workspace(root)
            self.assertEqual(main(["install-repo", "--repo", str(repo)]), 0)
            fake_controller = root / "fake-zcodectl.mjs"
            fake_controller.write_text(
                """#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
	const out = process.argv[process.argv.indexOf("--out") + 1];
	const payload = {
		ok: true,
		supervisor_state: "success",
		validation_ok: true,
		audit: { ok: true, changed_count: 1, validation: { ok: true } }
	};
	fs.mkdirSync(path.dirname(out), { recursive: true });
	fs.writeFileSync(out, JSON.stringify(payload) + "\\n");
	process.stdout.write(JSON.stringify({ changed_files: ["src/app.js"] }) + "\\n");
""",
                encoding="utf-8",
            )

            exit_code, result = self._main_json(
                [
                    "auto-route",
                    "--workspace",
                    str(repo),
                    "--objective",
                    "fix src/app.js",
                    "--allowed",
                    "src/app.js",
                    "--validation",
                    "python3 -c 'print(42)'",
                    "--execute",
                    "--max-attempts",
                    "1",
                    "--retry-delay-ms",
                    "0",
                    "--trusted-zcodectl",
                    str(fake_controller),
                    "--result-verbosity",
                    "full",
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(result["ok"])
            self.assertEqual(result["run_result_source"], "run_file")
            self.assertEqual(result["run_result"]["supervisor_state"], "success")
            self.assertEqual(result["zcode_changed_count"], 1)

    def test_auto_route_execute_rejects_missing_run_file_even_when_stdout_is_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._fixture_workspace(root)
            self.assertEqual(main(["install-repo", "--repo", str(repo)]), 0)
            fake_controller = root / "fake-zcodectl.mjs"
            fake_controller.write_text(
                """#!/usr/bin/env node
	process.stdout.write(JSON.stringify({
		ok: true,
		supervisor_state: "success",
		validation_ok: true,
		audit: { ok: true, changed_count: 1, validation: { ok: true } }
	}) + "\\n");
""",
                encoding="utf-8",
            )

            exit_code, result = self._main_json(
                [
                    "auto-route",
                    "--workspace",
                    str(repo),
                    "--objective",
                    "fix src/app.js",
                    "--allowed",
                    "src/app.js",
                    "--validation",
                    "python3 -c 'print(42)'",
                    "--execute",
                    "--max-attempts",
                    "1",
                    "--retry-delay-ms",
                    "0",
                    "--trusted-zcodectl",
                    str(fake_controller),
                ]
            )

            self.assertEqual(exit_code, 1)
            self.assertFalse(result["ok"])
            self.assertEqual(result["failure_reason"], "run_json_missing")
            self.assertFalse(result["run_file_exists"])
            self.assertEqual(result["run_result_source"], "stdout")

    def test_auto_route_execute_removes_stale_run_file_before_controller(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._fixture_workspace(root)
            self.assertEqual(main(["install-repo", "--repo", str(repo)]), 0)
            marker = root / "called-once"
            fake_controller = root / "fake-zcodectl.mjs"
            fake_controller.write_text(
                f"""#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
	const out = process.argv[process.argv.indexOf("--out") + 1];
	const payload = {{
		ok: true,
		supervisor_state: "success",
		validation_ok: true,
		audit: {{ ok: true, changed_count: 1, validation: {{ ok: true }} }}
	}};
	if (!fs.existsSync({json.dumps(str(marker))})) {{
		fs.writeFileSync({json.dumps(str(marker))}, "called");
		fs.mkdirSync(path.dirname(out), {{ recursive: true }});
		fs.writeFileSync(out, JSON.stringify(payload) + "\\n");
	}} else {{
		process.stdout.write(JSON.stringify(payload) + "\\n");
	}}
""",
                encoding="utf-8",
            )
            original_utc_slug = auto_route.utc_slug
            auto_route.utc_slug = lambda: "fixed-stale-run"
            try:
                base_args = [
                    "auto-route",
                    "--workspace",
                    str(repo),
                    "--objective",
                    "fix src/app.js",
                    "--allowed",
                    "src/app.js",
                    "--validation",
                    "python3 -c 'print(42)'",
                    "--execute",
                    "--max-attempts",
                    "1",
                    "--retry-delay-ms",
                    "0",
                    "--trusted-zcodectl",
                    str(fake_controller),
                ]

                first_exit_code, first_result = self._main_json(base_args)
                second_exit_code, second_result = self._main_json(base_args)
            finally:
                auto_route.utc_slug = original_utc_slug

            self.assertEqual(first_exit_code, 0)
            self.assertTrue(first_result["ok"])
            self.assertTrue(first_result["run_file_exists"])
            self.assertEqual(second_exit_code, 1)
            self.assertFalse(second_result["ok"])
            self.assertEqual(second_result["failure_reason"], "run_json_missing")
            self.assertFalse(second_result["run_file_exists"])
            self.assertEqual(second_result["run_result_source"], "stdout")

    def test_auto_route_parent_timeout_accounts_for_usage_snapshot_fallbacks(self):
        args = SimpleNamespace(
            max_attempts=2,
            retry_delay_ms=60000,
            timeout_ms=600000,
            validation_timeout=60,
            usage_snapshot_timeout_ms=20000,
            usage_snapshot_source="auto",
        )

        self.assertEqual(auto_route.run_command_timeout_seconds(args, {}), 1490)

        args.usage_snapshot_source = "zai-api"
        self.assertEqual(auto_route.run_command_timeout_seconds(args, {}), 1450)

        args.usage_snapshot_source = "none"
        self.assertEqual(auto_route.run_command_timeout_seconds(args, {}), 1410)

    def test_run_json_command_timeout_terminates_child_process_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child_pid = root / "child.pid"
            child_marker = root / "child.terminated"
            child_script = root / "child.py"
            child_script.write_text(
                """
import signal
import sys
import time
from pathlib import Path

marker = Path(sys.argv[1])

def handle_term(signum, frame):
    marker.write_text("terminated", encoding="utf-8")
    raise SystemExit(0)

signal.signal(signal.SIGTERM, handle_term)
while True:
    time.sleep(1)
""".lstrip(),
                encoding="utf-8",
            )
            parent_script = root / "parent.py"
            parent_script.write_text(
                f"""
import subprocess
import sys
import time
from pathlib import Path

child = subprocess.Popen([sys.executable, {json.dumps(str(child_script))}, {json.dumps(str(child_marker))}])
Path({json.dumps(str(child_pid))}).write_text(str(child.pid), encoding="utf-8")
print('{{"status":"started"}}', flush=True)
while True:
    time.sleep(1)
""".lstrip(),
                encoding="utf-8",
            )

            try:
                rc, parsed, stdout, stderr, timed_out = auto_route.run_json_command(
                    [sys.executable, str(parent_script)],
                    root,
                    timeout_seconds=0.5,
                )

                self.assertEqual(rc, 124)
                self.assertTrue(timed_out)
                self.assertIsNone(parsed)
                self.assertIn("started", stdout)
                self.assertIn("command timed out", stderr)
                self.assertTrue(self._wait_for_path(child_marker))
            finally:
                if child_pid.exists() and not child_marker.exists():
                    try:
                        os.kill(int(child_pid.read_text(encoding="utf-8")), signal.SIGKILL)
                    except (OSError, ValueError):
                        pass

    def test_auto_route_execute_rejects_successful_no_change_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._fixture_workspace(root)
            self.assertEqual(main(["install-repo", "--repo", str(repo)]), 0)
            fake_controller = root / "fake-zcodectl.mjs"
            fake_controller.write_text(
                """#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
	const out = process.argv[process.argv.indexOf("--out") + 1];
	const payload = {
		ok: true,
		supervisor_state: "success",
		validation_ok: true,
		audit: { ok: true, changed_count: 0, validation: { ok: true } }
	};
	fs.mkdirSync(path.dirname(out), { recursive: true });
	fs.writeFileSync(out, JSON.stringify(payload) + "\\n");
	process.stdout.write("No changes were needed.\\n");
""",
                encoding="utf-8",
            )

            exit_code, result = self._main_json(
                [
                    "auto-route",
                    "--workspace",
                    str(repo),
                    "--objective",
                    "fix src/app.js",
                    "--allowed",
                    "src/app.js",
                    "--validation",
                    "python3 -c 'print(42)'",
                    "--execute",
                    "--max-attempts",
                    "1",
                    "--retry-delay-ms",
                    "0",
                    "--trusted-zcodectl",
                    str(fake_controller),
                ]
            )

            self.assertEqual(exit_code, 1)
            self.assertFalse(result["ok"])
            self.assertEqual(result["failure_reason"], "zcode_no_changes")
            self.assertEqual(result["zcode_changed_count"], 0)
            self.assertFalse(result["zcode_did_work"])

    def test_auto_route_rejects_repo_controlled_output_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._fixture_workspace(root)
            self.assertEqual(main(["install-repo", "--repo", str(repo)]), 0)
            routing = repo / ".codex/zcode-routing.json"
            payload = json.loads(routing.read_text(encoding="utf-8"))
            payload["paths"]["packets"] = "../outside"
            routing.write_text(json.dumps(payload), encoding="utf-8")

            exit_code, result = self._main_json(
                [
                    "auto-route",
                    "--workspace",
                    str(repo),
                    "--objective",
                    "fix src/app.js",
                    "--allowed",
                    "src/app.js",
                    "--validation",
                    "python3 -c 'print(42)'",
                    "--execute",
                ]
            )

            self.assertEqual(exit_code, 1)
            self.assertFalse(result["ok"])
            self.assertIn("escapes workspace", result["error"])

    def _fixture_workspace(self, root: Path) -> Path:
        workspace = root / "workspace"
        (workspace / "src").mkdir(parents=True)
        (workspace / "src/app.js").write_text("export const value = 1;\n", encoding="utf-8")
        (workspace / "README.md").write_text("fixture\n", encoding="utf-8")
        return workspace


if __name__ == "__main__":
    unittest.main()
