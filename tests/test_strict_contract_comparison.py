import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.zcode_eval.strict_contract_comparison import (
    PROVIDER_SUCCESS_WITHOUT_USAGE_PAYLOAD_REASON,
    WORKER_USAGE_EMPTY_SIDECAR_REASON,
    auto_reusable_codex_rows,
    build_parser,
    changed_files,
    GIT_CONTROL_ENV_VARS,
    codex_exec_args,
    launcher_script,
    prepare_reused_baseline_row,
    provider_blocker_fields,
    preserve_worker_usage_sidecar,
    run_command,
    selected_tasks,
    task_specs,
    zcode_worker_usage_fields,
)
from tools.zcode_eval.strict_contract_breakdown import (
    DEFAULT_REPAIR_ATTEMPT_BUDGET,
    apply_report_schema,
    compact_policy_contract,
    evaluate_repair_policy,
    repair_policy_fields,
    summarize,
    unavailable_usage_metrics,
    zero_usage_metrics,
)


class StrictContractComparisonHarnessTests(unittest.TestCase):
    def test_task_specs_include_followup_conditions(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks = {task["slug"]: task for task in task_specs(Path(tmp))}

            billing = tasks["billing-credit-contract"]
            self.assertEqual(billing["zcode_timeout_ms"], 600000)
            self.assertTrue(any("Strict self-audit JSON" in item for item in billing["expected_outputs"]))

            policy = tasks["policy-reason-contract"]
            self.assertEqual(policy["contract_mode"], "compact_capsule")

            vision = tasks["vision-card-latest"]
            self.assertEqual(vision["task_class"], "small-fix")
            self.assertEqual(vision["zcode_timeout_ms"], 600000)
            self.assertIn("zcode_self_audit.json", vision["objective"])
            self.assertIn("Do not inspect validate.mjs", vision["objective"])
            self.assertIn("hidden expected JSON", vision["objective"])
            self.assertIn("deviations_from_plan", vision["what_not_to_do"])
            self.assertIn("validation.explanation", vision["what_not_to_do"])
            self.assertIn("risk_flags", vision["what_not_to_do"])
            self.assertIn("statusColor=screenshots/target-card.png@124,162", vision["vision_color_samples"])

    def test_task_specs_expose_goal_e_benchmark_breadth(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks = task_specs(Path(tmp))
            slugs = [task["slug"] for task in tasks]
            categories = {task.get("category") for task in tasks}
            hard_fixtures = {task["slug"] for task in tasks if task.get("hard_fixture")}

            self.assertGreaterEqual(len(slugs), 20)
            self.assertEqual(len(slugs), len(set(slugs)))
            self.assertLess(len(slugs), 30)
            self.assertLessEqual(len(categories), len(slugs))
            self.assertGreaterEqual(len(categories), 12)
            self.assertTrue(
                {
                    "billing-credit-contract",
                    "ledger-summary-contract",
                    "policy-reason-contract",
                    "vision-card-latest",
                }.issubset(slugs)
            )
            self.assertEqual(hard_fixtures, {"billing-credit-contract", "policy-reason-contract"})
            self.assertTrue(all(task.get("dry_run_safe") is True for task in tasks))
            self.assertTrue(all(task.get("future_live_comparison") is True for task in tasks))
            self.assertTrue(all(task.get("artifact_contract") for task in tasks))

    def test_selected_tasks_accepts_all_expanded_slugs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks = task_specs(Path(tmp))
            slugs = ",".join(task["slug"] for task in tasks)

            selected = selected_tasks(tasks, slugs)

            self.assertEqual([task["slug"] for task in selected], [task["slug"] for task in tasks])

    def test_goal_j_core_four_is_explicit_subset_not_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks = task_specs(Path(tmp))
            default = selected_tasks(tasks, None)
            core_four = selected_tasks(
                tasks,
                "billing-credit-contract,ledger-summary-contract,policy-reason-contract,vision-card-latest",
            )

            self.assertGreaterEqual(len(default), 20)
            self.assertEqual(len(core_four), 4)
            self.assertLess(len(core_four), len(default))

    def test_expanded_dry_run_writes_task_plan_and_launchers(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report"
            result = subprocess.run(
                [
                    sys.executable,
                    "tools/zcode_eval/strict_contract_comparison.py",
                    "--report-dir",
                    str(report),
                    "--dry-run",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout)
            plan = json.loads((report / "task-plan.json").read_text(encoding="utf-8"))
            self.assertGreaterEqual(len(plan), 20)
            for task in plan:
                script = report / "tasks" / task["slug"] / "zcode-delegated" / "run_zcode_launcher.sh"
                self.assertTrue(script.exists(), task["slug"])
                text = script.read_text(encoding="utf-8")
                self.assertIn("worker-usage.json", text)
                self.assertIn("export ZCODE_PROVIDER_USAGE_LEDGER=", text)
                self.assertIn("worker-usage.jsonl", text)
                self.assertIn("worker_usage_source_path", text)
                self.assertIn("ZCODE_WORKER_USAGE_SIDECAR", text)
                self.assertEqual(task.get("dry_run_safe"), True)
                self.assertIn("artifact_contract", task)

    def test_launcher_script_wires_vision_samples_and_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp)
            vision = next(task for task in task_specs(report) if task["slug"] == "vision-card-latest")
            workspace = report / "workspace"
            task_dir = report / "task"
            workspace.mkdir()

            script = launcher_script(vision, workspace, task_dir)
            text = script.read_text(encoding="utf-8")

            self.assertIn("--task-class small-fix", text)
            self.assertIn("--timeout-ms 600000", text)
            self.assertIn('--vision-color-sample "accentColor=screenshots/target-card.png@100,80"', text)
            self.assertIn('--vision-color-sample "statusColor=screenshots/target-card.png@124,162"', text)
            self.assertIn("--vision-preflight off", text)
            self.assertIn("Do not compensate for unavailable local validation", text)
            self.assertIn("unset ZCODE_WORKER_USAGE_SIDECAR ZCODE_WORKER_USAGE_LEDGER ZCODE_USAGE_LEDGER", text)
            self.assertIn("export ZCODE_PROVIDER_USAGE_LEDGER=", text)

    def test_launcher_script_wires_compact_policy_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp)
            policy = next(task for task in task_specs(report) if task["slug"] == "policy-reason-contract")
            workspace = report / "workspace"
            task_dir = report / "task"
            workspace.mkdir()

            script = launcher_script(policy, workspace, task_dir)
            text = script.read_text(encoding="utf-8")
            contract = json.loads((task_dir / "compact-contract-capsule.json").read_text(encoding="utf-8"))

            self.assertIn("--task-contract", text)
            self.assertNotIn("--strict-contract-rubric-id", text)
            self.assertEqual(contract["schema_version"], "task_contract.v1")
            self.assertEqual(contract["task_id"], "policy-reason-contract")
            self.assertLess(len(json.dumps(compact_policy_contract(policy))), 3000)

    def test_task_plan_is_json_serializable(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = task_specs(Path(tmp))

            encoded = json.dumps(payload, sort_keys=True)

            self.assertIn("strict_rubric", encoded)
            self.assertIn("vision-card-latest", encoded)

    def test_summary_splits_all_rows_quality_green_phase_and_component(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp)
            (report / "dirty-status.txt").write_text("", encoding="utf-8")
            (report / "dirty-diff.sha256").write_text("empty\n", encoding="utf-8")
            rows = [
                self._row("policy", "codex_only", "pass", 10, "implementation", "codex_implementation"),
                self._row("policy", "zcode_delegated", "pass", 4, "launcher_orchestration", "codex_launcher_orchestration"),
                self._direct_row("policy", "pass"),
                self._row("billing", "codex_only", "pass", 20, "implementation", "codex_implementation"),
                self._row("billing", "zcode_delegated", "fail", 2, "launcher_orchestration", "codex_launcher_orchestration"),
            ]

            payload = summarize(report, rows, Path.cwd())
            all_delegated = payload["token_delta"]["all_rows"]["by_arm"]["zcode_delegated"]
            green_delegated = payload["token_delta"]["quality_green_subset"]["by_arm"]["zcode_delegated"]
            strict_delegated = payload["token_delta"]["strict_green_subset"]["by_arm"]["zcode_delegated"]
            direct = payload["token_delta"]["quality_green_subset"]["by_arm"]["zcode_direct_launcher"]

            self.assertEqual(all_delegated["baseline_effective"], 30)
            self.assertEqual(all_delegated["arm_effective"], 6)
            self.assertEqual(green_delegated["baseline_effective"], 10)
            self.assertEqual(green_delegated["arm_effective"], 4)
            self.assertEqual(strict_delegated["baseline_effective"], 10)
            self.assertEqual(strict_delegated["arm_effective"], 4)
            self.assertEqual(direct["arm_effective"], 0)
            self.assertIn(
                "zcode_delegated::launcher_orchestration::codex_launcher_orchestration",
                payload["token_delta"]["all_rows"]["by_phase_component"],
            )
            self.assertIn(
                "zcode_direct_launcher::launcher_execution::non_llm_direct_launcher",
                payload["token_delta"]["all_rows"]["by_phase_component"],
            )

    def test_default_mode_remains_codex_mediated(self):
        args = build_parser().parse_args([])
        self.assertEqual(args.delegation_execution, "codex-mediated")

    def test_help_documents_direct_operational_entrypoint(self):
        result = subprocess.run(
            [sys.executable, "tools/zcode_eval/strict_contract_comparison.py", "--help"],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("--delegation-execution direct", result.stdout)
        self.assertIn("scripts/run_direct_delegated_strict_contract.sh", result.stdout)
        self.assertIn("direct_orchestrated_delegation_savings", result.stdout)
        self.assertIn("codex_mediated_delegation_savings", result.stdout)
        self.assertIn("Production green-path skip remains disabled", result.stdout)

    def test_direct_wrapper_is_direct_only_and_documents_claim_family(self):
        script = Path(__file__).resolve().parents[1] / "scripts/run_direct_delegated_strict_contract.sh"
        text = script.read_text(encoding="utf-8")

        self.assertIn("--delegation-execution direct", text)
        self.assertIn("direct_orchestrated_delegation_savings", text)
        self.assertIn("codex_mediated_delegation_savings", text)
        self.assertIn("Production green-path skip remains disabled", text)
        self.assertIn("do not pass --delegation-execution", text)

    def test_direct_launcher_child_env_strips_git_control_vars(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "capture_env.py"
            stdout = root / "env.json"
            script.write_text(
                "import json, os\n"
                f"names = {json.dumps(list(GIT_CONTROL_ENV_VARS))}\n"
                "print(json.dumps({name: os.environ.get(name) for name in names}, sort_keys=True))\n",
                encoding="utf-8",
            )
            old_env = {name: os.environ.get(name) for name in GIT_CONTROL_ENV_VARS}
            try:
                for name in GIT_CONTROL_ENV_VARS:
                    os.environ[name] = f"{name}-sentinel"

                result = run_command([sys.executable, str(script)], cwd=root, stdout=stdout)
            finally:
                for name, value in old_env.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value

            self.assertEqual(result.rc, 0)
            captured = json.loads(stdout.read_text(encoding="utf-8"))
            self.assertEqual(captured, {name: None for name in GIT_CONTROL_ENV_VARS})

    def test_codex_exec_defaults_preserve_existing_full_access_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = codex_exec_args(workspace=root / "workspace", task_dir=root / "task", schema=root / "schema.json")

            self.assertIn("danger-full-access", args)
            self.assertNotIn("--add-dir", args)

    def test_codex_exec_can_enforce_workspace_write_for_untrusted_packets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = root / "task"
            args = codex_exec_args(
                workspace=root / "workspace",
                task_dir=task_dir,
                schema=root / "schema.json",
                sandbox="workspace-write",
                writable_dirs=[task_dir],
            )

            self.assertIn("workspace-write", args)
            self.assertEqual(args[args.index("--add-dir") + 1], str(task_dir))

    def test_direct_mode_reports_separate_measurement_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp)
            (report / "dirty-status.txt").write_text("", encoding="utf-8")
            (report / "dirty-diff.sha256").write_text("empty\n", encoding="utf-8")
            baseline = apply_report_schema(
                self._row("policy", "codex_only", "pass", 10, "implementation", "codex_implementation"),
                measurement_mode="direct",
                fresh_run=True,
                baseline_cache_hit=False,
                baseline_cache_key="k",
            )
            direct = apply_report_schema(
                self._direct_row("policy", "pass"),
                measurement_mode="direct",
                fresh_run=True,
                baseline_cache_hit=False,
                baseline_cache_key=None,
            )

            payload = summarize(report, [baseline, direct], Path.cwd())

            self.assertEqual(payload["measurement_mode"], "direct")
            self.assertEqual(payload["rows"][1]["measurement_mode"], "direct")
            self.assertEqual(payload["rows"][1]["claim_family"], "direct_orchestrated_delegation_savings")
            self.assertIn("direct_orchestrated_delegation_savings", payload["claim_families"]["strict-green"])

    def test_mixed_direct_and_codex_mediated_rows_are_separate_claim_families(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp)
            (report / "dirty-status.txt").write_text("", encoding="utf-8")
            (report / "dirty-diff.sha256").write_text("empty\n", encoding="utf-8")
            baseline = apply_report_schema(
                self._row("policy", "codex_only", "pass", 10, "implementation", "codex_implementation"),
                measurement_mode="codex-mediated",
                fresh_run=True,
                baseline_cache_hit=False,
                baseline_cache_key="k",
            )
            mediated = apply_report_schema(
                self._row("policy", "zcode_delegated", "pass", 4, "launcher_orchestration", "codex_launcher_orchestration"),
                measurement_mode="codex-mediated",
                fresh_run=True,
                baseline_cache_hit=False,
                baseline_cache_key=None,
            )
            direct = apply_report_schema(
                self._direct_row("policy", "pass"),
                measurement_mode="direct",
                fresh_run=True,
                baseline_cache_hit=False,
                baseline_cache_key=None,
            )

            payload = summarize(report, [baseline, mediated, direct], Path.cwd())

            self.assertEqual(payload["measurement_mode"], "mixed")
            self.assertIn("codex_mediated_delegation_savings", payload["claim_families"]["strict-green"])
            self.assertIn("direct_orchestrated_delegation_savings", payload["claim_families"]["strict-green"])
            self.assertEqual(payload["rows"][1]["claim_family"], "codex_mediated_delegation_savings")
            self.assertEqual(payload["rows"][2]["claim_family"], "direct_orchestrated_delegation_savings")
            self.assertNotIn("zcode_savings", payload["claim_families"]["strict-green"])

    def test_direct_end_to_end_accounting_separates_delegated_arm_from_total_claims(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp)
            (report / "dirty-status.txt").write_text("", encoding="utf-8")
            (report / "dirty-diff.sha256").write_text("empty\n", encoding="utf-8")
            baseline = apply_report_schema(
                self._row("policy", "codex_only", "pass", 10, "implementation", "codex_implementation"),
                measurement_mode="direct",
                fresh_run=True,
                baseline_cache_hit=False,
                baseline_cache_key="k",
            )
            direct = apply_report_schema(
                self._direct_row("policy", "pass"),
                measurement_mode="direct",
                fresh_run=True,
                baseline_cache_hit=False,
                baseline_cache_key=None,
            )

            payload = summarize(report, [baseline, direct], Path.cwd())
            row = payload["rows"][1]
            strict = payload["end_to_end_accounting"]["strict_green_subset"]["zcode_direct_launcher"]
            delegated = strict["delegated_arm_codex_savings"]
            total_codex = strict["total_codex_side_savings"]
            total_workflow = strict["total_workflow_savings"]

            self.assertEqual(row["delegated_arm_codex_work"], 0)
            self.assertEqual(row["delegated_arm_codex_usage_status"], "measured")
            self.assertIsNone(row["total_codex_side_work"])
            self.assertEqual(row["total_codex_side_usage_status"], "unavailable")
            self.assertIsNone(row["total_workflow_work"])
            self.assertEqual(row["total_workflow_usage_status"], "unavailable")
            self.assertEqual(delegated["claim_family"], "direct_orchestrated_delegation_savings")
            self.assertEqual(delegated["baseline_work"], 10)
            self.assertEqual(delegated["arm_work"], 0)
            self.assertEqual(delegated["reduction_percent"], 100.0)
            self.assertTrue(delegated["deployable_claim_allowed"])
            self.assertEqual(total_codex["claim_family"], "total_codex_side_direct_orchestrated_delegation_savings")
            self.assertEqual(total_codex["usage_status"], "unavailable")
            self.assertIsNone(total_codex["arm_work"])
            self.assertFalse(total_codex["deployable_claim_allowed"])
            self.assertEqual(total_workflow["usage_status"], "unavailable")
            self.assertIsNone(total_workflow["arm_work"])
            self.assertFalse(total_workflow["deployable_claim_allowed"])

    def test_total_workflow_stays_unavailable_when_worker_usage_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp)
            (report / "dirty-status.txt").write_text("", encoding="utf-8")
            (report / "dirty-diff.sha256").write_text("empty\n", encoding="utf-8")
            baseline = apply_report_schema(
                self._row("policy", "codex_only", "pass", 10, "implementation", "codex_implementation"),
                measurement_mode="codex-mediated",
                fresh_run=True,
                baseline_cache_hit=False,
                baseline_cache_key="k",
            )
            mediated = apply_report_schema(
                self._row("policy", "zcode_delegated", "pass", 4, "launcher_orchestration", "codex_launcher_orchestration"),
                measurement_mode="codex-mediated",
                fresh_run=True,
                baseline_cache_hit=False,
                baseline_cache_key=None,
            )

            payload = summarize(report, [baseline, mediated], Path.cwd())
            strict = payload["end_to_end_accounting"]["strict_green_subset"]["zcode_delegated"]

            self.assertEqual(strict["delegated_arm_codex_savings"]["usage_status"], "measured")
            self.assertEqual(strict["delegated_arm_codex_savings"]["arm_work"], 4)
            self.assertEqual(strict["total_codex_side_savings"]["usage_status"], "unavailable")
            self.assertEqual(strict["total_workflow_savings"]["usage_status"], "unavailable")
            self.assertIsNone(strict["total_workflow_savings"]["arm_work"])
            self.assertEqual(mediated["worker_kind"], "zcode")
            self.assertEqual(mediated["worker_usage_status"], "unavailable")
            self.assertEqual(mediated["worker_usage_no_usage_reason"], "zcode_cli_usage_missing")

    def test_measured_worker_tokens_flow_into_total_workflow_accounting(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp)
            (report / "dirty-status.txt").write_text("", encoding="utf-8")
            (report / "dirty-diff.sha256").write_text("empty\n", encoding="utf-8")
            baseline = apply_report_schema(
                self._row("policy", "codex_only", "pass", 10, "implementation", "codex_implementation"),
                measurement_mode="codex-mediated",
                fresh_run=True,
                baseline_cache_hit=False,
                baseline_cache_key="k",
            )
            row = self._row("policy", "zcode_delegated", "pass", 4, "launcher_orchestration", "codex_launcher_orchestration")
            row.update(
                {
                    "codex_acceptance_tokens": 1,
                    "codex_acceptance_usage_status": "measured",
                    "codex_acceptance_no_usage_reason": None,
                    "codex_repair_tokens": 0,
                    "codex_repair_usage_status": "measured",
                    "codex_repair_no_usage_reason": None,
                }
            )
            row.update(
                zcode_worker_usage_fields(
                    {
                        "provider_id": "zai",
                        "model": "glm-5.2",
                        "usage_accounting": {
                            "tokens_source": "zcode_cli_json_usage",
                            "tokens_used": 6,
                            "input_tokens": 5,
                            "output_tokens": 1,
                            "quota_provider": "zai",
                            "quota_percent_used": 0.5,
                        },
                    },
                    report / "tasks/policy/zcode-delegated/zcode-run.json",
                )
            )
            mediated = apply_report_schema(
                row,
                measurement_mode="codex-mediated",
                fresh_run=True,
                baseline_cache_hit=False,
                baseline_cache_key=None,
            )

            payload = summarize(report, [baseline, mediated], Path.cwd())
            strict = payload["end_to_end_accounting"]["strict_green_subset"]["zcode_delegated"]

            self.assertEqual(mediated["worker_kind"], "zcode")
            self.assertEqual(mediated["worker_provider"], "zai")
            self.assertEqual(mediated["worker_model"], "glm-5.2")
            self.assertEqual(mediated["worker_usage_status"], "measured")
            self.assertEqual(mediated["worker_usage_unit"], "tokens")
            self.assertEqual(mediated["worker_total_tokens"], 6)
            self.assertEqual(mediated["worker_quota_percent_used"], 0.5)
            self.assertEqual(mediated["total_workflow_work"], 11)
            self.assertEqual(strict["total_workflow_savings"]["usage_status"], "measured")
            self.assertEqual(strict["total_workflow_savings"]["arm_work"], 11)
            self.assertTrue(strict["total_workflow_savings"]["deployable_claim_allowed"])

    def test_worker_token_usage_can_be_loaded_from_sidecar_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp)
            run_json = report / "tasks/policy/zcode-delegated/zcode-run.json"
            run_json.parent.mkdir(parents=True)
            sidecar = run_json.parent / "worker-usage.json"
            sidecar.write_text(
                json.dumps(
                    {
                        "source_type": "provider_usage_ledger",
                        "provider": "zai",
                        "model": "glm-live",
                        "unit": "tokens",
                        "usage": {
                            "total_tokens": 17,
                            "input_tokens": 12,
                            "output_tokens": 3,
                            "reasoning_tokens": 2,
                        },
                    }
                ),
                encoding="utf-8",
            )

            fields = zcode_worker_usage_fields(
                {
                    "usage_accounting": {
                        "usage_available": False,
                        "no_usage_reason": "zcode_cli_usage_missing",
                        "worker_usage_source_path": "worker-usage.json",
                    }
                },
                run_json,
            )

            self.assertEqual(fields["worker_usage_status"], "measured")
            self.assertEqual(fields["worker_usage_unit"], "tokens")
            self.assertEqual(fields["worker_usage_capture_method"], "provider_usage_ledger")
            self.assertEqual(fields["worker_usage_source_path"], str(sidecar.resolve()))
            self.assertEqual(fields["worker_provider"], "zai")
            self.assertEqual(fields["worker_model"], "glm-live")
            self.assertEqual(fields["worker_total_tokens"], 17)
            self.assertEqual(fields["worker_input_tokens"], 12)
            self.assertEqual(fields["worker_output_tokens"], 3)
            self.assertEqual(fields["worker_reasoning_tokens"], 2)
            self.assertEqual(fields["zcode_worker_tokens"], 17)

    def test_empty_worker_usage_sidecar_is_classified_and_not_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp)
            run_json = report / "tasks/policy/zcode-delegated/zcode-run.json"
            run_json.parent.mkdir(parents=True)
            sidecar = run_json.parent / "worker-usage.json"
            sidecar.write_text(
                json.dumps(
                    {
                        "source_type": "worker_usage_sidecar",
                        "provider": "zai",
                        "model": "glm-live",
                        "status": "unavailable",
                        "unit": "unknown",
                        "usage": {},
                        "no_usage_reason": "zcode_cli_usage_missing",
                    }
                ),
                encoding="utf-8",
            )

            fields = zcode_worker_usage_fields({"usage_accounting": {"worker_usage_source_path": "worker-usage.json"}}, run_json)

            self.assertEqual(fields["worker_usage_status"], "unavailable")
            self.assertEqual(fields["worker_usage_unit"], "unknown")
            self.assertEqual(fields["worker_usage_capture_method"], "worker_usage_sidecar")
            self.assertEqual(fields["worker_usage_no_usage_reason"], WORKER_USAGE_EMPTY_SIDECAR_REASON)
            self.assertEqual(fields["worker_usage_source_path"], str(sidecar.resolve()))
            self.assertIsNone(fields["worker_total_tokens"])
            self.assertIsNone(fields["zcode_worker_tokens"])

    def test_preserve_worker_usage_sidecar_writes_measured_usage_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_json = Path(tmp) / "tasks/policy/zcode-delegated/zcode-run.json"
            run_json.parent.mkdir(parents=True)
            run_json.write_text(
                json.dumps(
                    {
                        "provider_id": "zai",
                        "model": "glm-live",
                        "usage_accounting": {
                            "tokens_source": "zcode_cli_json_usage",
                            "tokens_used": 21,
                            "input_tokens": 13,
                            "output_tokens": 5,
                            "reasoning_tokens": 3,
                        },
                    }
                ),
                encoding="utf-8",
            )

            sidecar = preserve_worker_usage_sidecar(run_json)

            self.assertEqual(sidecar, run_json.parent / "worker-usage.json")
            updated = json.loads(run_json.read_text(encoding="utf-8"))
            self.assertEqual(updated["usage_accounting"]["worker_usage_source_path"], "worker-usage.json")
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
            self.assertEqual(payload["source_type"], "zcode_cli_json_usage")
            self.assertEqual(payload["provider"], "zai")
            self.assertEqual(payload["model"], "glm-live")
            self.assertEqual(payload["unit"], "tokens")
            self.assertEqual(payload["usage"]["total_tokens"], 21)
            self.assertEqual(payload["usage"]["input_tokens"], 13)
            self.assertEqual(payload["usage"]["output_tokens"], 5)
            self.assertEqual(payload["usage"]["reasoning_tokens"], 3)

            fields = zcode_worker_usage_fields(updated, run_json)
            self.assertEqual(fields["worker_usage_status"], "measured")
            self.assertEqual(fields["worker_usage_capture_method"], "zcode_cli_json_usage")
            self.assertEqual(fields["worker_total_tokens"], 21)

    def test_preserve_worker_usage_sidecar_copies_env_ledger_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_json = root / "tasks/policy/zcode-delegated/zcode-run.json"
            run_json.parent.mkdir(parents=True)
            run_json.write_text(
                json.dumps(
                    {
                        "usage_accounting": {
                            "usage_available": False,
                            "no_usage_reason": "zcode_cli_usage_missing",
                        }
                    }
                ),
                encoding="utf-8",
            )
            ledger = run_json.parent / "provider-ledger.jsonl"
            ledger.write_text(
                json.dumps(
                    {
                        "source_type": "provider_usage_ledger",
                        "provider": "zai",
                        "model": "glm-ledger",
                        "unit": "tokens",
                        "usage": {
                            "total_tokens": 34,
                            "input_tokens": 20,
                            "output_tokens": 14,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            sidecar = preserve_worker_usage_sidecar(
                run_json,
                {
                    "ZCODE_PROVIDER_USAGE_LEDGER": str(ledger),
                },
            )

            self.assertEqual(sidecar, run_json.parent / "worker-usage.jsonl")
            updated = json.loads(run_json.read_text(encoding="utf-8"))
            self.assertEqual(updated["usage_accounting"]["worker_usage_source_path"], "worker-usage.jsonl")
            fields = zcode_worker_usage_fields(updated, run_json)
            self.assertEqual(fields["worker_usage_status"], "measured")
            self.assertEqual(fields["worker_usage_capture_method"], "provider_usage_ledger")
            self.assertEqual(fields["worker_total_tokens"], 34)
            self.assertEqual(fields["worker_model"], "glm-ledger")

    def test_preserve_worker_usage_sidecar_keeps_missing_usage_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_json = Path(tmp) / "tasks/policy/zcode-delegated/zcode-run.json"
            run_json.parent.mkdir(parents=True)
            run_json.write_text(
                json.dumps(
                    {
                        "usage_accounting": {
                            "usage_available": False,
                            "no_usage_reason": "zcode_cli_usage_missing",
                            "tokens_source": None,
                            "tokens_used": None,
                        }
                    }
                ),
                encoding="utf-8",
            )

            sidecar = preserve_worker_usage_sidecar(run_json)

            self.assertEqual(sidecar, run_json.parent / "worker-usage.json")
            updated = json.loads(run_json.read_text(encoding="utf-8"))
            self.assertEqual(updated["usage_accounting"]["worker_usage_source_path"], "worker-usage.json")
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "unavailable")
            self.assertEqual(payload["unit"], "unknown")
            self.assertEqual(payload["usage"], {})
            self.assertEqual(payload["no_usage_reason"], "zcode_cli_usage_missing")

            fields = zcode_worker_usage_fields(updated, run_json)
            self.assertEqual(fields["worker_usage_status"], "unavailable")
            self.assertEqual(fields["worker_usage_unit"], "unknown")
            self.assertIsNone(fields["worker_total_tokens"])
            self.assertEqual(fields["worker_usage_no_usage_reason"], WORKER_USAGE_EMPTY_SIDECAR_REASON)

    def test_preserve_worker_usage_sidecar_classifies_provider_success_without_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_json = Path(tmp) / "tasks/policy/zcode-delegated/zcode-run.json"
            run_json.parent.mkdir(parents=True)
            run_json.write_text(
                json.dumps(
                    {
                        "status": "success",
                        "usage_accounting": {
                            "usage_available": False,
                            "no_usage_reason": PROVIDER_SUCCESS_WITHOUT_USAGE_PAYLOAD_REASON,
                            "tokens_source": None,
                            "tokens_used": None,
                        },
                    }
                ),
                encoding="utf-8",
            )

            sidecar = preserve_worker_usage_sidecar(run_json)

            payload = json.loads(sidecar.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "unavailable")
            self.assertEqual(payload["unit"], "unknown")
            self.assertEqual(payload["usage"], {})
            self.assertEqual(payload["no_usage_reason"], PROVIDER_SUCCESS_WITHOUT_USAGE_PAYLOAD_REASON)

            fields = zcode_worker_usage_fields(json.loads(run_json.read_text(encoding="utf-8")), run_json)
            self.assertEqual(fields["worker_usage_status"], "unavailable")
            self.assertEqual(fields["worker_usage_unit"], "unknown")
            self.assertEqual(fields["worker_usage_no_usage_reason"], PROVIDER_SUCCESS_WITHOUT_USAGE_PAYLOAD_REASON)
            self.assertTrue(fields["worker_usage_empty_sidecar"])
            self.assertIsNone(fields["worker_total_tokens"])

    def test_credit_only_sidecar_is_not_worker_token_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp)
            run_json = report / "tasks/policy/zcode-delegated/zcode-run.json"
            run_json.parent.mkdir(parents=True)
            sidecar = run_json.parent / "worker-usage.json"
            sidecar.write_text(
                json.dumps(
                    {
                        "source_type": "credits",
                        "credits_used": 2.5,
                        "no_usage_reason": "worker_token_usage_unavailable_credits_only",
                    }
                ),
                encoding="utf-8",
            )

            fields = zcode_worker_usage_fields(
                {
                    "usage_accounting": {
                        "usage_available": False,
                        "no_usage_reason": "zcode_cli_usage_missing",
                    }
                },
                run_json,
            )

            self.assertEqual(fields["worker_usage_status"], "partial")
            self.assertEqual(fields["worker_usage_unit"], "credits")
            self.assertIsNone(fields["worker_total_tokens"])
            self.assertIsNone(fields["zcode_worker_tokens"])
            self.assertEqual(fields["worker_usage_no_usage_reason"], "worker_token_usage_unavailable_credits_only")

    def test_quota_credit_percent_only_sidecars_are_not_worker_token_usage(self):
        cases = [
            (
                "quota_percent",
                {"source_type": "quota_percent", "quota_percent_used": 0.5},
                "worker_token_usage_unavailable_quota_percent_only",
            ),
            (
                "credits",
                {"source_type": "credits", "credits_used": 2.5},
                "worker_token_usage_unavailable_credits_only",
            ),
            (
                "percent",
                {"source_type": "percent", "percent_used": 42},
                "worker_token_usage_unavailable_percent_only",
            ),
        ]
        for unit, payload, reason in cases:
            with self.subTest(unit=unit), tempfile.TemporaryDirectory() as tmp:
                report = Path(tmp)
                run_json = report / "tasks/policy/zcode-delegated/zcode-run.json"
                run_json.parent.mkdir(parents=True)
                sidecar = run_json.parent / "worker-usage.json"
                sidecar.write_text(json.dumps(payload), encoding="utf-8")

                fields = zcode_worker_usage_fields({"usage_accounting": {"worker_usage_source_path": "worker-usage.json"}}, run_json)

                self.assertEqual(fields["worker_usage_status"], "partial")
                self.assertEqual(fields["worker_usage_unit"], unit)
                self.assertIsNone(fields["worker_total_tokens"])
                self.assertIsNone(fields["zcode_worker_tokens"])
                self.assertEqual(fields["worker_usage_no_usage_reason"], reason)

    def test_quota_percent_worker_usage_is_partial_not_token_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp)
            row = self._row("policy", "zcode_delegated", "pass", 4, "launcher_orchestration", "codex_launcher_orchestration")
            row.update(
                {
                    "codex_acceptance_tokens": 1,
                    "codex_acceptance_usage_status": "measured",
                    "codex_acceptance_no_usage_reason": None,
                    "codex_repair_tokens": 0,
                    "codex_repair_usage_status": "measured",
                    "codex_repair_no_usage_reason": None,
                }
            )
            row.update(
                zcode_worker_usage_fields(
                    {
                        "usage_accounting": {
                            "usage_available": False,
                            "no_usage_reason": "zcode_cli_usage_missing",
                            "quota_provider": "zai",
                            "quota_source": "zai-api",
                            "quota_percent_before": 2.0,
                            "quota_percent_after": 2.75,
                            "quota_percent_used": 0.75,
                            "quota_percent_status": "measured",
                        }
                    },
                    report / "zcode-run.json",
                )
            )

            enriched = apply_report_schema(
                row,
                measurement_mode="codex-mediated",
                fresh_run=True,
                baseline_cache_hit=False,
                baseline_cache_key=None,
            )

            self.assertEqual(enriched["worker_usage_status"], "partial")
            self.assertEqual(enriched["worker_usage_unit"], "quota_percent")
            self.assertEqual(enriched["worker_quota_percent_used"], 0.75)
            self.assertIsNone(enriched["worker_total_tokens"])
            self.assertIsNone(enriched["zcode_worker_tokens"])
            self.assertEqual(enriched["total_workflow_usage_status"], "unavailable")
            self.assertIsNone(enriched["total_workflow_work"])

    def test_zero_worker_total_tokens_is_not_measured_usage(self):
        row = self._row("policy", "zcode_delegated", "pass", 4, "launcher_orchestration", "codex_launcher_orchestration")
        row.update(
            {
                "worker_kind": "zcode",
                "worker_usage_status": "measured",
                "worker_usage_unit": "tokens",
                "worker_total_tokens": 0,
            }
        )

        enriched = apply_report_schema(
            row,
            measurement_mode="codex-mediated",
            fresh_run=True,
            baseline_cache_hit=False,
            baseline_cache_key=None,
        )

        self.assertEqual(enriched["worker_usage_status"], "unavailable")
        self.assertIsNone(enriched["worker_total_tokens"])
        self.assertEqual(enriched["total_workflow_usage_status"], "unavailable")

    def test_usage_unavailable_is_null_not_zero(self):
        row = self._row("policy", "zcode_delegated", "fail", 0, "launcher_orchestration", "codex_launcher_orchestration")
        row["usage"] = unavailable_usage_metrics("zcode_cli_usage_missing")
        row["usage_status"] = "unavailable"

        enriched = apply_report_schema(
            row,
            measurement_mode="codex-mediated",
            fresh_run=True,
            baseline_cache_hit=False,
            baseline_cache_key=None,
        )

        self.assertIsNone(enriched["usage"]["effective_codex_work"])
        self.assertIsNone(enriched["codex_effective_work"])
        self.assertEqual(enriched["no_usage_reason"], "zcode_cli_usage_missing")

    def test_provider_overload_is_infrastructure_blocker_not_quality_failure(self):
        fields = provider_blocker_fields(
            {
                "supervisor_state": "run_timeout",
                "provider_error": True,
                "provider_code": "1305",
                "provider_error_temporary": True,
                "usage_available": False,
            },
            quality=False,
        )

        self.assertEqual(fields["provider_error_kind"], "provider_overload")
        self.assertEqual(fields["blocker_kind"], "infrastructure_blocker")
        self.assertTrue(fields["infrastructure_blocker"])
        self.assertFalse(fields["benchmark_evidence_qualified"])
        self.assertFalse(fields["live_evidence_manifest_allowed"])
        self.assertEqual(fields["quality_failure_kind"], "infrastructure_blocker")

    def test_provider_rate_limit_1302_is_distinct_infrastructure_blocker(self):
        fields = provider_blocker_fields(
            {
                "supervisor_state": "run_timeout",
                "provider_error": True,
                "provider_code": "1302",
                "provider_rate_limit_1302_count": 64,
                "usage_available": False,
            },
            quality=False,
        )

        self.assertEqual(fields["provider_error_kind"], "provider_rate_limit_1302")
        self.assertTrue(fields["provider_rate_limit_1302"])
        self.assertEqual(fields["provider_rate_limit_1302_count"], 64)
        self.assertEqual(fields["blocker_kind"], "infrastructure_blocker")
        self.assertTrue(fields["infrastructure_blocker"])
        self.assertEqual(fields["quality_failure_kind"], "infrastructure_blocker")

    def test_strict_failure_classification_distinguishes_billing_parser_style_failure(self):
        row = self._row("billing-credit-contract", "zcode_delegated", "fail", 2, "launcher_orchestration", "codex_launcher_orchestration")
        row["allowed_file"] = "src/credits.js"
        row["changed_files"] = ["src/credits.js"]
        row["strict_violations"] = ["src/credits.js:4-8,12-15:outside_allowed_files"]

        enriched = apply_report_schema(
            row,
            measurement_mode="codex-mediated",
            fresh_run=True,
            baseline_cache_hit=False,
            baseline_cache_key=None,
        )

        self.assertEqual(enriched["strict_failure_classification"], ["path_range_parser_issue"])

    def test_strict_failure_classification_keeps_quality_failures_visible(self):
        row = self._row("billing-credit-contract", "zcode_delegated", "fail", 2, "launcher_orchestration", "codex_launcher_orchestration")
        row["zcode_acceptance"] = {
            "artifact_quality": "partial",
            "codex_repair_size": "moderate fix",
            "validation_result": "fail",
        }
        row["strict_violations"] = ["risk_flags", "validation_result"]
        row["final_validation_rc"] = 1

        enriched = apply_report_schema(
            row,
            measurement_mode="codex-mediated",
            fresh_run=True,
            baseline_cache_hit=False,
            baseline_cache_key=None,
        )

        self.assertIn("artifact_quality", enriched["strict_failure_classification"])
        self.assertIn("codex_repair_size", enriched["strict_failure_classification"])
        self.assertIn("risk_flags", enriched["strict_failure_classification"])
        self.assertIn("validation_failure", enriched["strict_failure_classification"])

    def test_repair_policy_schema_fields_are_machine_readable(self):
        fields = set(repair_policy_fields())

        self.assertLessEqual(DEFAULT_REPAIR_ATTEMPT_BUDGET, 1)
        self.assertTrue(
            {
                "repair_policy_version",
                "repair_policy_enabled",
                "repair_attempt_budget",
                "repair_attempts_used",
                "repair_decision",
                "repair_blocker",
                "repair_reason",
                "repair_failure_classification",
                "repair_size_class",
                "repair_allowed_files",
                "repair_forbidden_files",
                "repair_changed_files",
                "repair_changed_lines",
                "repair_stop_reason",
                "post_repair_validation_status",
                "post_repair_strict_accepted",
                "repair_evidence_paths",
            }.issubset(fields)
        )

    def test_repair_policy_allows_small_deterministic_contract_fix(self):
        decision = evaluate_repair_policy(
            repair_failure_classification=["validation_failure"],
            repair_size_class="small_contract_fix",
            attempts_used=0,
            changed_files=["src/policy.js"],
            changed_lines=4,
            allowed_files=["src/policy.js"],
            forbidden_files=["test/policy.test.js"],
        )

        self.assertEqual(decision["repair_decision"], "allowed")
        self.assertIsNone(decision["repair_blocker"])
        self.assertEqual(decision["repair_attempt_budget"], 1)
        self.assertEqual(decision["repair_changed_lines"], 4)
        self.assertFalse(decision["post_repair_strict_accepted"])

    def test_repair_policy_blocks_forbidden_or_broad_changes(self):
        decision = evaluate_repair_policy(
            repair_failure_classification=["strict_gate_failure"],
            repair_size_class="broad_blocked",
            attempts_used=0,
            changed_files=["benchmarks/hard-token-fixtures/billing-credit-contract/test/credits.test.js"],
            allowed_files=["src/credits.js"],
            forbidden_files=["test/credits.test.js"],
        )

        self.assertEqual(decision["repair_decision"], "blocked")
        self.assertEqual(decision["repair_stop_reason"], "forbidden_file_changed")
        self.assertIn("forbidden_file_changed", decision["repair_blocker"])
        self.assertEqual(decision["repair_attempts_used"], 0)

    def test_repair_policy_blocks_budget_exhaustion(self):
        decision = evaluate_repair_policy(
            repair_failure_classification=["malformed_json"],
            repair_size_class="small_contract_fix",
            attempts_used=DEFAULT_REPAIR_ATTEMPT_BUDGET,
            changed_files=["src/report.json"],
            allowed_files=["src/report.json"],
            forbidden_files=[],
        )

        self.assertEqual(decision["repair_decision"], "blocked")
        self.assertEqual(decision["repair_stop_reason"], "attempt_budget_exhausted")
        self.assertEqual(decision["repair_blocker"], "attempt_budget_exhausted")

    def test_report_schema_records_repair_without_overriding_strict_acceptance(self):
        row = self._row("policy-reason-contract", "zcode_delegated", "fail", 2, "launcher_orchestration", "codex_launcher_orchestration")
        row["changed_files"] = ["src/policy.js"]
        row["diff"] = [{"file": "src/policy.js", "added": 2, "deleted": 1}]
        row["final_validation_rc"] = 1
        row["strict_accepted"] = False
        row["zcode_acceptance"] = {
            "artifact_quality": "pass",
            "codex_repair_size": "small polish",
            "validation_result": "fail",
        }

        enriched = apply_report_schema(
            row,
            measurement_mode="codex-mediated",
            fresh_run=True,
            baseline_cache_hit=False,
            baseline_cache_key=None,
        )

        self.assertEqual(enriched["repair_decision"], "allowed")
        self.assertEqual(enriched["repair_attempt_budget"], 1)
        self.assertEqual(enriched["repair_attempts_used"], 0)
        self.assertEqual(enriched["repair_size_class"], "small_polish")
        self.assertEqual(enriched["repair_failure_classification"], ["validation_failure"])
        self.assertEqual(enriched["post_repair_validation_status"], "not_run")
        self.assertFalse(enriched["post_repair_strict_accepted"])
        self.assertFalse(enriched["strict_accepted"])

    def test_summary_distinguishes_repaired_success_from_first_pass_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp)
            (report / "dirty-status.txt").write_text("", encoding="utf-8")
            (report / "dirty-diff.sha256").write_text("empty\n", encoding="utf-8")
            baseline = apply_report_schema(
                self._row("policy", "codex_only", "pass", 10, "implementation", "codex_implementation"),
                measurement_mode="codex-mediated",
                fresh_run=True,
                baseline_cache_hit=False,
                baseline_cache_key="k",
            )
            first_pass = apply_report_schema(
                self._row("policy", "zcode_delegated", "pass", 4, "launcher_orchestration", "codex_launcher_orchestration"),
                measurement_mode="codex-mediated",
                fresh_run=True,
                baseline_cache_hit=False,
                baseline_cache_key=None,
            )
            repaired = self._row("billing", "zcode_delegated", "pass", 5, "launcher_orchestration", "codex_launcher_orchestration")
            repaired.update(
                {
                    "repair_attempts_used": 1,
                    "post_repair_validation_status": "pass",
                    "post_repair_strict_accepted": True,
                }
            )
            repaired = apply_report_schema(
                repaired,
                measurement_mode="codex-mediated",
                fresh_run=True,
                baseline_cache_hit=False,
                baseline_cache_key=None,
            )

            payload = summarize(report, [baseline, first_pass, repaired], Path.cwd())

            self.assertEqual(first_pass["repair_decision"], "skipped")
            self.assertEqual(repaired["repair_decision"], "attempted")
            self.assertEqual(repaired["repair_attempts_used"], 1)
            self.assertTrue(repaired["post_repair_strict_accepted"])
            self.assertEqual(payload["repair_policy"]["repaired_success_count"], 1)
            self.assertEqual(payload["repair_policy"]["repair_decisions"]["skipped"], 2)
            self.assertEqual(payload["repair_policy"]["repair_decisions"]["attempted"], 1)

    def test_baseline_cache_auto_hit_and_miss(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            previous = root / "previous"
            current = root / "current"
            previous.mkdir()
            current.mkdir()
            (previous / "summary.json").write_text(
                json.dumps(
                    {
                        "rows": [
                            {
                                "task": "policy",
                                "mode": "codex_only",
                                "baseline_cache_key": "hit-key",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            hit = auto_reusable_codex_rows(current, {"policy": "hit-key"})
            miss = auto_reusable_codex_rows(current, {"policy": "changed-key"})

            self.assertIn("policy", hit)
            self.assertEqual(miss, {})

    def test_reused_baseline_row_is_marked_non_fresh_cache_hit(self):
        reused = prepare_reused_baseline_row(
            self._row("policy", "codex_only", "pass", 10, "implementation", "codex_implementation"),
            measurement_mode="direct",
            cache_key="verified-key",
            verified_cache_hit=True,
            reuse_mode="auto",
        )

        self.assertFalse(reused["fresh_run"])
        self.assertTrue(reused["baseline_cache_hit"])
        self.assertEqual(reused["baseline_cache_key"], "verified-key")
        self.assertEqual(reused["baseline_reuse_mode"], "auto")

    def test_changed_files_ignores_harness_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "base"
            workspace = root / "workspace"
            (base / "src").mkdir(parents=True)
            (workspace / "src").mkdir(parents=True)
            (workspace / ".codex/zcode/runs").mkdir(parents=True)
            (base / "src/app.js").write_text("before\n", encoding="utf-8")
            (workspace / "src/app.js").write_text("after\n", encoding="utf-8")
            (workspace / ".codex/ZCODE_DELEGATION.md").write_text("metadata\n", encoding="utf-8")
            (workspace / ".codex/zcode/runs/zcode_self_audit.json").write_text("{}\n", encoding="utf-8")

            self.assertEqual(changed_files(base, workspace), ["src/app.js"])

    def _row(self, task, mode, quality, effective, phase, component):
        usage = {
            "input_tokens": effective,
            "cached_input_tokens": 0,
            "uncached_input_tokens": effective,
            "output_tokens": 0,
            "reasoning_output_tokens": 0,
            "total_in_out": effective,
            "total_plus_reasoning": effective,
            "uncached_plus_reasoning": effective,
            "effective_codex_work": effective,
            "usage_missing": False,
        }
        return {
            "task": task,
            "kind": "fixture",
            "mode": mode,
            "quality": quality,
            "duration_seconds": 1,
            "diff": [],
            "allowed_file": "src/policy.js",
            "usage": usage,
            "strict_accepted": quality == "pass" if mode != "codex_only" else None,
            "codex_exec_rows": [
                {
                    "task": task,
                    "arm": mode,
                    "phase": phase,
                    "component": component,
                    "usage": {
                        "uncached_input": effective,
                        "output": 0,
                        "reasoning_output": 0,
                        "effective_codex_work": effective,
                    },
                }
            ],
        }

    def _direct_row(self, task, quality):
        return {
            "task": task,
            "kind": "fixture",
            "mode": "zcode_direct_launcher",
            "quality": quality,
            "duration_seconds": 1,
            "diff": [],
            "usage": zero_usage_metrics(),
            "strict_accepted": quality == "pass",
            "allowed_file": "src/policy.js",
            "codex_exec_rows": [],
        }


class TestWorkerUsageSidecarPreflight(unittest.TestCase):
    def test_task_scoped_sidecar_path_is_measured(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_json = Path(tmp) / "tasks/policy/zcode-delegated/zcode-run.json"
            run_json.parent.mkdir(parents=True)
            sidecar = run_json.parent / "worker-usage.json"
            sidecar.write_text(
                json.dumps(
                    {
                        "source_type": "provider_usage_ledger",
                        "provider": "zai",
                        "model": "glm-live",
                        "unit": "tokens",
                        "usage": {"total_tokens": 17},
                    }
                ),
                encoding="utf-8",
            )

            fields = zcode_worker_usage_fields(
                {"usage_accounting": {"worker_usage_source_path": "worker-usage.json"}},
                run_json,
            )

            self.assertEqual(fields["worker_usage_status"], "measured")
            self.assertEqual(fields["worker_usage_unit"], "tokens")
            self.assertEqual(fields["worker_total_tokens"], 17)
            self.assertEqual(fields["worker_usage_source_path"], str(sidecar.resolve()))

    def test_global_ledger_path_is_not_row_scoped_token_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_json = root / "tasks/policy/zcode-delegated/zcode-run.json"
            run_json.parent.mkdir(parents=True)
            global_ledger = root / "provider-ledger.json"
            global_ledger.write_text(
                json.dumps(
                    {
                        "source_type": "provider_usage_ledger",
                        "provider": "zai",
                        "model": "glm-live",
                        "unit": "tokens",
                        "usage": {"total_tokens": 1000},
                    }
                ),
                encoding="utf-8",
            )

            fields = zcode_worker_usage_fields(
                {
                    "usage_accounting": {
                        "worker_usage_source_path": str(global_ledger),
                        "no_usage_reason": "zcode_cli_usage_missing",
                    }
                },
                run_json,
            )

            self.assertEqual(fields["worker_usage_status"], "unavailable")
            self.assertIsNone(fields["worker_total_tokens"])
            self.assertEqual(fields["worker_usage_no_usage_reason"], "zcode_cli_usage_missing")

    def test_preserve_rejects_out_of_scope_env_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_json = root / "tasks/policy/zcode-delegated/zcode-run.json"
            run_json.parent.mkdir(parents=True)
            run_json.write_text(
                json.dumps(
                    {
                        "usage_accounting": {
                            "usage_available": False,
                            "no_usage_reason": "zcode_cli_usage_missing",
                        }
                    }
                ),
                encoding="utf-8",
            )
            global_ledger = root / "shared-provider-ledger.jsonl"
            global_ledger.write_text(
                json.dumps(
                    {
                        "source_type": "provider_usage_ledger",
                        "provider": "zai",
                        "model": "glm-live",
                        "usage": {"total_tokens": 1000},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            sidecar = preserve_worker_usage_sidecar(
                run_json,
                {"ZCODE_PROVIDER_USAGE_LEDGER": str(global_ledger)},
            )

            self.assertEqual(sidecar, run_json.parent / "worker-usage.json")
            self.assertFalse((run_json.parent / "worker-usage.jsonl").exists())
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "unavailable")
            self.assertEqual(payload["no_usage_reason"], "worker_usage_source_out_of_scope")
            updated = json.loads(run_json.read_text(encoding="utf-8"))
            self.assertEqual(updated["usage_accounting"]["worker_usage_source_path"], "worker-usage.json")
            fields = zcode_worker_usage_fields(updated, run_json)
            self.assertEqual(fields["worker_usage_status"], "unavailable")
            self.assertIsNone(fields["worker_total_tokens"])

    def test_missing_usage_stays_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_json = Path(tmp) / "tasks/policy/zcode-delegated/zcode-run.json"
            run_json.parent.mkdir(parents=True)
            run_json.write_text(
                json.dumps({"usage_accounting": {"no_usage_reason": "zcode_cli_usage_missing"}}),
                encoding="utf-8",
            )

            sidecar = preserve_worker_usage_sidecar(run_json)
            fields = zcode_worker_usage_fields(json.loads(run_json.read_text(encoding="utf-8")), run_json)

            self.assertEqual(sidecar, run_json.parent / "worker-usage.json")
            self.assertEqual(fields["worker_usage_status"], "unavailable")
            self.assertEqual(fields["worker_usage_unit"], "unknown")
            self.assertIsNone(fields["worker_total_tokens"])

    def test_quota_and_credit_evidence_are_not_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_json = Path(tmp) / "tasks/policy/zcode-delegated/zcode-run.json"
            run_json.parent.mkdir(parents=True)
            fields = zcode_worker_usage_fields(
                {"usage_accounting": {"quota_percent_used": 0.5}},
                run_json,
            )
            self.assertEqual(fields["worker_usage_status"], "partial")
            self.assertEqual(fields["worker_usage_unit"], "quota_percent")
            self.assertIsNone(fields["worker_total_tokens"])

            credit_sidecar = run_json.parent / "worker-usage.json"
            credit_sidecar.write_text(
                json.dumps({"source_type": "credits", "credits_used": 2.5}),
                encoding="utf-8",
            )
            fields = zcode_worker_usage_fields(
                {"usage_accounting": {"worker_usage_source_path": "worker-usage.json"}},
                run_json,
            )
            self.assertEqual(fields["worker_usage_status"], "partial")
            self.assertEqual(fields["worker_usage_unit"], "credits")
            self.assertIsNone(fields["worker_total_tokens"])


if __name__ == "__main__":
    unittest.main()
