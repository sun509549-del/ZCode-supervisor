import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


class HardFixtureCandidateGateTests(unittest.TestCase):
    def test_accept_after_zero_runs_without_accept_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = self._build_fixture_root(Path(tmp))
            report_dir = self._run_gate(fixture_root, accept_after_ms="0")

            route_args = self._route_args(report_dir)
            self.assertEqual(len(route_args), 2)
            for args in route_args:
                self.assertNotIn("--accept-validated-artifact-after-ms", args)
                task_class_index = args.index("--task-class")
                self.assertEqual(args[task_class_index + 1], "small-fix")

    def test_accept_after_nonzero_passes_accept_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = self._build_fixture_root(Path(tmp))
            report_dir = self._run_gate(fixture_root, accept_after_ms="1234")

            route_args = self._route_args(report_dir)
            self.assertEqual(len(route_args), 2)
            for args in route_args:
                flag_index = args.index("--accept-validated-artifact-after-ms")
                self.assertEqual(args[flag_index + 1], "1234")

    def test_manifest_acceptance_is_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = self._build_fixture_root(Path(tmp))
            self._write_fake_zcode_eval(fixture_root)
            report_dir = self._run_gate(
                fixture_root,
                accept_after_ms="0",
                extra_env={
                    "ZCODE_CANDIDATE_GATE_MANIFEST_ACCEPTANCE": "1",
                    "ZCODE_CANDIDATE_GATE_SECRET_SCAN_RESULT": "pass",
                },
            )

            manifest_dir = report_dir / "manifests"
            self.assertTrue((manifest_dir / "billing-credit-contract.manifest.json").is_file())
            self.assertTrue((manifest_dir / "billing-credit-contract.acceptance.json").is_file())
            self.assertEqual((manifest_dir / "billing-credit-contract.acceptance.rc").read_text(encoding="utf-8").strip(), "0")

    def test_strict_contract_arm_is_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = self._build_fixture_root(Path(tmp))
            report_dir = self._run_gate(
                fixture_root,
                accept_after_ms="0",
                extra_env={"ZCODE_CANDIDATE_GATE_STRICT_CONTRACT": "1"},
            )

            route_args = self._route_args(report_dir)
            billing = route_args[0]
            self.assertIn("--strict-contract-rubric-id", billing)
            rubric_index = billing.index("--strict-contract-rubric-id")
            self.assertEqual(billing[rubric_index + 1], "billing_cent_rounding.v1")
            timeout_index = billing.index("--timeout-ms")
            self.assertEqual(billing[timeout_index + 1], "300000")
            self.assertTrue((report_dir / "strict-contract" / "billing-credit-contract.strict.skipped").is_file())

    def _run_gate(self, fixture_root, *, accept_after_ms, extra_env=None):
        script = Path(__file__).resolve().parents[1] / "scripts" / "run_hard_fixture_candidate_gate.sh"
        env = os.environ.copy()
        env["ACCEPT_VALIDATED_ARTIFACT_AFTER_MS"] = accept_after_ms
        if extra_env:
            env.update(extra_env)
        result = subprocess.run(
            ["bash", str(script), str(fixture_root)],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        report_dir = Path(result.stdout.strip().splitlines()[-1])
        self.assertTrue(report_dir.is_dir())
        return report_dir

    def _route_args(self, report_dir):
        args = []
        for route_stdout in sorted(report_dir.glob("*.route.stdout.json")):
            args.append(json.loads(route_stdout.read_text(encoding="utf-8"))["argv"])
        return args

    def _build_fixture_root(self, tmp_root):
        fixtures = tmp_root / "benchmarks" / "hard-token-fixtures"
        self._write_fixture(fixtures / "billing-credit-contract", "src/credits.js")
        self._write_fixture(fixtures / "policy-reason-contract", "src/policy.js")

        supervisor = tmp_root / "tools" / "zcode_supervisor" / "zcode_supervisor.py"
        supervisor.parent.mkdir(parents=True)
        supervisor.write_text(
            textwrap.dedent(
                """\
                import json
                import sys
                from pathlib import Path

                command = sys.argv[1]
                args = sys.argv[1:]
                if command == "install-repo":
                    print(json.dumps({"ok": True, "argv": args}))
                    raise SystemExit(0)
                if command == "auto-route":
                    workspace = Path(args[args.index("--workspace") + 1])
                    run_dir = workspace / ".codex" / "zcode" / "run-1"
                    run_dir.mkdir(parents=True)
                    (run_dir / "manifest.json").write_text("{}", encoding="utf-8")
                    (run_dir / "run.zcode.json").write_text("{}", encoding="utf-8")
                    print(json.dumps({"ok": True, "argv": args}))
                    raise SystemExit(0)
                raise SystemExit(2)
                """
            ),
            encoding="utf-8",
        )
        return tmp_root

    def _write_fixture(self, fixture_dir, source_path):
        source = fixture_dir / source_path
        source.parent.mkdir(parents=True)
        source.write_text("module.exports = {};\n", encoding="utf-8")

    def _write_fake_zcode_eval(self, fixture_root):
        evaluator = fixture_root / "tools" / "zcode_eval" / "zcode_eval.py"
        evaluator.parent.mkdir(parents=True)
        evaluator.write_text(
            textwrap.dedent(
                """\
                import json
                import sys
                from pathlib import Path

                command = sys.argv[1]
                args = sys.argv[1:]
                if command == "build-zcode-result-manifest":
                    out = Path(args[args.index("--manifest-out") + 1])
                    task_id = args[args.index("--task-id") + 1]
                    payload = {
                        "schema_version": "zcode_result_manifest.v1",
                        "task_id": task_id,
                        "changed_files": [],
                        "allowed_files_only": True,
                        "diffstat": {"insertions": 0, "deletions": 0, "files_changed": 0},
                        "validation": {"result": "pass", "exit_code": 0, "summary": "pass"},
                        "artifact": {"scope_safety": "pass", "artifact_quality": "pass", "codex_repair_size": "none"},
                        "risk_flags": [],
                        "untrusted_data_notice": True,
                    }
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text(json.dumps(payload), encoding="utf-8")
                    print(json.dumps(payload))
                    raise SystemExit(0)
                if command == "accept-zcode-manifest":
                    out = Path(args[args.index("--acceptance-out") + 1])
                    payload = {"schema_version": "codex_acceptance.v1", "accepted": True, "violations": []}
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text(json.dumps(payload), encoding="utf-8")
                    print(json.dumps(payload))
                    raise SystemExit(0)
                raise SystemExit(2)
                """
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
