import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR = ROOT / "tools/zcode_supervisor/zcode_supervisor.py"
ZCODECTL = ROOT / "tools/zcode_control/zcodectl.mjs"
STRICT_COMPARISON = ROOT / "tools/zcode_eval/strict_contract_comparison.py"


def write_fake_cli(root: Path, *, hang: bool = False, write_marker: bool = False) -> Path:
    cli = root / "fake-zcode.cjs"
    marker_block = ""
    if write_marker:
        marker_block = (
            "const markerDir = path.join(cwd, '.codex', 'zcode', 'runs');\n"
            "fs.mkdirSync(markerDir, { recursive: true });\n"
            "fs.writeFileSync(path.join(markerDir, 'worker-completion.json'), JSON.stringify({"
            "schema_version: 'zcode_worker_completion.v1', status: 'implemented', "
            "changed_files: ['src/app.js'], validation_attempted: false, notes: []"
            "}) + '\\n');\n"
        )
    tail = "setTimeout(() => {}, 10000);" if hang else "process.exit(0);"
    cli.write_text(
        "\n".join(
            [
                "const fs = require('node:fs');",
                "const path = require('node:path');",
                "const cwdIndex = process.argv.indexOf('--cwd');",
                "const cwd = cwdIndex >= 0 ? process.argv[cwdIndex + 1] : process.cwd();",
                "fs.writeFileSync(path.join(cwd, 'src/app.js'), 'after\\n');",
                marker_block,
                "process.stdout.write(JSON.stringify({ response: 'done' }) + '\\n');",
                tail,
            ]
        ),
        encoding="utf-8",
    )
    cli.chmod(0o755)
    return cli


def make_packet(root: Path, *, worker_finalization: str = "zcode_owned") -> tuple[Path, Path]:
    workspace = root / "workspace"
    (workspace / "src").mkdir(parents=True)
    (workspace / "src/app.js").write_text("before\n", encoding="utf-8")
    (workspace / "check.py").write_text(
        "from pathlib import Path\n"
        "raise SystemExit(0 if Path('src/app.js').read_text() == 'after\\n' else 1)\n",
        encoding="utf-8",
    )
    packet = root / f"packet-{worker_finalization}.json"
    subprocess.run(
        [
            sys.executable,
            str(SUPERVISOR),
            "packet",
            "--workspace",
            str(workspace),
            "--objective",
            "synthetic worker finalization fixture",
            "--allowed",
            "src/app.js",
            "--validation",
            "python3 check.py",
            "--worker-finalization",
            worker_finalization,
            "--strict-contract-rubric-id",
            "billing_cent_rounding.v1",
            "--strict-contract-task-id",
            "billing-credit-contract",
            "--out",
            str(packet),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return workspace, packet


def run_packet(packet: Path, cli: Path, *, accept_after_ms: int = 0, timeout_ms: int = 5000) -> dict:
    out = packet.parent / f"run-{packet.stem}.json"
    args = [
        "node",
        str(ZCODECTL),
        "run-packet",
        "--packet",
        str(packet),
        "--max-attempts",
        "1",
        "--retry-delay-ms",
        "0",
        "--timeout-ms",
        str(timeout_ms),
        "--validation-timeout",
        "5",
        "--no-bootstrap",
        "--usage-snapshot-source",
        "none",
        "--no-repair-validation",
        "--out",
        str(out),
    ]
    if accept_after_ms:
        args.extend(["--accept-validated-artifact-after-ms", str(accept_after_ms)])
    subprocess.run(
        args,
        cwd=ROOT,
        env={**os.environ, "ZCODE_CLI_PATH": str(cli)},
        text=True,
        capture_output=True,
        check=False,
    )
    return json.loads(out.read_text(encoding="utf-8"))


class WorkerFinalizationPacketTests(unittest.TestCase):
    def test_default_packet_keeps_zcode_owned_self_audit_and_final_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, packet = make_packet(Path(tmp))
            payload = json.loads(packet.read_text(encoding="utf-8"))

            self.assertEqual(payload["worker_finalization"], "zcode_owned")
            self.assertIn("Write the required zcode_self_audit.v1 JSON", payload["prompt"])
            self.assertIn("Required final report shape", payload["prompt"])
            self.assertTrue(payload["strict_contract"]["enabled"])

    def test_supervisor_owned_packet_is_lighter_and_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, packet = make_packet(Path(tmp), worker_finalization="supervisor_owned")
            payload = json.loads(packet.read_text(encoding="utf-8"))

            self.assertEqual(payload["worker_finalization"], "supervisor_owned")
            self.assertIn("Worker finalization: supervisor_owned", payload["prompt"])
            self.assertIn("worker-completion.json", payload["prompt"])
            self.assertNotIn("Write the required zcode_self_audit.v1 JSON", payload["prompt"])
            self.assertNotIn("Required final report shape", payload["prompt"])
            self.assertTrue(payload["strict_contract"]["enabled"])


class WorkerFinalizationRunPacketTests(unittest.TestCase):
    def test_default_strict_packet_still_fails_without_zcode_self_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, packet = make_packet(root)
            cli = write_fake_cli(root)

            result = run_packet(packet, cli)

            self.assertFalse(result["ok"])
            self.assertEqual(result["worker_finalization"], "zcode_owned")
            self.assertEqual(result["supervisor_state"], "audit_failed")
            violations = result["audit"]["violations"]
            self.assertTrue(any(item["type"] == "strict_contract_self_audit_missing" for item in violations))

    def test_supervisor_owned_accepts_validated_artifact_without_worker_self_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, packet = make_packet(root, worker_finalization="supervisor_owned")
            cli = write_fake_cli(root, hang=True, write_marker=True)

            result = run_packet(packet, cli, accept_after_ms=100, timeout_ms=5000)

            self.assertTrue(result["ok"])
            self.assertEqual(result["worker_finalization"], "supervisor_owned")
            self.assertTrue(result["accepted_validated_artifact"])
            self.assertEqual(result["supervisor_state"], "success")
            self.assertTrue(result["audit"]["strict_contract"]["accepted"])
            self.assertTrue(result["audit"]["strict_contract"]["supervisor_owned_finalization"])
            self.assertFalse((root / "workspace/.codex/zcode/runs/zcode_self_audit.json").exists())


class WorkerFinalizationLauncherTests(unittest.TestCase):
    def test_strict_comparison_default_launcher_behavior_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report-default"
            subprocess.run(
                [
                    sys.executable,
                    str(STRICT_COMPARISON),
                    "--report-dir",
                    str(report),
                    "--only",
                    "policy-reason-contract",
                    "--dry-run",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=True,
            )
            script = report / "tasks/policy-reason-contract/zcode-delegated/run_zcode_launcher.sh"
            text = script.read_text(encoding="utf-8")
            self.assertIn("--worker-finalization zcode_owned", text)
            self.assertIn("--accept-validated-artifact-after-ms 60000", text)

    def test_strict_comparison_supervisor_owned_is_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report-supervisor"
            subprocess.run(
                [
                    sys.executable,
                    str(STRICT_COMPARISON),
                    "--report-dir",
                    str(report),
                    "--only",
                    "policy-reason-contract",
                    "--worker-finalization",
                    "supervisor_owned",
                    "--dry-run",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=True,
            )
            script = report / "tasks/policy-reason-contract/zcode-delegated/run_zcode_launcher.sh"
            text = script.read_text(encoding="utf-8")
            self.assertIn("--worker-finalization supervisor_owned", text)
            self.assertIn("--accept-validated-artifact-after-ms 1000", text)


class FixedGlmGuardTests(unittest.TestCase):
    def test_zcode_cli_bootstrap_default_remains_glm_5_2(self):
        source = ZCODECTL.read_text(encoding="utf-8")
        self.assertIn('args.model ?? "glm-5.2"', source)
        self.assertNotIn("glm-4.7", source)

    def test_no_glm_4_7_fallback_in_runtime_paths(self):
        runtime_paths = [
            ROOT / "tools/zcode_control/zcodectl.mjs",
            ROOT / "tools/zcode_supervisor/zcode_supervisor.py",
            ROOT / "tools/zcode_eval/strict_contract_comparison.py",
            ROOT / "scripts/run_direct_delegated_strict_contract.sh",
        ]
        for path in runtime_paths:
            self.assertNotIn("glm-4.7", path.read_text(encoding="utf-8"), str(path))


class NoTimeOfDayGateTests(unittest.TestCase):
    def test_no_jst_or_time_window_blocking_in_runtime_paths(self):
        forbidden = ("Asia/Tokyo", "JST", "15:00-19:00", "time_of_day", "time-of-day")
        runtime_paths = [
            ROOT / "tools/zcode_control/zcodectl.mjs",
            ROOT / "tools/zcode_supervisor/zcode_supervisor.py",
            ROOT / "tools/zcode_eval/strict_contract_comparison.py",
            ROOT / "scripts/run_direct_delegated_strict_contract.sh",
        ]
        for path in runtime_paths:
            source = path.read_text(encoding="utf-8")
            for needle in forbidden:
                self.assertNotIn(needle, source, f"{needle} in {path}")


if __name__ == "__main__":
    unittest.main()
