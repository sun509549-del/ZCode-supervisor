import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ZCODECTL = ROOT / "tools/zcode_control/zcodectl.mjs"
SUPERVISOR = ROOT / "tools/zcode_supervisor/zcode_supervisor.py"

sys.path.insert(0, str(ROOT))
from tools.zcode_eval.strict_contract_comparison import (  # noqa: E402
    PROVIDER_SUCCESS_WITHOUT_USAGE_PAYLOAD_REASON,
    WORKER_USAGE_EMPTY_SIDECAR_REASON,
    preserve_worker_usage_sidecar,
    zcode_worker_usage_fields,
)


class ZcodectlProviderUsageTests(unittest.TestCase):
    def make_workspace(self, root: Path) -> tuple[Path, Path]:
        workspace = root / "workspace"
        (workspace / "src").mkdir(parents=True)
        (workspace / "src/app.js").write_text("before\n", encoding="utf-8")
        (workspace / "check.py").write_text(
            "from pathlib import Path\n"
            "raise SystemExit(0 if Path('src/app.js').read_text() == 'after\\n' else 1)\n",
            encoding="utf-8",
        )
        packet = root / "packet.json"
        subprocess.run(
            [
                sys.executable,
                str(SUPERVISOR),
                "packet",
                "--workspace",
                str(workspace),
                "--objective",
                "synthetic provider usage fixture",
                "--allowed",
                "src/app.js",
                "--validation",
                "python3 check.py",
                "--out",
                str(packet),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        return workspace, packet

    def write_fake_cli(self, root: Path, stdout_lines: list[dict]) -> Path:
        cli = root / "fake-zcode.cjs"
        source = [
            "#!/usr/bin/env node",
            "const fs = require('node:fs');",
            "const path = require('node:path');",
            "const cwdIndex = process.argv.indexOf('--cwd');",
            "const cwd = cwdIndex >= 0 ? process.argv[cwdIndex + 1] : process.cwd();",
            "fs.writeFileSync(path.join(cwd, 'src/app.js'), 'after\\n');",
            "process.stdout.write('provider progress\\n');",
        ]
        for payload in stdout_lines:
            source.append(f"process.stdout.write({json.dumps(json.dumps(payload) + chr(10))});")
        cli.write_text("\n".join(source) + "\n", encoding="utf-8")
        cli.chmod(0o755)
        return cli

    def run_packet(self, packet: Path, cli: Path, extra_env: dict[str, str] | None = None) -> dict:
        out = packet.parent / "zcode-run.json"
        env = {
            **os.environ,
            "ZCODE_CLI_PATH": str(cli),
            **(extra_env or {}),
        }
        subprocess.run(
            [
                "node",
                str(ZCODECTL),
                "run-packet",
                "--packet",
                str(packet),
                "--max-attempts",
                "1",
                "--retry-delay-ms",
                "0",
                "--no-bootstrap",
                "--usage-snapshot-source",
                "none",
                "--out",
                str(out),
            ],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        return json.loads(out.read_text(encoding="utf-8"))

    def test_nested_openai_style_provider_usage_becomes_measured_worker_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, packet = self.make_workspace(root)
            cli = self.write_fake_cli(
                root,
                [
                    {
                        "type": "response.completed",
                        "provider": "zai",
                        "response": {
                            "model": "glm-5.2",
                            "usage": {
                                "input_tokens": 31,
                                "output_tokens": 11,
                                "output_tokens_details": {"reasoning_tokens": 5},
                                "total_tokens": 42,
                            },
                        },
                    }
                ],
            )

            run_payload = self.run_packet(packet, cli)
            run_json = packet.parent / "zcode-run.json"
            sidecar = preserve_worker_usage_sidecar(run_json)
            updated = json.loads(run_json.read_text(encoding="utf-8"))
            fields = zcode_worker_usage_fields(updated, run_json)
            sidecar_payload = json.loads(sidecar.read_text(encoding="utf-8"))

            self.assertTrue(run_payload["usage_accounting"]["usage_available"])
            self.assertEqual(run_payload["usage_accounting"]["tokens_source"], "zcode_cli_json_usage")
            self.assertEqual(run_payload["usage_accounting"]["tokens_used"], 42)
            self.assertEqual(run_payload["usage_accounting"]["input_tokens"], 31)
            self.assertEqual(run_payload["usage_accounting"]["output_tokens"], 11)
            self.assertEqual(run_payload["usage_accounting"]["reasoning_tokens"], 5)
            self.assertEqual(updated["usage_accounting"]["worker_usage_source_path"], "worker-usage.json")
            self.assertEqual(sidecar_payload["source_type"], "zcode_cli_json_usage")
            self.assertEqual(sidecar_payload["unit"], "tokens")
            self.assertEqual(sidecar_payload["usage"]["total_tokens"], 42)
            self.assertEqual(fields["worker_usage_status"], "measured")
            self.assertEqual(fields["worker_usage_unit"], "tokens")
            self.assertEqual(fields["worker_total_tokens"], 42)

    def test_zcodectl_appends_measured_stdout_usage_to_provider_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, packet = self.make_workspace(root)
            cli = self.write_fake_cli(
                root,
                [
                    {
                        "type": "response.completed",
                        "provider": "zai",
                        "response": {
                            "model": "glm-5.2",
                            "usage": {
                                "input_tokens": 31,
                                "output_tokens": 11,
                                "output_tokens_details": {"reasoning_tokens": 5},
                                "total_tokens": 42,
                            },
                        },
                    }
                ],
            )
            ledger = packet.parent / "worker-usage.jsonl"
            ledger.write_text(
                json.dumps(
                    {
                        "source_type": "provider_usage_ledger",
                        "unit": "tokens",
                        "usage": {"total_tokens": 1},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            run_payload = self.run_packet(packet, cli, {"ZCODE_PROVIDER_USAGE_LEDGER": str(ledger)})
            records = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
            appended = records[-1]

            self.assertEqual(len(records), 2)
            self.assertTrue(run_payload["attempt_results"][0]["provider_usage_ledger"]["appended"])
            self.assertEqual(run_payload["attempt_results"][0]["provider_usage_ledger"]["status"], "appended")
            self.assertEqual(run_payload["provider_usage_ledger_records_appended"], 1)
            self.assertEqual(appended["source_type"], "provider_usage_ledger")
            self.assertEqual(appended["unit"], "tokens")
            self.assertEqual(appended["usage"]["total_tokens"], 42)
            self.assertEqual(appended["usage"]["input_tokens"], 31)
            self.assertEqual(appended["usage"]["output_tokens"], 11)
            self.assertEqual(appended["usage"]["reasoning_tokens"], 5)
            self.assertEqual(appended["provider"], "zai")
            self.assertEqual(appended["model"], "glm-5.2")
            self.assertEqual(appended["row_id"], packet.parent.name)
            self.assertEqual(appended["provider_call_index"], 1)

    def test_split_token_usage_without_explicit_total_gets_canonical_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, packet = self.make_workspace(root)
            cli = self.write_fake_cli(
                root,
                [
                    {
                        "response": {
                            "usage": {
                                "prompt_tokens": 10,
                                "completion_tokens": 2,
                            }
                        }
                    }
                ],
            )

            run_payload = self.run_packet(packet, cli)

            self.assertTrue(run_payload["usage_accounting"]["usage_available"])
            self.assertEqual(run_payload["usage_accounting"]["tokens_used"], 12)
            self.assertEqual(run_payload["usage_normalized"]["total_tokens"], 12)

    def test_empty_usage_sidecar_remains_blocked_and_not_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_json = Path(tmp) / "tasks/policy/zcode-delegated/zcode-run.json"
            run_json.parent.mkdir(parents=True)
            run_json.write_text(
                json.dumps({"usage": {}, "usage_accounting": {"no_usage_reason": "zcode_cli_usage_missing"}}),
                encoding="utf-8",
            )

            sidecar = preserve_worker_usage_sidecar(run_json)
            updated = json.loads(run_json.read_text(encoding="utf-8"))
            fields = zcode_worker_usage_fields(updated, run_json)
            sidecar_payload = json.loads(sidecar.read_text(encoding="utf-8"))

            self.assertEqual(sidecar_payload["usage"], {})
            self.assertEqual(fields["worker_usage_status"], "unavailable")
            self.assertEqual(fields["worker_usage_unit"], "unknown")
            self.assertEqual(fields["worker_usage_no_usage_reason"], WORKER_USAGE_EMPTY_SIDECAR_REASON)
            self.assertIsNone(fields["worker_total_tokens"])

    def test_provider_success_without_usage_payload_gets_precise_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, packet = self.make_workspace(root)
            cli = self.write_fake_cli(root, [])

            run_payload = self.run_packet(packet, cli)
            run_json = packet.parent / "zcode-run.json"
            sidecar = preserve_worker_usage_sidecar(run_json)
            updated = json.loads(run_json.read_text(encoding="utf-8"))
            fields = zcode_worker_usage_fields(updated, run_json)
            sidecar_payload = json.loads(sidecar.read_text(encoding="utf-8"))

            self.assertFalse(run_payload["usage_accounting"]["usage_available"])
            self.assertEqual(run_payload["usage_accounting"]["no_usage_reason"], PROVIDER_SUCCESS_WITHOUT_USAGE_PAYLOAD_REASON)
            self.assertEqual(sidecar_payload["usage"], {})
            self.assertEqual(sidecar_payload["no_usage_reason"], PROVIDER_SUCCESS_WITHOUT_USAGE_PAYLOAD_REASON)
            self.assertEqual(fields["worker_usage_status"], "unavailable")
            self.assertEqual(fields["worker_usage_no_usage_reason"], PROVIDER_SUCCESS_WITHOUT_USAGE_PAYLOAD_REASON)
            self.assertTrue(fields["worker_usage_empty_sidecar"])
            self.assertIsNone(fields["worker_total_tokens"])

    def test_provider_usage_ledger_env_is_row_scoped_and_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, packet = self.make_workspace(root)
            cli = root / "fake-zcode-ledger.cjs"
            cli.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env node",
                        "const fs = require('node:fs');",
                        "const path = require('node:path');",
                        "const cwdIndex = process.argv.indexOf('--cwd');",
                        "const cwd = cwdIndex >= 0 ? process.argv[cwdIndex + 1] : process.cwd();",
                        "fs.writeFileSync(path.join(cwd, 'src/app.js'), 'after\\n');",
                        "const ledger = process.env.ZCODE_PROVIDER_USAGE_LEDGER;",
                        "if (!ledger) process.exit(33);",
                        "fs.mkdirSync(path.dirname(ledger), { recursive: true });",
                        "fs.appendFileSync(ledger, JSON.stringify({",
                        "  source_type: 'provider_usage_ledger',",
                        "  provider: 'zai',",
                        "  model: 'glm-ledger',",
                        "  unit: 'tokens',",
                        "  usage: { total_tokens: 58, input_tokens: 40, output_tokens: 18 }",
                        "}) + '\\n');",
                        "process.stdout.write('provider completed without stdout usage\\n');",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            cli.chmod(0o755)
            ledger = packet.parent / "worker-usage.jsonl"

            run_payload = self.run_packet(packet, cli, {"ZCODE_PROVIDER_USAGE_LEDGER": str(ledger)})
            sidecar = preserve_worker_usage_sidecar(
                packet.parent / "zcode-run.json",
                {"ZCODE_PROVIDER_USAGE_LEDGER": str(ledger)},
            )
            updated = json.loads((packet.parent / "zcode-run.json").read_text(encoding="utf-8"))
            fields = zcode_worker_usage_fields(updated, packet.parent / "zcode-run.json")

            self.assertFalse(run_payload["usage_accounting"]["usage_available"])
            self.assertEqual(sidecar, ledger)
            self.assertEqual(updated["usage_accounting"]["worker_usage_source_path"], "worker-usage.jsonl")
            self.assertEqual(fields["worker_usage_status"], "measured")
            self.assertEqual(fields["worker_usage_capture_method"], "provider_usage_ledger")
            self.assertEqual(fields["worker_usage_unit"], "tokens")
            self.assertEqual(fields["worker_total_tokens"], 58)
            self.assertEqual(fields["worker_provider"], "zai")
            self.assertEqual(fields["worker_model"], "glm-ledger")


if __name__ == "__main__":
    unittest.main()
