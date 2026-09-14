from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = ROOT / "tools" / "zcode_control" / "zcodectl.mjs"


class ZCodeCtlCrossPlatformTests(unittest.TestCase):
    def test_custom_api_profile_can_be_listed_and_bootstrapped_without_printing_its_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_config = root / "desktop.json"
            cli_config = root / "cli.json"
            test_key = "test-key-that-must-not-be-printed"
            source_config.write_text(
                json.dumps(
                    {
                        "provider": {
                            "tokenrhythm": {
                                "name": "基元律动 (Token Rhythm)",
                                "kind": "openai-compatible",
                                "options": {
                                    "apiKey": test_key,
                                    "apiKeyRequired": True,
                                    "baseURL": "https://example.invalid/v1",
                                },
                                "models": {"glm-5.3-flash": {"name": "GLM-5.3-Flash"}},
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            listed = subprocess.run(
                [
                    "node",
                    str(CONTROLLER),
                    "api-profiles",
                    "--source-config",
                    str(source_config),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertNotIn(test_key, listed.stdout)
            self.assertNotIn("example.invalid", listed.stdout)
            profiles = json.loads(listed.stdout)["profiles"]
            self.assertEqual(profiles[0]["id"], "tokenrhythm")
            self.assertEqual(profiles[0]["models"], ["glm-5.3-flash"])

            bootstrapped = subprocess.run(
                [
                    "node",
                    str(CONTROLLER),
                    "bootstrap-cli-config",
                    "--provider",
                    "tokenrhythm",
                    "--model",
                    "glm-5.3-flash",
                    "--source-config",
                    str(source_config),
                    "--cli-config",
                    str(cli_config),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(bootstrapped.returncode, 0, bootstrapped.stderr)
            self.assertNotIn(test_key, bootstrapped.stdout)
            payload = json.loads(bootstrapped.stdout)
            self.assertEqual(payload["target"]["main_model"], "tokenrhythm/glm-5.3-flash")
            saved = json.loads(cli_config.read_text(encoding="utf-8"))
            self.assertEqual(saved["model"]["main"], "tokenrhythm/glm-5.3-flash")
            self.assertEqual(saved["provider"]["tokenrhythm"]["options"]["apiKey"], test_key)

    def test_custom_api_bootstrap_preserves_headers_exact_model_id_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_config = root / "desktop.json"
            cli_config = root / "cli.json"
            source_config.write_text(
                json.dumps(
                    {
                        "provider": {
                            "customcase": {
                                "kind": "openai-compatible",
                                "options": {
                                    "apiKey": "fixture-key",
                                    "apiKeyRequired": True,
                                    "baseURL": "https://example.invalid/v1",
                                    "headers": {"X-Route": "required"},
                                },
                                "models": {
                                    "Vendor/Model-X": {
                                        "name": "Vendor Model X",
                                        "limit": {"context": 123456},
                                        "modalities": {"input": ["text", "image"]},
                                    }
                                },
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    "node",
                    str(CONTROLLER),
                    "bootstrap-cli-config",
                    "--provider",
                    "customcase",
                    "--model",
                    "vendor/model-x",
                    "--source-config",
                    str(source_config),
                    "--cli-config",
                    str(cli_config),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            saved = json.loads(cli_config.read_text(encoding="utf-8"))
            provider = saved["provider"]["customcase"]
            self.assertEqual(saved["model"]["main"], "customcase/Vendor/Model-X")
            self.assertEqual(provider["options"]["headers"], {"X-Route": "required"})
            self.assertIn("Vendor/Model-X", provider["models"])
            self.assertEqual(provider["models"]["Vendor/Model-X"]["limit"]["context"], 123456)
            self.assertEqual(provider["models"]["Vendor/Model-X"]["modalities"]["input"], ["text", "image"])

    def test_zai_alias_falls_back_past_a_stale_direct_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_config = root / "desktop.json"
            cli_config = root / "cli.json"
            source_config.write_text(
                json.dumps(
                    {
                        "provider": {
                            "zai": {
                                "kind": "anthropic",
                                "options": {"apiKey": "stale-key", "baseURL": "https://stale.invalid"},
                                "models": {"old-model": {"name": "Old model"}},
                            },
                            "builtin:zai-coding-plan": {
                                "kind": "anthropic",
                                "options": {
                                    "apiKey": "fixture-key",
                                    "baseURL": "https://working.invalid",
                                },
                                "models": {"glm-5.3": {"name": "GLM-5.3"}},
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    "node",
                    str(CONTROLLER),
                    "bootstrap-cli-config",
                    "--provider",
                    "zai",
                    "--model",
                    "glm-5.3",
                    "--source-config",
                    str(source_config),
                    "--cli-config",
                    str(cli_config),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["source"]["provider_id"], "builtin:zai-coding-plan")
            self.assertEqual(payload["target"]["main_model"], "zai/glm-5.3")

    def test_cli_preflight_uses_userprofile_when_home_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            user_home = Path(temp_dir)
            config_dir = user_home / ".zcode" / "cli"
            config_dir.mkdir(parents=True)
            (config_dir / "config.json").write_text(
                json.dumps(
                    {
                        "model": {"main": "custom/example-model"},
                        "provider": {
                            "custom": {
                                "kind": "anthropic",
                                "options": {"apiKeyRequired": True, "apiKey": "test-only"},
                                "models": {"example-model": {"name": "Example"}},
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            fake_cli = user_home / "fake-zcode.cjs"
            fake_cli.write_text(
                """
const args = process.argv.slice(2);
if (args.includes('--version')) console.log('0.0-test');
else if (args[0] === 'doctor') console.log(JSON.stringify({ ok: true, platform: process.platform }));
else process.exitCode = 2;
""".strip()
                + "\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment.pop("HOME", None)
            environment["USERPROFILE"] = str(user_home)
            environment["ZCODE_CLI_PATH"] = str(fake_cli)

            completed = subprocess.run(
                ["node", str(CONTROLLER), "cli-preflight"],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["prompt_ready"])
            self.assertEqual(payload["config"]["main_model"], "custom/example-model")
            self.assertTrue(payload["config"]["has_selected_provider_api_key"])

    def test_controller_doctor_accepts_explicit_python_executable(self) -> None:
        environment = os.environ.copy()
        environment["ZCODE_SUPERVISOR_PYTHON"] = sys.executable
        completed = subprocess.run(
            ["node", str(CONTROLLER), "doctor"],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertIn("status", payload)

    def test_zcode_child_does_not_inherit_common_secret_environment_variables(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            capture = root / "child-env.json"
            fake_cli = root / "fake-zcode.cjs"
            fake_cli.write_text(
                "const fs = require('node:fs');\n"
                "fs.writeFileSync(process.env.CAPTURE_PATH, JSON.stringify({\n"
                "  openai: process.env.OPENAI_API_KEY ?? null,\n"
                "  github: process.env.GITHUB_TOKEN ?? null,\n"
                "  awsAccess: process.env.AWS_ACCESS_KEY_ID ?? null,\n"
                "  aws: process.env.AWS_SECRET_ACCESS_KEY ?? null,\n"
                "  ssh: process.env.SSH_AUTH_SOCK ?? null\n"
                "}));\n"
                "process.stdout.write(JSON.stringify({ response: 'ok' }) + '\\n');\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment.update(
                {
                    "ZCODE_CLI_PATH": str(fake_cli),
                    "CAPTURE_PATH": str(capture),
                    "OPENAI_API_KEY": "must-not-leak",
                    "GITHUB_TOKEN": "must-not-leak",
                    "AWS_ACCESS_KEY_ID": "must-not-leak",
                    "AWS_SECRET_ACCESS_KEY": "must-not-leak",
                    "SSH_AUTH_SOCK": "must-not-leak",
                }
            )

            completed = subprocess.run(
                [
                    "node",
                    str(CONTROLLER),
                    "cli-prompt",
                    "--text",
                    "reply",
                    "--workspace",
                    str(root),
                    "--mode",
                    "plan",
                    "--no-bootstrap",
                ],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                json.loads(capture.read_text(encoding="utf-8")),
                {"openai": None, "github": None, "awsAccess": None, "aws": None, "ssh": None},
            )

    def test_run_packet_blocks_secret_paths_before_starting_zcode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / ".env").write_text("SECRET=fixture\n", encoding="utf-8")
            invoked = root / "zcode-invoked"
            fake_cli = root / "fake-zcode.cjs"
            fake_cli.write_text(
                "const fs = require('node:fs');\n"
                "fs.writeFileSync(process.env.INVOKED_PATH, 'yes');\n",
                encoding="utf-8",
            )
            packet = root / "packet.json"
            packet.write_text(
                json.dumps(
                    {
                        "prompt": "bounded task",
                        "workspace": str(workspace),
                        "allowed_files": ["app.js"],
                        "forbidden_files": [],
                        "validation": f'"{sys.executable}" -c "raise SystemExit(0)"',
                        "mode": "Auto Edit",
                        "vision": {"required": False, "image_files": []},
                    }
                ),
                encoding="utf-8",
            )
            output = root / "run.json"
            environment = os.environ.copy()
            environment["ZCODE_CLI_PATH"] = str(fake_cli)
            environment["INVOKED_PATH"] = str(invoked)

            completed = subprocess.run(
                [
                    "node",
                    str(CONTROLLER),
                    "run-packet",
                    "--packet",
                    str(packet),
                    "--no-bootstrap",
                    "--usage-snapshot-source",
                    "none",
                    "--out",
                    str(output),
                ],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            self.assertNotEqual(completed.returncode, 0)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "workspace_secret_paths_blocked")
            self.assertEqual(payload["secret_files"], [".env"])
            self.assertFalse(invoked.exists())

    @unittest.skipUnless(os.name == "nt" and shutil.which("pwsh"), "Windows PowerShell regression")
    def test_windows_setup_skip_bootstrap_does_not_require_desktop_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", str(repo)], check=True, capture_output=True, text=True)
            cli_config = root / ".zcode" / "cli" / "config.json"
            cli_config.parent.mkdir(parents=True)
            cli_config.write_text(
                json.dumps(
                    {
                        "model": {"main": "custom/example-model"},
                        "provider": {
                            "custom": {
                                "kind": "anthropic",
                                "options": {"apiKeyRequired": True, "apiKey": "fixture-key"},
                                "models": {"example-model": {"name": "Example"}},
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            fake_cli = root / "fake-zcode.cjs"
            fake_cli.write_text(
                "const args = process.argv.slice(2);\n"
                "if (args.includes('--version')) console.log('0.0-test');\n"
                "else if (args[0] === 'doctor') console.log(JSON.stringify({ ok: true }));\n"
                "else process.exitCode = 2;\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["HOME"] = str(root)
            environment["USERPROFILE"] = str(root)
            environment["ZCODE_CLI_PATH"] = str(fake_cli)

            completed = subprocess.run(
                [
                    "pwsh",
                    "-NoProfile",
                    "-File",
                    str(ROOT / "scripts/setup-windows.ps1"),
                    "-Repo",
                    str(repo),
                    "-SkipCliBootstrap",
                    "-SkipVisionMcp",
                    "-NoWriteAgents",
                ],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue((repo / ".codex/zcode-routing.json").exists())


if __name__ == "__main__":
    unittest.main()
