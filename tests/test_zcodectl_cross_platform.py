from __future__ import annotations

import json
import os
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


if __name__ == "__main__":
    unittest.main()
