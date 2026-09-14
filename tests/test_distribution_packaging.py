import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import tomllib

from tools import zcode_control, zcode_dashboard
from scripts import verify_python_wheel_for_tests


ROOT = Path(__file__).resolve().parents[1]


class DistributionPackagingTests(unittest.TestCase):
    def test_pyproject_declares_uvx_safe_console_scripts_and_node_assets(self):
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        scripts = pyproject["project"]["scripts"]
        self.assertEqual(
            scripts["zcode-install-repo"],
            "tools.zcode_supervisor.repo_setup:install_repo_entrypoint",
        )
        self.assertEqual(scripts["zcode-auto-route"], "tools.zcode_supervisor.auto_route:auto_route_entrypoint")
        self.assertEqual(scripts["zcodectl"], "tools.zcode_control:main")
        self.assertEqual(scripts["zcode-dashboard"], "tools.zcode_dashboard:main")
        self.assertEqual(pyproject["tool"]["setuptools"]["package-data"]["tools.zcode_control"], ["*.mjs"])
        self.assertEqual(
            pyproject["tool"]["setuptools"]["package-data"]["tools.zcode_dashboard"],
            ["*.mjs", "public/*"],
        )
        self.assertEqual(
            pyproject["tool"]["setuptools"]["package-data"]["tools.zcode_eval"],
            ["fixtures/*.png", "rubrics/*.json"],
        )

    def test_strict_contract_breakdown_import_does_not_require_pillow(self):
        script = """
import builtins

real_import = builtins.__import__

def blocked_import(name, *args, **kwargs):
    if name == "PIL" or name.startswith("PIL."):
        raise AssertionError("strict_contract_breakdown imported Pillow")
    return real_import(name, *args, **kwargs)

builtins.__import__ = blocked_import
from tools.zcode_eval.strict_contract_breakdown import create_vision_fixture
print(create_vision_fixture.__name__)
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("create_vision_fixture", result.stdout)

    def test_packaged_rubrics_match_documented_rubrics(self):
        documented = ROOT / "docs" / "zcode-strict-contract-v3" / "rubrics"
        packaged = ROOT / "tools" / "zcode_eval" / "rubrics"

        self.assertEqual(
            [path.name for path in sorted(documented.glob("*.json"))],
            [path.name for path in sorted(packaged.glob("*.json"))],
        )
        for path in documented.glob("*.json"):
            self.assertEqual(path.read_bytes(), (packaged / path.name).read_bytes(), path.name)

    def test_pypi_workflow_uses_trusted_publishing_and_build_only_default(self):
        workflow = (ROOT / ".github/workflows/pypi-publish.yml").read_text(encoding="utf-8")

        self.assertIn("default: build-only", workflow)
        self.assertIn("trusted_publishers_configured:", workflow)
        self.assertIn("environment:\n      name: testpypi", workflow)
        self.assertIn("environment:\n      name: pypi", workflow)
        self.assertIn("id-token: write", workflow)
        self.assertIn("Check TestPyPI release readiness", workflow)
        self.assertIn("Check PyPI release readiness", workflow)
        self.assertIn("scripts/check-pypi-release-readiness", workflow)
        self.assertIn("trusted_publishers_configured=true", workflow)
        self.assertIn("- preflight-testpypi", workflow)
        self.assertIn("- preflight-pypi", workflow)
        self.assertIn(
            "pypa/gh-action-pypi-publish@"
            "cef221092ed1bacb1cc03d23a2d87d1d172e277b # release/v1",
            workflow,
        )
        self.assertIn("repository-url: https://test.pypi.org/legacy/", workflow)
        self.assertNotIn("PYPI" "_TOKEN", workflow)
        self.assertNotIn("pass" "word:", workflow)

    def test_verify_python_wheel_rejects_missing_node_controller_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            wheel = Path(tmp) / "zcode_supervisor-0.0.1-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(
                    "zcode_supervisor-0.0.1.dist-info/entry_points.txt",
                    "\n".join(sorted(verify_python_wheel_for_tests.REQUIRED_ENTRY_POINTS)),
                )

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(verify_python_wheel_for_tests.main([str(wheel)]), 1)

    def test_zcodectl_wrapper_reports_missing_node_without_traceback(self):
        stderr = io.StringIO()

        with mock.patch("tools.zcode_control.subprocess.call", side_effect=FileNotFoundError):
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(zcode_control.main(["--help"]), 127)

        self.assertIn("Node.js on PATH", stderr.getvalue())

    def test_zcodectl_wrapper_invokes_bundled_node_controller(self):
        with mock.patch("tools.zcode_control.subprocess.call", return_value=0) as call:
            self.assertEqual(zcode_control.main(["cli-preflight"]), 0)

        command = call.call_args.args[0]
        self.assertEqual(command[0], "node")
        self.assertTrue(command[1].endswith("zcodectl.mjs"))
        self.assertEqual(command[2:], ["cli-preflight"])

    def test_dashboard_wrapper_invokes_bundled_node_server(self):
        with mock.patch("tools.zcode_dashboard.subprocess.call", return_value=0) as call:
            with mock.patch.object(sys, "argv", ["zcode-dashboard", "--workspace", "fixture"]):
                self.assertEqual(zcode_dashboard.main(), 0)

        command = call.call_args.args[0]
        self.assertEqual(command[0], "node")
        self.assertTrue(command[1].endswith("server.mjs"))
        self.assertEqual(command[2:], ["--workspace", "fixture"])


if __name__ == "__main__":
    unittest.main()
