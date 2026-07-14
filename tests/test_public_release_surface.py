"""Guard the public repository surface against private or mutable release inputs."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PATHS = (
    ".public-sync.toml",
    "CONTINUITY.md",
    "FRESH_CLONE_LOG.md",
    "GOAL.md",
    "HANDOFF.md",
    "docs/goals",
    "docs/v0.0.2-release-readiness.md",
)
PRIVATE_MARKERS = ("/Users/", "ZCode-supervisor-internal")
PINNED_ACTION = re.compile(r"^[^\s@]+@[0-9a-f]{40}(?:\s+#.*)?$")


class PublicReleaseSurfaceTests(unittest.TestCase):
    def test_private_paths_are_absent(self) -> None:
        for relative_path in FORBIDDEN_PATHS:
            self.assertFalse((ROOT / relative_path).exists(), relative_path)

    def test_public_markdown_has_no_private_markers(self) -> None:
        markdown_files = [
            ROOT / "CHANGELOG.md",
            ROOT / "README.md",
            ROOT / "README.ja.md",
            ROOT / "SECURITY.md",
            *sorted((ROOT / "docs").rglob("*.md")),
        ]
        for path in markdown_files:
            text = path.read_text(encoding="utf-8")
            for marker in PRIVATE_MARKERS:
                self.assertNotIn(marker, text, f"{path.relative_to(ROOT)}: {marker}")

    def test_workflow_actions_are_pinned_to_full_commit_shas(self) -> None:
        for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if not stripped.startswith("uses:"):
                    continue
                action = stripped.removeprefix("uses:").strip()
                self.assertRegex(
                    action,
                    PINNED_ACTION,
                    f"{path.relative_to(ROOT)}:{line_number}: {action}",
                )

    def test_readmes_use_release_and_package_indexes_as_version_authority(self) -> None:
        for filename in ("README.md", "README.ja.md"):
            text = (ROOT / filename).read_text(encoding="utf-8")
            self.assertIn("https://github.com/AkiGarage/ZCode-supervisor/releases", text)
            self.assertIn("https://pypi.org/project/zcode-supervisor/", text)
            self.assertNotIn("latest published package is `v0.0.1`", text)


if __name__ == "__main__":
    unittest.main()
