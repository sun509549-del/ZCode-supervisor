import json
import tempfile
import unittest
from pathlib import Path

from tools.zcode_eval.goal_o_accounting import (
    OUTCOME_BLOCKED_CODEX_UNAVAILABLE,
    OUTCOME_BLOCKED_PHASE_SPLIT,
    OUTCOME_CLAIMABLE,
    build_goal_o_accounting_report,
)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def final_payload(worker_tokens: int | None = 90) -> dict:
    rows = []
    for index in range(3):
        rows.append(
            {
                "row_id": f"task-{index}",
                "claim_family": "zcode_required_20_live",
                "worker_usage_status": "measured" if worker_tokens else "unavailable",
                "worker_usage_unit": "tokens" if worker_tokens else "unknown",
                "worker_total_tokens": 30 if worker_tokens else None,
            }
        )
    return {
        "final_outcome": "zcode_20_live_partial_measured",
        "claim_family": "zcode_required_20_live",
        "delegated_rows": 3,
        "strict_green_count": 2,
        "failure_count": 1,
        "worker_token_measured_count": 3 if worker_tokens else 0,
        "worker_total_tokens": worker_tokens,
        "worker_usage_unit": "tokens" if worker_tokens else "unknown",
        "rows": rows,
        "route_used_summary": {"zcode_cli": 3, "codex_fallback": 0},
        "zcode_implemented": True,
        "codex_touched_target_artifact": False,
        "production_green_path_enabled": False,
        "direct_mode_default": False,
    }


def usage_record(phase: str | None, tokens: int | None, *, family: str | None = "zcode_required_20_live") -> dict:
    usage = {"usage_missing": True} if tokens is None else {"effective_codex_work": tokens, "usage_missing": False}
    record = {"usage": usage}
    if phase is not None:
        record["phase"] = phase
    if family is not None:
        record["claim_family"] = family
    return record


class GoalOAccountingTests(unittest.TestCase):
    def test_unavailable_codex_tokens_are_not_zero_filled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(root / "final-outcome.json", final_payload())

            report = build_goal_o_accounting_report(root)

            self.assertEqual(report["final_outcome"], OUTCOME_BLOCKED_CODEX_UNAVAILABLE)
            self.assertEqual(report["codex_orchestration_token_status"], "unavailable")
            self.assertIsNone(report["codex_orchestration_total_tokens"])
            self.assertIsNone(report["codex_phase_tokens"])
            self.assertFalse(report["total_workflow_savings_claimable"])
            self.assertEqual(report["blocker"], "codex_orchestration_usage_unavailable")

    def test_phase_split_missing_blocks_total_workflow_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "codex.jsonl"
            write_json(root / "final-outcome.json", final_payload())
            write_jsonl(ledger, [usage_record(None, 42)])

            report = build_goal_o_accounting_report(root, codex_usage_ledgers=[ledger])

            self.assertEqual(report["final_outcome"], OUTCOME_BLOCKED_PHASE_SPLIT)
            self.assertEqual(report["codex_orchestration_token_status"], "phase_split_unavailable")
            self.assertIsNone(report["codex_orchestration_total_tokens"])
            self.assertEqual(report["blocker"], "codex_phase_split_unavailable")

    def test_engineering_overhead_is_separated_from_orchestration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "codex.jsonl"
            write_json(root / "final-outcome.json", final_payload())
            write_jsonl(
                ledger,
                [
                    usage_record("plan", 10),
                    usage_record("contract_generation", 15),
                    usage_record("implementation", 99),
                ],
            )

            report = build_goal_o_accounting_report(root, codex_usage_ledgers=[ledger])

            self.assertEqual(report["final_outcome"], OUTCOME_CLAIMABLE)
            self.assertEqual(report["codex_orchestration_total_tokens"], 25)
            self.assertEqual(report["codex_phase_tokens"], {"contract_generation": 15, "plan": 10})
            self.assertEqual(report["engineering_overhead_tokens"], 99)
            self.assertTrue(report["total_workflow_savings_claimable"])

    def test_zero_usage_guard_blocks_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "codex.jsonl"
            write_json(root / "final-outcome.json", final_payload())
            write_jsonl(ledger, [usage_record("plan", 0)])

            report = build_goal_o_accounting_report(root, codex_usage_ledgers=[ledger])

            self.assertEqual(report["final_outcome"], OUTCOME_BLOCKED_CODEX_UNAVAILABLE)
            self.assertIsNone(report["codex_orchestration_total_tokens"])
            self.assertEqual(report["blocker"], "codex_orchestration_usage_unavailable")

    def test_missing_worker_tokens_are_terminal_evidence_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "codex.jsonl"
            write_json(root / "final-outcome.json", final_payload(worker_tokens=None))
            write_jsonl(ledger, [usage_record("plan", 10)])

            report = build_goal_o_accounting_report(root, codex_usage_ledgers=[ledger])

            self.assertEqual(report["final_outcome"], "terminal_evidence_missing")
            self.assertEqual(report["blocker"], "zcode_worker_token_evidence_missing")
            self.assertFalse(report["total_workflow_savings_claimable"])


if __name__ == "__main__":
    unittest.main()
