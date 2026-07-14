import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools.zcode_eval.strict_contract_comparison import (
    read_worker_usage_payload,
    zcode_worker_usage_fields,
)
from tools.zcode_eval.zcode_model_usage_db_delta import (
    SOURCE_TYPE,
    capture_after_delta,
    capture_before_marker,
)


def create_model_usage_db(path: Path, *, with_total: bool = True, with_attribution_columns: bool = False) -> None:
    total_columns = ", computed_total_tokens INTEGER, provider_total_tokens INTEGER" if with_total else ""
    attribution_columns = ", status TEXT, started_at INTEGER, completed_at INTEGER" if with_attribution_columns else ""
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "create table model_usage ("
            "provider_id TEXT, model_id TEXT, "
            "input_tokens INTEGER, output_tokens INTEGER, reasoning_tokens INTEGER, "
            "cache_creation_input_tokens INTEGER, cache_read_input_tokens INTEGER"
            f"{total_columns}{attribution_columns})"
        )
        conn.commit()
    finally:
        conn.close()


def insert_usage(
    path: Path,
    *,
    total: int | None = 42,
    provider: str = "zai",
    model: str = "glm-5.2",
    input_tokens: int = 31,
    output_tokens: int = 11,
    reasoning_tokens: int = 5,
    cache_write_tokens: int = 7,
    cache_read_tokens: int = 3,
    status: str | None = None,
    started_at: int | None = None,
    completed_at: int | None = None,
) -> None:
    conn = sqlite3.connect(path)
    try:
        columns = [
            "provider_id",
            "model_id",
            "input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
            "computed_total_tokens",
            "provider_total_tokens",
        ]
        values = [
            provider,
            model,
            input_tokens,
            output_tokens,
            reasoning_tokens,
            cache_write_tokens,
            cache_read_tokens,
            total,
            total,
        ]
        if status is not None or started_at is not None or completed_at is not None:
            columns.extend(["status", "started_at", "completed_at"])
            values.extend([status, started_at, completed_at])
        conn.execute(
            f"insert into model_usage ({', '.join(columns)}) values ({', '.join('?' for _ in columns)})",
            values,
        )
        conn.commit()
    finally:
        conn.close()


class ZCodeModelUsageDbDeltaTests(unittest.TestCase):
    def test_single_positive_delta_writes_row_local_provider_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "db.sqlite"
            row_dir = root / "tasks/policy/zcode-delegated"
            row_dir.mkdir(parents=True)
            create_model_usage_db(db)
            insert_usage(db, total=9)
            before = capture_before_marker(db)
            insert_usage(db, total=42)

            ledger = row_dir / "worker-usage.jsonl"
            result = capture_after_delta(before, db_path=db, ledger_path=ledger, row_dir=row_dir)
            records = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]

            self.assertEqual(before["status"], "available")
            self.assertEqual(before["before_max_rowid"], 1)
            self.assertEqual(result["status"], "measured")
            self.assertEqual(result["source_type"], SOURCE_TYPE)
            self.assertEqual(result["unit"], "tokens")
            self.assertEqual(result["before_max_rowid"], 1)
            self.assertEqual(result["after_max_rowid"], 2)
            self.assertEqual(result["row_ids"], [2])
            self.assertEqual(result["provider"], "zai")
            self.assertEqual(result["model"], "glm-5.2")
            self.assertEqual(result["total_tokens"], 42)
            self.assertEqual(result["usage"]["input_tokens"], 31)
            self.assertEqual(result["usage"]["output_tokens"], 11)
            self.assertEqual(result["usage"]["reasoning_tokens"], 5)
            self.assertEqual(result["usage"]["cache_write_tokens"], 7)
            self.assertEqual(result["usage"]["cache_read_tokens"], 3)
            self.assertEqual(records, [result])
            self.assertEqual(result["source_db_path"], "<redacted>/db.sqlite")
            self.assertTrue(result["source_db_uri"].endswith("?mode=ro"))

            aggregated = read_worker_usage_payload(ledger)
            self.assertIsNotNone(aggregated)
            self.assertEqual(aggregated["tokens_source"], SOURCE_TYPE)
            self.assertEqual(aggregated["total_tokens"], 42)
            fields = zcode_worker_usage_fields(
                {"usage_accounting": {"worker_usage_source_path": "worker-usage.jsonl"}},
                row_dir / "zcode-run.json",
            )
            self.assertEqual(fields["worker_usage_status"], "measured")
            self.assertEqual(fields["worker_usage_capture_method"], SOURCE_TYPE)
            self.assertEqual(fields["worker_total_tokens"], 42)

    def test_missing_db_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "missing.sqlite"
            before = capture_before_marker(db)

            self.assertEqual(before["status"], "unavailable")
            self.assertEqual(before["no_usage_reason"], "zcode_model_usage_db_missing")

    def test_missing_model_usage_table_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "db.sqlite"
            sqlite3.connect(db).close()

            before = capture_before_marker(db)

            self.assertEqual(before["status"], "unavailable")
            self.assertEqual(before["no_usage_reason"], "zcode_model_usage_table_missing")

    def test_missing_total_token_columns_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "db.sqlite"
            create_model_usage_db(db, with_total=False)

            before = capture_before_marker(db)

            self.assertEqual(before["status"], "unavailable")
            self.assertEqual(before["no_usage_reason"], "zcode_model_usage_token_columns_missing")

    def test_no_new_rows_stays_unavailable_not_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "db.sqlite"
            create_model_usage_db(db)
            insert_usage(db, total=9)
            before = capture_before_marker(db)

            result = capture_after_delta(before, db_path=db)

            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["no_usage_reason"], "zcode_model_usage_no_new_rows")
            self.assertEqual(result["row_ids"], [])
            self.assertNotIn("total_tokens", result)

    def test_zero_total_tokens_stays_unavailable_not_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "db.sqlite"
            create_model_usage_db(db)
            before = capture_before_marker(db)
            insert_usage(db, total=0)

            result = capture_after_delta(before, db_path=db)

            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["no_usage_reason"], "zcode_model_usage_total_tokens_missing_null_or_zero")
            self.assertIsNone(result.get("total_tokens"))

    def test_multiple_new_rows_without_timestamps_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "db.sqlite"
            row_dir = Path(tmp) / "row"
            row_dir.mkdir()
            create_model_usage_db(db)
            before = capture_before_marker(db)
            insert_usage(db, total=10)
            insert_usage(db, total=20)
            ledger = row_dir / "worker-usage.jsonl"

            result = capture_after_delta(before, db_path=db, ledger_path=ledger, row_dir=row_dir)

            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["no_usage_reason"], "zcode_model_usage_multi_row_timestamps_missing")
            self.assertEqual(result["row_ids"], [1, 2])
            self.assertFalse(ledger.exists())

    def test_existing_provider_ledger_prevents_double_count_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "db.sqlite"
            row_dir = Path(tmp) / "row"
            row_dir.mkdir()
            create_model_usage_db(db)
            before = capture_before_marker(db)
            insert_usage(db, total=42)
            ledger = row_dir / "worker-usage.jsonl"
            ledger.write_text(json.dumps({"source_type": "provider_usage_ledger", "unit": "tokens", "usage": {"total_tokens": 1}}) + "\n", encoding="utf-8")

            result = capture_after_delta(before, db_path=db, ledger_path=ledger, row_dir=row_dir)

            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["no_usage_reason"], "zcode_model_usage_provider_ledger_already_present")
            self.assertEqual(len(ledger.read_text(encoding="utf-8").splitlines()), 1)


class TestModelUsageDbDeltaMultiRowAttribution(unittest.TestCase):
    def test_compatible_multi_row_delta_writes_aggregate_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "db.sqlite"
            row_dir = root / "tasks/policy/zcode-delegated"
            row_dir.mkdir(parents=True)
            create_model_usage_db(db, with_attribution_columns=True)
            before = capture_before_marker(db)
            start = before["captured_at_ms"]
            insert_usage(
                db,
                total=10,
                input_tokens=7,
                output_tokens=3,
                reasoning_tokens=0,
                cache_write_tokens=0,
                cache_read_tokens=2,
                status="completed",
                started_at=start,
                completed_at=start,
            )
            insert_usage(
                db,
                total=20,
                input_tokens=17,
                output_tokens=3,
                reasoning_tokens=0,
                cache_write_tokens=0,
                cache_read_tokens=4,
                status="completed",
                started_at=start,
                completed_at=start,
            )
            insert_usage(
                db,
                total=0,
                input_tokens=0,
                output_tokens=0,
                reasoning_tokens=0,
                cache_write_tokens=0,
                cache_read_tokens=0,
                status="cancelled",
                started_at=start,
                completed_at=start,
            )

            ledger = row_dir / "worker-usage.jsonl"
            result = capture_after_delta(before, db_path=db, ledger_path=ledger, row_dir=row_dir)
            records = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]

            self.assertEqual(result["status"], "measured")
            self.assertEqual(result["source_type"], SOURCE_TYPE)
            self.assertEqual(result["unit"], "tokens")
            self.assertEqual(result["row_ids"], [1, 2, 3])
            self.assertEqual(result["row_count"], 3)
            self.assertEqual(result["total_tokens"], 30)
            self.assertEqual(result["usage"]["total_tokens"], 30)
            self.assertEqual(result["usage"]["input_tokens"], 24)
            self.assertEqual(result["usage"]["output_tokens"], 6)
            self.assertEqual(result["usage"]["cache_read_tokens"], 6)
            self.assertEqual(result["zero_token_row_ids"], [3])
            self.assertEqual(result["token_bearing_row_count"], 2)
            self.assertEqual([row["total_tokens"] for row in result["rows"]], [10, 20, 0])
            self.assertEqual(result["rows"][2]["status"], "cancelled")
            self.assertEqual(result["isolation"]["strategy"], "single_delegated_row_model_usage_db_delta")
            self.assertIn("isolation_assumption", result["isolation"])
            self.assertEqual(records, [result])

            aggregated = read_worker_usage_payload(ledger)
            self.assertIsNotNone(aggregated)
            self.assertEqual(aggregated["tokens_source"], SOURCE_TYPE)
            self.assertEqual(aggregated["total_tokens"], 30)
            fields = zcode_worker_usage_fields(
                {"usage_accounting": {"worker_usage_source_path": "worker-usage.jsonl"}},
                row_dir / "zcode-run.json",
            )
            self.assertEqual(fields["worker_usage_status"], "measured")
            self.assertEqual(fields["worker_usage_capture_method"], SOURCE_TYPE)
            self.assertEqual(fields["worker_total_tokens"], 30)

    def test_multi_row_fails_closed_on_mixed_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "db.sqlite"
            create_model_usage_db(db, with_attribution_columns=True)
            before = capture_before_marker(db)
            start = before["captured_at_ms"]
            insert_usage(db, total=10, provider="zai", status="completed", started_at=start, completed_at=start)
            insert_usage(db, total=20, provider="other", status="completed", started_at=start, completed_at=start)

            result = capture_after_delta(before, db_path=db)

            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(
                result["no_usage_reason"],
                "zcode_model_usage_provider_model_unsafe_for_multi_row_attribution",
            )

    def test_multi_row_fails_closed_when_rows_are_outside_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "db.sqlite"
            create_model_usage_db(db, with_attribution_columns=True)
            before = capture_before_marker(db)
            start = before["captured_at_ms"] - 100
            insert_usage(db, total=10, status="completed", started_at=start, completed_at=start + 10)
            insert_usage(db, total=20, status="completed", started_at=start + 11, completed_at=start + 20)

            result = capture_after_delta(before, db_path=db)

            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["no_usage_reason"], "zcode_model_usage_rows_outside_delegated_window")

    def test_multi_row_fails_closed_when_all_totals_are_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "db.sqlite"
            create_model_usage_db(db, with_attribution_columns=True)
            before = capture_before_marker(db)
            start = before["captured_at_ms"]
            insert_usage(db, total=0, status="cancelled", started_at=start, completed_at=start)
            insert_usage(db, total=0, status="cancelled", started_at=start, completed_at=start)

            result = capture_after_delta(before, db_path=db)

            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["no_usage_reason"], "zcode_model_usage_total_tokens_missing_null_or_zero")
