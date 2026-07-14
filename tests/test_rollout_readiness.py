import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from scripts.build_live_evidence_manifest import build_manifest
from tools.zcode_eval.live_evidence import (
    LIVE_EVIDENCE_MANIFEST_SCHEMA_VERSION,
    validate_live_evidence_manifest,
)
from tools.zcode_eval.rollout_readiness import (
    ROLLOUT_READINESS_SCHEMA_VERSION,
    assess_rollout_readiness,
)


class RolloutReadinessTests(unittest.TestCase):
    def test_default_assessment_is_fixture_ready_and_blocks_production(self):
        payload = assess_rollout_readiness(benchmark_count=20)

        self.assertEqual(payload["rollout_readiness_schema_version"], ROLLOUT_READINESS_SCHEMA_VERSION)
        self.assertEqual(payload["rollout_status"], "fixture_ready")
        self.assertEqual(payload["rollout_mode"], "dry_run_only")
        self.assertFalse(payload["production_green_path_enabled"])
        self.assertFalse(payload["direct_mode_default"])
        self.assertEqual(payload["benchmark_task_count"], 20)
        self.assertEqual(payload["live_benchmark_status"], "unavailable")
        self.assertEqual(payload["worker_usage_status"], "unavailable")
        self.assertEqual(payload["total_workflow_savings_status"], "blocked_unavailable_usage")
        self.assertIn("live_20_provider_benchmark", self._blocked_ids(payload))
        self.assertIn("worker_token_usage", self._blocked_ids(payload))
        self.assertIn("total_workflow_savings", self._blocked_ids(payload))
        self.assertIn("manual_approval", self._blocked_ids(payload))

    def test_production_green_path_or_direct_default_blocks_readiness(self):
        green = assess_rollout_readiness(benchmark_count=20, production_green_path=True)
        self.assertEqual(green["rollout_status"], "blocked")
        self.assertIn("production_green_path_disabled", self._blocked_ids(green))

        direct = assess_rollout_readiness(benchmark_count=20, direct_default=True)
        self.assertEqual(direct["rollout_status"], "blocked")
        self.assertIn("direct_mode_default_disabled", self._blocked_ids(direct))

    def test_candidate_and_ready_require_live_usage_savings_and_approval(self):
        candidate = assess_rollout_readiness(
            benchmark_count=20,
            live_benchmark_status="passed",
            worker_usage_status="measured_tokens",
            total_workflow_savings_status="measured",
            manual_approval_status="pending",
        )
        self.assertEqual(candidate["rollout_status"], "candidate")
        self.assertEqual(candidate["rollout_mode"], "opt_in_candidate")
        self.assertIn("manual_approval", self._blocked_ids(candidate))

        ready = assess_rollout_readiness(
            benchmark_count=20,
            live_benchmark_status="passed",
            worker_usage_status="measured_tokens",
            total_workflow_savings_status="measured",
            manual_approval_status="approved",
        )
        self.assertEqual(ready["rollout_status"], "ready")
        self.assertEqual(ready["rollout_mode"], "production_ready")
        self.assertEqual(ready["blocked_checks"], [])

    def test_cli_writes_stable_json_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "readiness.json"
            result = subprocess.run(
                [sys.executable, "scripts/check_rollout_readiness.py", "--output", str(output), "--json"],
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            stdout_payload = json.loads(result.stdout)
            self.assertEqual(payload, stdout_payload)
            self.assertEqual(payload["rollout_status"], "fixture_ready")
            self.assertFalse(payload["production_green_path_enabled"])

    def test_fixture_ready_manifest_is_valid_but_not_live_evidence(self):
        payload = self._manifest()
        validation = validate_live_evidence_manifest(payload)

        self.assertTrue(validation["valid"], validation["errors"])
        self.assertEqual(validation["live_benchmark_status"], "unavailable")
        self.assertEqual(validation["worker_usage_status"], "unavailable")
        self.assertFalse(validation["claimable_total_workflow_savings"])

    def test_measured_worker_usage_must_be_tokens_with_source_path(self):
        payload = self._live_manifest()
        payload["worker_usage"]["unit"] = "quota_percent"
        payload["worker_usage"]["source_type"] = "quota_percent"

        validation = validate_live_evidence_manifest(payload)

        self.assertFalse(validation["valid"])
        self.assertIn("worker_usage:quota_credit_percent_not_token_evidence", validation["errors"])
        self.assertIn("worker_usage:measured_usage_requires_token_unit", validation["errors"])

    def test_live_manifest_requires_measured_worker_tokens(self):
        payload = self._live_manifest()
        payload["worker_usage"] = {
            "status": "unavailable",
            "unit": None,
            "source_path": None,
            "source_type": "worker_usage_sidecar",
            "no_usage_reason": "worker_usage_empty_sidecar",
        }
        payload["total_workflow_savings"] = {
            "status": "blocked",
            "unit": None,
            "claim_scope": "direct_orchestrated_delegation_savings",
            "no_claim_reason": "worker_usage_empty_sidecar",
        }

        validation = validate_live_evidence_manifest(payload)

        self.assertFalse(validation["valid"])
        self.assertIn("worker_usage:live_run_requires_measured_tokens", validation["errors"])
        self.assertEqual(validation["worker_usage_status"], "worker_usage_empty_sidecar")
        self.assertEqual(validation["live_benchmark_status"], "invalid_manifest")

    def test_total_workflow_savings_require_both_sides_measured_tokens(self):
        payload = self._live_manifest()
        payload["codex_usage"] = {
            "status": "unavailable",
            "unit": None,
            "source_path": None,
            "no_usage_reason": "codex_usage_missing",
        }

        validation = validate_live_evidence_manifest(payload)

        self.assertFalse(validation["valid"])
        self.assertIn("total_workflow_savings:requires_codex_measured_tokens", validation["errors"])
        self.assertEqual(validation["total_workflow_savings_status"], "blocked_unavailable_usage")

    def test_mixed_claim_families_fail_closed(self):
        payload = self._live_manifest()
        payload["claim_families"] = [
            "direct_orchestrated_delegation_savings",
            "codex_mediated_delegation_savings",
        ]

        validation = validate_live_evidence_manifest(payload)

        self.assertFalse(validation["valid"])
        self.assertIn("claim_family:mixed_direct_and_codex_mediated", validation["errors"])

    def test_readiness_with_valid_fixture_manifest_stays_fixture_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text(json.dumps(self._manifest()), encoding="utf-8")

            payload = assess_rollout_readiness(benchmark_count=20, live_evidence_manifest=path)

            self.assertEqual(payload["rollout_status"], "fixture_ready")
            self.assertEqual(payload["rollout_mode"], "dry_run_only")
            self.assertEqual(payload["live_evidence_manifest_status"], "valid")
            self.assertIn("live_20_provider_benchmark", self._blocked_ids(payload))

    def test_live_manifest_cli_rejects_invalid_claims(self):
        with tempfile.TemporaryDirectory() as tmp:
            valid_path = Path(tmp) / "valid.json"
            invalid_path = Path(tmp) / "invalid.json"
            valid_path.write_text(json.dumps(self._manifest()), encoding="utf-8")
            invalid = self._live_manifest()
            invalid["worker_usage"]["source_type"] = "credits"
            invalid["worker_usage"]["credits"] = 12
            invalid_path.write_text(json.dumps(invalid), encoding="utf-8")

            valid = subprocess.run(
                ["python3", "scripts/check_live_evidence_manifest.py", str(valid_path), "--json"],
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
            invalid_result = subprocess.run(
                ["python3", "scripts/check_live_evidence_manifest.py", str(invalid_path), "--json"],
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )

            self.assertEqual(valid.returncode, 0, msg=valid.stderr)
            self.assertNotEqual(invalid_result.returncode, 0)
            self.assertIn("worker_usage:quota_credit_percent_not_token_evidence", invalid_result.stdout)

    def test_build_live_manifest_from_summary_preserves_measured_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary_path = Path(tmp) / "summary.json"
            summary_path.write_text(json.dumps(self._summary(worker_measured=True)), encoding="utf-8")
            (summary_path.parent / "summary.md").write_text("# summary\n", encoding="utf-8")
            (summary_path.parent / "task-plan.json").write_text("[]\n", encoding="utf-8")

            manifest = build_manifest(
                json.loads(summary_path.read_text(encoding="utf-8")),
                summary_path,
                operator="aki-ai-desk",
                approval_reference="goal-objective.md",
            )
            validation = validate_live_evidence_manifest(manifest)

            self.assertTrue(validation["valid"], validation["errors"])
            self.assertEqual(manifest["task_count"], 20)
            self.assertEqual(manifest["claim_family"], "codex_mediated_delegation_savings")
            self.assertEqual(manifest["worker_usage"]["status"], "measured")
            self.assertEqual(manifest["worker_usage"]["unit"], "tokens")
            self.assertEqual(manifest["worker_usage"]["total_tokens"], 1000)
            self.assertEqual(manifest["total_workflow_savings"]["status"], "measured")

    def test_build_live_manifest_preserves_db_delta_worker_usage_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = self._summary(worker_measured=True)
            for row in summary["rows"]:
                if row["mode"] == "zcode_delegated":
                    row["worker_usage_capture_method"] = "zcode_cli_model_usage_db_delta"
            summary_path = Path(tmp) / "summary.json"
            summary_path.write_text(json.dumps(summary), encoding="utf-8")
            (summary_path.parent / "summary.md").write_text("# summary\n", encoding="utf-8")
            (summary_path.parent / "task-plan.json").write_text("[]\n", encoding="utf-8")

            manifest = build_manifest(
                json.loads(summary_path.read_text(encoding="utf-8")),
                summary_path,
                operator="aki-ai-desk",
                approval_reference="goal-objective.md",
            )

            self.assertEqual(manifest["worker_usage"]["source_type"], "zcode_cli_model_usage_db_delta")

    def test_build_live_manifest_rejects_unavailable_worker_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary_path = Path(tmp) / "summary.json"
            summary_path.write_text(json.dumps(self._summary(worker_measured=False)), encoding="utf-8")

            with self.assertRaises(SystemExit) as raised:
                build_manifest(
                    json.loads(summary_path.read_text(encoding="utf-8")),
                    summary_path,
                    operator="aki-ai-desk",
                    approval_reference="goal-objective.md",
                )

            self.assertIn("worker_token_usage", str(raised.exception))
            self.assertIn("worker_token_usage_unavailable", str(raised.exception))

    def test_build_live_manifest_rejects_provider_overload_partial_run(self):
        summary = {
            "rows": [
                {
                    "task": "policy-reason-contract",
                    "mode": "zcode_direct_launcher",
                    "quality": "fail",
                    "strict_accepted": None,
                    "provider_error": True,
                    "provider_code": "1305",
                    "provider_error_kind": "provider_overload",
                    "blocker_kind": "infrastructure_blocker",
                    "benchmark_evidence_qualified": False,
                    "claim_family": "direct_orchestrated_delegation_savings",
                    "worker_usage_status": "unavailable",
                    "worker_usage_unit": "unknown",
                    "worker_usage_no_usage_reason": "provider_error_without_zcode_cli_usage",
                }
            ]
        }

        with self.assertRaises(SystemExit) as raised:
            build_manifest(
                summary,
                Path("artifacts/reports/goal-n-approved-live-20-direct-run/summary.json"),
                operator="aki-ai-desk",
                approval_reference="goal-objective.md",
            )

        message = str(raised.exception)
        self.assertIn("provider_overload", message)
        self.assertIn("task_count:must_be_at_least_20:1", message)

    def test_readiness_reports_provider_overload_infrastructure_blocker(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary_path = Path(tmp) / "summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "rows": [
                            {
                                "task": "policy-reason-contract",
                                "mode": "zcode_direct_launcher",
                                "quality": "fail",
                                "provider_error_kind": "provider_overload",
                                "blocker_kind": "infrastructure_blocker",
                                "worker_usage_status": "unavailable",
                                "worker_usage_unit": "unknown",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            payload = assess_rollout_readiness(benchmark_count=20, live_benchmark_summary=summary_path)

            self.assertEqual(payload["rollout_status"], "fixture_ready")
            self.assertEqual(payload["rollout_mode"], "dry_run_only")
            self.assertEqual(payload["live_benchmark_status"], "provider_overload_infrastructure_blocker")
            self.assertEqual(payload["provider_blocker_status"], "provider_overload_infrastructure_blocker")
            self.assertEqual(payload["worker_usage_status"], "unavailable")
            self.assertEqual(payload["total_workflow_savings_status"], "blocked_unavailable_usage")

    def test_readiness_reports_empty_worker_usage_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary_path = Path(tmp) / "summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "rows": [
                            {
                                "task": "policy-reason-contract",
                                "mode": "zcode_direct_launcher",
                                "quality": "pass",
                                "worker_usage_status": "unavailable",
                                "worker_usage_unit": "unknown",
                                "worker_total_tokens": None,
                                "worker_usage_no_usage_reason": "worker_usage_empty_sidecar",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            payload = assess_rollout_readiness(benchmark_count=20, live_benchmark_summary=summary_path)

            self.assertEqual(payload["rollout_status"], "fixture_ready")
            self.assertEqual(payload["worker_usage_status"], "worker_usage_empty_sidecar")
            self.assertIn("worker_token_usage", self._blocked_ids(payload))

    def _blocked_ids(self, payload):
        return {check["check_id"] for check in payload["blocked_checks"]}

    def _manifest(self):
        path = Path("docs/zcode-strict-contract-v3/examples/live-evidence-manifest.fixture-ready.example.json")
        return json.loads(path.read_text(encoding="utf-8"))

    def _live_manifest(self):
        payload = deepcopy(self._manifest())
        payload.update(
            {
                "benchmark_run_id": "live-run-001",
                "benchmark_run_kind": "live_provider_benchmark",
                "provider_models": [{"provider": "zai", "model": "glm-live", "status": "pass"}],
                "operator": "aki-ai-desk",
                "strict_result_status": "pass",
                "quality_result_status": "pass",
                "worker_usage": {
                    "status": "measured",
                    "unit": "tokens",
                    "source_path": "artifacts/evals/live-run-001-worker-usage.jsonl",
                    "source_type": "provider_usage_ledger",
                    "input_tokens": 1200,
                    "output_tokens": 300,
                    "total_tokens": 1500,
                },
                "codex_usage": {
                    "status": "measured",
                    "unit": "tokens",
                    "source_path": "artifacts/evals/live-run-001-codex-usage.jsonl",
                    "source_type": "codex_usage_ledger",
                    "input_tokens": 900,
                    "output_tokens": 100,
                    "total_tokens": 1000,
                },
                "total_workflow_savings": {
                    "status": "measured",
                    "unit": "tokens",
                    "claim_scope": "direct_orchestrated_delegation_savings",
                },
                "artifact_paths": ["artifacts/reports/live-run-001/summary.json"],
                "provider_auth_status": "pass",
                "partial_run": False,
                "production_green_path_enabled": False,
                "direct_mode_default": False,
            }
        )
        self.assertEqual(payload["schema_version"], LIVE_EVIDENCE_MANIFEST_SCHEMA_VERSION)
        return payload

    def _summary(self, *, worker_measured):
        rows = []
        for index in range(20):
            task = f"task-{index:02d}"
            rows.append(
                {
                    "task": task,
                    "mode": "codex_only",
                    "quality": "pass",
                    "codex_usage_status": "measured",
                    "codex_effective_work": 100,
                    "usage": {
                        "uncached_input_tokens": 80,
                        "output_tokens": 10,
                        "reasoning_output_tokens": 10,
                    },
                }
            )
            delegated = {
                "task": task,
                "mode": "zcode_delegated",
                "quality": "pass",
                "strict_accepted": True,
                "timed_out": False,
                "claim_family": "codex_mediated_delegation_savings",
                "worker_provider": "zai",
                "worker_model": "glm-live",
                "codex_usage_status": "measured",
                "codex_effective_work": 30,
                "usage": {
                    "uncached_input_tokens": 20,
                    "output_tokens": 5,
                    "reasoning_output_tokens": 5,
                },
            }
            if worker_measured:
                delegated.update(
                    {
                        "worker_usage_status": "measured",
                        "worker_usage_unit": "tokens",
                        "worker_total_tokens": 50,
                        "worker_input_tokens": 40,
                        "worker_output_tokens": 10,
                        "worker_usage_source_path": f"artifacts/reports/live/tasks/{task}/zcode-run.json",
                    }
                )
            else:
                delegated.update(
                    {
                        "worker_usage_status": "unavailable",
                        "worker_usage_unit": "unknown",
                        "worker_total_tokens": None,
                        "worker_usage_source_path": None,
                        "worker_usage_no_usage_reason": "worker_token_usage_unavailable",
                    }
                )
            rows.append(delegated)
        status = "measured" if worker_measured else "unavailable"
        return {
            "rows": rows,
            "end_to_end_accounting": {
                "strict_green_subset": {
                    "zcode_delegated": {
                        "total_workflow_savings": {
                            "usage_status": status,
                            "no_usage_reason": None if worker_measured else "worker_token_usage_unavailable",
                        }
                    }
                }
            },
        }


if __name__ == "__main__":
    unittest.main()
