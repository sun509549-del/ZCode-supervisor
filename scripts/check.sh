#!/usr/bin/env bash
set -euo pipefail

python3 -m unittest tests/test_zcode_supervisor.py tests/test_zcode_repo_setup.py tests/test_zcode_eval.py tests/test_codex_usage.py tests/test_goal_n_final_gate1.py tests/test_goal_o_accounting.py tests/test_goal_p_codex_worker_comparison.py tests/test_zcode_release.py tests/test_homebrew_formula.py tests/test_distribution_packaging.py tests/test_operational_gateway.py tests/test_pypi_readiness.py tests/test_public_release_surface.py tests/test_strict_contract_comparison.py tests/test_strict_path_reference_normalization.py tests/test_rollout_readiness.py tests/test_live_retry_preflight.py tests/test_zcode_one_task_loop.py tests/test_zcode_20_resumable_runner.py tests/test_zcode_20_resumable_state.py tests/test_zcode_20_provider_cooldown.py tests/test_zcode_20_usage_capture.py
python3 -m py_compile tools/zcode_supervisor/zcode_supervisor.py tools/zcode_supervisor/repo_setup.py tools/zcode_supervisor/auto_route.py tools/zcode_supervisor/operational_gateway.py tools/zcode_supervisor/one_task_loop.py tools/zcode_supervisor/zcode_20_resumable.py tools/zcode_eval/zcode_eval.py tools/zcode_eval/codex_usage.py tools/zcode_eval/goal_o_accounting.py tools/zcode_eval/goal_p_codex_worker_comparison.py tools/zcode_eval/goal_p_codex_worker_report.py tools/zcode_eval/goal_p_codex_worker_audit.py tools/zcode_eval/direct_launcher.py tools/zcode_eval/strict_contract.py tools/zcode_eval/strict_contract_tasks.py tools/zcode_eval/live_evidence.py tools/zcode_eval/rollout_readiness.py tools/zcode_eval/live_retry_preflight.py tools/zcode_eval/zcode_release.py tools/zcode_eval/pypi_readiness.py tools/zcode_control/__init__.py scripts/zcode-install-repo scripts/zcode-auto-route scripts/update-homebrew-formula scripts/verify-python-wheel scripts/check-pypi-release-readiness scripts/check_rollout_readiness.py scripts/check_goal_o_accounting.py scripts/check_goal_p_codex_worker_comparison.py scripts/check_live_evidence_manifest.py scripts/check_live_retry_preflight.py scripts/check_zcode_one_task_final_outcome.py scripts/check_zcode_20_final_outcome.py scripts/run_zcode_20_resumable.py
python3 scripts/check_live_evidence_manifest.py docs/zcode-strict-contract-v3/examples/live-evidence-manifest.fixture-ready.example.json >/dev/null
ruby -c packaging/homebrew/zcode-supervisor.rb >/dev/null
node --check tools/zcode_control/zcodectl.mjs
node --check tools/zcode_dashboard/server.mjs
node --check tools/zcode_dashboard/public/app.js
node --check tools/zcode_control/browser_scripts.mjs
node --test tests/zcode_provider_errors.test.mjs tests/zcode_run_packet_e2e.test.mjs tests/zcode_dashboard.test.mjs

rm -rf .local/check/wheelhouse
PIP_DISABLE_PIP_VERSION_CHECK=1 python3 -m pip wheel . --no-deps --no-build-isolation -w .local/check/wheelhouse >/dev/null
python3 scripts/verify-python-wheel .local/check/wheelhouse/*.whl >/dev/null

python3 tools/zcode_supervisor/zcode_supervisor.py packet \
  --workspace benchmarks/zcode-goal-mode \
  --objective "Audit the ledger implementation; it must pass npm test." \
  --allowed src/ledger.js \
  --forbidden test/ledger.test.js \
  --validation "npm test" \
  --effort max \
  --task-class production-gate \
  --risk-budget low \
  --max-changed-files 1 \
  --goal \
  --out .local/check/ledger.packet.json \
  --prompt-out .local/check/ledger.prompt.txt >/dev/null

python3 tools/zcode_supervisor/zcode_supervisor.py snapshot \
  --workspace benchmarks/zcode-goal-mode \
  --out .local/check/ledger.before.json >/dev/null

python3 tools/zcode_supervisor/zcode_supervisor.py audit \
  --workspace benchmarks/zcode-goal-mode \
  --snapshot .local/check/ledger.before.json \
  --packet .local/check/ledger.packet.json >/dev/null

bash scripts/check_zcode_delegated_artifact_quality.sh .
bash scripts/check_rollout_readiness.sh --output .local/check/rollout-readiness.json >/dev/null
