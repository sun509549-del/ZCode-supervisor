import json
import tempfile
import unittest
from pathlib import Path

from tools.zcode_eval.zcode_release import latest_release, main, parse_releases


FIXTURE_CHANGELOG = """
<html><body>
<h1>Releases & Updates</h1>
<p>3.1.2 Released Jun 18, 2026</p>
<h2>Release v3.1.2</h2>
<h2>New Features</h2>
<li>You can now set a custom certificate for desktop proxy connections.</li>
<li>On Windows, you can choose which shell the app uses.</li>
<h2>Bug Fixes</h2>
<li>Fixed resumed tasks losing or using the wrong thinking level.</li>
<li>Fixed the issue where the sse|http MCP http header transmission was ineffective.</li>
<p>3.1.1 Released Jun 16, 2026</p>
<h2>Release v3.1.1</h2>
<h2>New Features</h2>
<li>HTML files now open directly in the built-in browser.</li>
<h2>Bug Fixes</h2>
<li>Starting plans is more reliable when the app is busy.</li>
<p>3.1.0 Released Jun 16, 2026</p>
<h2>ZCode 3.1.0 Update</h2>
<li>A new usage and quota entry point lets you check your current availability.</li>
</body></html>
"""

FIXTURE_CHANGELOG_WITH_320 = """
<html><body>
<h1>Releases & Updates</h1>
<p>3.2.0 Released Jun 29, 2026</p>
<h2>Release v3.2.0</h2>
<h2>New Features</h2>
<li>Newly added support for plugin management and custom addition of plugins (beta).</li>
<li>Added a generic sub-intelligent agent, supporting custom read and write permissions and models.</li>
<h2>Bug Fixes</h2>
<li>Fixed the compatibility prompt issue when restoring historical sessions.</li>
<p>3.1.2 Released Jun 18, 2026</p>
<h2>Release v3.1.2</h2>
<li>You can now set a custom certificate for desktop proxy connections.</li>
</body></html>
"""

FIXTURE_CHANGELOG_WITH_335 = """
<html><body>
<h1>Releases &amp; Updates</h1>
<p>3.3.5 Released Jul 13, 2026</p>
<h2>Release v3.3.5</h2>
<h2>New Features</h2>
<li>Added support for installing plugins from ZIP URLs.</li>
<h2>Bug Fixes</h2>
<li>Improved request reliability for OpenAI-compatible models.</li>
<li>Added clearer failure reasons for background tasks.</li>
<p>3.3.4 Released Jul 10, 2026</p>
<h2>Release v3.3.4</h2>
<li>New background tasks can be executed by sub-agents and bash.</li>
</body></html>
"""

ROOT = Path(__file__).resolve().parents[1]


class ZCodeReleaseTests(unittest.TestCase):
    def test_parse_latest_release(self):
        release = latest_release(FIXTURE_CHANGELOG, "fixture")

        self.assertEqual(release.version, "3.1.2")
        self.assertIn("custom certificate", release.notes[0])
        self.assertIn("MCP http header", " ".join(release.notes))

    def test_check_reports_update_against_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            changelog = root / "changelog.html"
            baseline = root / "baseline.json"
            output = root / "release.json"
            changelog.write_text(FIXTURE_CHANGELOG, encoding="utf-8")
            baseline.write_text('{"version":"3.1.1"}\n', encoding="utf-8")

            exit_code = main(
                [
                    "check",
                    "--html-file",
                    str(changelog),
                    "--baseline",
                    str(baseline),
                    "--json-out",
                    str(output),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertIn('"update_available": true', output.read_text(encoding="utf-8"))

    def test_check_reports_320_update_against_current_supervisor_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            changelog = root / "changelog.html"
            baseline = root / "baseline.json"
            output = root / "release.json"
            changelog.write_text(FIXTURE_CHANGELOG_WITH_320, encoding="utf-8")
            baseline.write_text('{"version":"3.1.2"}\n', encoding="utf-8")

            exit_code = main(
                [
                    "check",
                    "--html-file",
                    str(changelog),
                    "--baseline",
                    str(baseline),
                    "--json-out",
                    str(output),
                ]
            )

            self.assertEqual(exit_code, 0)
            payload = output.read_text(encoding="utf-8")
            self.assertIn('"version": "3.2.0"', payload)
            self.assertIn('"baseline_version": "3.1.2"', payload)
            self.assertIn('"update_available": true', payload)

    def test_parse_releases_keeps_usage_note(self):
        releases = parse_releases(FIXTURE_CHANGELOG, "fixture")

        self.assertEqual(len(releases), 3)
        self.assertIn("usage and quota", " ".join(releases[2].notes))

    def test_current_baseline_matches_latest_verified_335_fixture(self):
        baseline = json.loads((ROOT / "config/zcode-release-baseline.json").read_text(encoding="utf-8"))
        release = latest_release(FIXTURE_CHANGELOG_WITH_335, baseline["source_url"])

        self.assertEqual(release.version, "3.3.5")
        self.assertEqual(baseline["version"], release.version)
        self.assertEqual(baseline["checked_at"], "2026-07-14")

        with tempfile.TemporaryDirectory() as tmp:
            changelog = Path(tmp) / "changelog.html"
            output = Path(tmp) / "release.json"
            changelog.write_text(FIXTURE_CHANGELOG_WITH_335, encoding="utf-8")
            exit_code = main(
                [
                    "check",
                    "--html-file",
                    str(changelog),
                    "--baseline",
                    str(ROOT / "config/zcode-release-baseline.json"),
                    "--json-out",
                    str(output),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertFalse(json.loads(output.read_text(encoding="utf-8"))["update_available"])

    def test_335_compatibility_dossier_keeps_live_behavior_unverified(self):
        dossier = (ROOT / "docs/zcode-3.3.5-compatibility.md").read_text(encoding="utf-8")

        self.assertIn("https://zcode.z.ai/en/changelog", dossier)
        self.assertIn("3.3.5", dossier)
        self.assertIn("3.3.4", dossier)
        self.assertIn("0.15.2", dossier)
        self.assertIn("UNVERIFIED", dossier)
        self.assertIn("provider error classification", dossier)
        self.assertIn("packet orchestration", dossier)
        self.assertIn("usage extraction", dossier)
        self.assertIn("packaging", dossier)


if __name__ == "__main__":
    unittest.main()
