"""Read-only ZCode model_usage DB-delta worker token capture."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote


SOURCE_TYPE = "zcode_cli_model_usage_db_delta"
MODEL_USAGE_TABLE = "model_usage"
DEFAULT_DB_PATH = Path.home() / ".zcode/cli/db/db.sqlite"
TOTAL_TOKEN_COLUMNS = ("computed_total_tokens", "provider_total_tokens", "total_tokens")
PROVIDER_COLUMNS = ("provider_id", "provider")
MODEL_COLUMNS = ("model_id", "model")
STATUS_COLUMNS = ("status",)
TIMESTAMP_COLUMNS = ("started_at", "completed_at")
CANCELLED_STATUSES = {"cancelled", "canceled"}
TOKEN_COMPONENT_COLUMNS = {
    "input_tokens": ("input_tokens", "prompt_tokens"),
    "output_tokens": ("output_tokens", "completion_tokens"),
    "reasoning_tokens": ("reasoning_tokens", "reasoning_output_tokens"),
    "cache_read_tokens": ("cache_read_input_tokens", "cache_read_tokens"),
    "cache_write_tokens": ("cache_creation_input_tokens", "cache_write_tokens"),
}


def token_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    return None


def positive_token_int(value: Any) -> int | None:
    value = token_int(value)
    return value if value is not None and value > 0 else None


def current_time_ms() -> int:
    return int(time.time() * 1000)


def sqlite_readonly_uri(db_path: Path) -> str:
    path = db_path.expanduser().resolve(strict=False).as_posix()
    return f"file:{quote(path, safe='/')}?mode=ro"


def redacted_path(path: Path) -> str:
    expanded = path.expanduser().resolve(strict=False)
    home = Path.home().resolve(strict=False)
    try:
        return f"~/{expanded.relative_to(home).as_posix()}"
    except ValueError:
        return f"<redacted>/{expanded.name}"


def redacted_uri(path: Path) -> str:
    return f"file:{redacted_path(path)}?mode=ro"


def base_payload(db_path: Path) -> dict[str, Any]:
    return {
        "source_type": SOURCE_TYPE,
        "unit": "tokens",
        "source_db_path": redacted_path(db_path),
        "source_db_uri": redacted_uri(db_path),
        "sqlite_open_mode": "ro",
    }


def unavailable(db_path: Path, reason: str, **extra: Any) -> dict[str, Any]:
    return {
        **base_payload(db_path),
        "status": "unavailable",
        "usage": {},
        "no_usage_reason": reason,
        **{key: value for key, value in extra.items() if value is not None},
    }


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(sqlite_readonly_uri(db_path), uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def model_usage_columns(conn: sqlite3.Connection) -> set[str] | None:
    exists = conn.execute(
        "select 1 from sqlite_master where type = 'table' and name = ?",
        (MODEL_USAGE_TABLE,),
    ).fetchone()
    if exists is None:
        return None
    return {str(row["name"]) for row in conn.execute(f"pragma table_info({MODEL_USAGE_TABLE})")}


def first_available(columns: set[str], candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return None


def schema_guard(db_path: Path) -> tuple[sqlite3.Connection | None, set[str] | None, dict[str, Any] | None]:
    if not db_path.expanduser().is_file():
        return None, None, unavailable(db_path, "zcode_model_usage_db_missing")
    try:
        conn = connect_readonly(db_path)
    except sqlite3.Error as exc:
        return None, None, unavailable(db_path, "zcode_model_usage_db_open_failed", error=exc.__class__.__name__)
    columns = model_usage_columns(conn)
    if columns is None:
        conn.close()
        return None, None, unavailable(db_path, "zcode_model_usage_table_missing")
    if first_available(columns, TOTAL_TOKEN_COLUMNS) is None:
        conn.close()
        return None, None, unavailable(db_path, "zcode_model_usage_token_columns_missing")
    return conn, columns, None


def max_rowid(conn: sqlite3.Connection) -> int:
    row = conn.execute(f"select coalesce(max(rowid), 0) as max_rowid from {MODEL_USAGE_TABLE}").fetchone()
    return int(row["max_rowid"] or 0)


def capture_before_marker(db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    captured_at_ms = current_time_ms()
    conn, columns, failure = schema_guard(db_path)
    if failure is not None:
        return failure
    assert conn is not None and columns is not None
    try:
        before = max_rowid(conn)
    finally:
        conn.close()
    return {
        **base_payload(db_path),
        "status": "available",
        "before_max_rowid": before,
        "captured_at_ms": captured_at_ms,
        "token_total_columns": [column for column in TOTAL_TOKEN_COLUMNS if column in columns],
        "token_component_columns": sorted(
            column for candidates in TOKEN_COMPONENT_COLUMNS.values() for column in candidates if column in columns
        ),
    }


def rows_after_marker(
    conn: sqlite3.Connection,
    columns: set[str],
    before_max_rowid: int,
) -> list[dict[str, Any]]:
    selected = ["rowid"]
    for candidates in (PROVIDER_COLUMNS, MODEL_COLUMNS, STATUS_COLUMNS, TIMESTAMP_COLUMNS, TOTAL_TOKEN_COLUMNS):
        selected.extend(column for column in candidates if column in columns and column not in selected)
    for candidates in TOKEN_COMPONENT_COLUMNS.values():
        selected.extend(column for column in candidates if column in columns and column not in selected)
    quoted = ", ".join(selected)
    rows = conn.execute(
        f"select {quoted} from {MODEL_USAGE_TABLE} where rowid > ? order by rowid",
        (before_max_rowid,),
    ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def row_total_tokens(row: dict[str, Any], columns: set[str]) -> int | None:
    for column in TOTAL_TOKEN_COLUMNS:
        if column in columns:
            total = positive_token_int(row.get(column))
            if total is not None:
                return total
    return None


def row_total_token_value(row: dict[str, Any], columns: set[str]) -> int | None:
    for column in TOTAL_TOKEN_COLUMNS:
        if column in columns:
            total = token_int(row.get(column))
            if total is not None:
                return total
    return None


def row_component_tokens(row: dict[str, Any], columns: set[str]) -> dict[str, int]:
    usage: dict[str, int] = {}
    for target, candidates in TOKEN_COMPONENT_COLUMNS.items():
        for column in candidates:
            if column not in columns:
                continue
            value = token_int(row.get(column))
            if value is not None:
                usage[target] = value
                break
    return usage


def first_row_string(row: dict[str, Any], columns: set[str], candidates: tuple[str, ...]) -> str | None:
    for column in candidates:
        if column not in columns:
            continue
        value = row.get(column)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def row_string_values(rows: list[dict[str, Any]], columns: set[str], candidates: tuple[str, ...]) -> list[str | None]:
    return [first_row_string(row, columns, candidates) for row in rows]


def shared_row_string(rows: list[dict[str, Any]], columns: set[str], candidates: tuple[str, ...]) -> str | None:
    values = row_string_values(rows, columns, candidates)
    if any(value is None for value in values):
        return None
    unique = {value for value in values if value is not None}
    return next(iter(unique)) if len(unique) == 1 else None


def row_status(row: dict[str, Any], columns: set[str]) -> str | None:
    value = first_row_string(row, columns, STATUS_COLUMNS)
    return value.lower() if value else None


def row_timestamp_pair(row: dict[str, Any], columns: set[str]) -> tuple[int | None, int | None]:
    if not all(column in columns for column in TIMESTAMP_COLUMNS):
        return None, None
    return token_int(row.get("started_at")), token_int(row.get("completed_at"))


def multi_row_attribution_failure(
    rows: list[dict[str, Any]],
    columns: set[str],
    before_payload: dict[str, Any],
    *,
    before_max_rowid: int,
    after_max_rowid: int,
    after_captured_at_ms: int,
) -> str | None:
    row_ids = [int(row["rowid"]) for row in rows]
    if row_ids != list(range(before_max_rowid + 1, after_max_rowid + 1)):
        return "zcode_model_usage_non_contiguous_rows"
    if shared_row_string(rows, columns, PROVIDER_COLUMNS) is None:
        return "zcode_model_usage_provider_model_unsafe_for_multi_row_attribution"
    if shared_row_string(rows, columns, MODEL_COLUMNS) is None:
        return "zcode_model_usage_provider_model_unsafe_for_multi_row_attribution"
    if not all(column in columns for column in TIMESTAMP_COLUMNS):
        return "zcode_model_usage_multi_row_timestamps_missing"
    before_captured_at_ms = token_int(before_payload.get("captured_at_ms"))
    if before_captured_at_ms is None:
        return "zcode_model_usage_before_timestamp_missing_for_multi_row_attribution"
    previous_completed_at: int | None = None
    for row in rows:
        started_at, completed_at = row_timestamp_pair(row, columns)
        if started_at is None or completed_at is None:
            return "zcode_model_usage_row_timestamp_missing"
        if completed_at < started_at:
            return "zcode_model_usage_row_timestamp_invalid"
        if started_at < before_captured_at_ms or completed_at > after_captured_at_ms:
            return "zcode_model_usage_rows_outside_delegated_window"
        if previous_completed_at is not None and started_at < previous_completed_at:
            return "zcode_model_usage_rows_overlap_or_out_of_order"
        previous_completed_at = completed_at
    return None


def row_summary(row: dict[str, Any], columns: set[str], total: int) -> dict[str, Any]:
    summary: dict[str, Any] = {"rowid": int(row["rowid"]), "total_tokens": total}
    provider = first_row_string(row, columns, PROVIDER_COLUMNS)
    model = first_row_string(row, columns, MODEL_COLUMNS)
    status = row_status(row, columns)
    started_at, completed_at = row_timestamp_pair(row, columns)
    if provider:
        summary["provider"] = provider
    if model:
        summary["model"] = model
    if status:
        summary["status"] = status
    if started_at is not None:
        summary["started_at"] = started_at
    if completed_at is not None:
        summary["completed_at"] = completed_at
    components = row_component_tokens(row, columns)
    if components:
        summary["usage"] = components
    return summary


def aggregate_component_tokens(rows: list[dict[str, Any]], columns: set[str]) -> dict[str, int]:
    aggregate: dict[str, int] = {}
    for row in rows:
        for key, value in row_component_tokens(row, columns).items():
            aggregate[key] = aggregate.get(key, 0) + value
    return aggregate


def path_is_inside(path: Path, root: Path) -> bool:
    try:
        resolved = path.resolve()
        parent = root.resolve()
    except OSError:
        return False
    return resolved == parent or parent in resolved.parents


def ledger_has_records(path: Path) -> bool:
    try:
        return path.is_file() and any(line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    except OSError:
        return True


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")


def db_delta_record(
    db_path: Path,
    rows: list[dict[str, Any]],
    columns: set[str],
    *,
    before_max_rowid: int,
    after_max_rowid: int,
    before_captured_at_ms: int | None = None,
    after_captured_at_ms: int | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    totals: list[int] = []
    summaries: list[dict[str, Any]] = []
    zero_token_row_ids: list[int] = []
    for row in rows:
        total = row_total_token_value(row, columns)
        if total is None:
            return None, "zcode_model_usage_total_tokens_missing_null_or_zero"
        if total == 0:
            status = row_status(row, columns)
            if status not in CANCELLED_STATUSES:
                return None, "zcode_model_usage_total_tokens_missing_null_or_zero"
            zero_token_row_ids.append(int(row["rowid"]))
        totals.append(total)
        summaries.append(row_summary(row, columns, total))
    total_tokens = sum(totals)
    if total_tokens <= 0:
        return None, "zcode_model_usage_total_tokens_missing_null_or_zero"
    usage = {"total_tokens": total_tokens, **aggregate_component_tokens(rows, columns)}
    row_ids = [int(row["rowid"]) for row in rows]
    record: dict[str, Any] = {
        **base_payload(db_path),
        "status": "measured",
        "before_max_rowid": before_max_rowid,
        "after_max_rowid": after_max_rowid,
        "row_ids": row_ids,
        "row_count": len(rows),
        "unit": "tokens",
        "total_tokens": total_tokens,
        "usage": usage,
        "rows": summaries,
        "isolation": {
            "strategy": "single_delegated_row_model_usage_db_delta",
            "concurrent_usage_ruled_out_by": "compatible_provider_model_contiguous_rowids_and_delegated_window"
            if len(rows) > 1
            else "exactly_one_new_row",
            "isolation_assumption": "one delegated ZCode row ran between DB markers with no unrelated ZCode/ZAI usage",
        },
    }
    if before_captured_at_ms is not None:
        record["before_captured_at_ms"] = before_captured_at_ms
    if after_captured_at_ms is not None:
        record["after_captured_at_ms"] = after_captured_at_ms
    if zero_token_row_ids:
        record["zero_token_row_ids"] = zero_token_row_ids
        record["token_bearing_row_count"] = len(rows) - len(zero_token_row_ids)
    provider = first_row_string(rows[0], columns, PROVIDER_COLUMNS)
    model = first_row_string(rows[0], columns, MODEL_COLUMNS)
    if provider:
        record["provider"] = provider
    if model:
        record["model"] = model
    return record, None


def capture_after_delta(
    before_payload: dict[str, Any],
    *,
    db_path: Path = DEFAULT_DB_PATH,
    ledger_path: Path | None = None,
    row_dir: Path | None = None,
) -> dict[str, Any]:
    before_max_rowid = token_int(before_payload.get("before_max_rowid"))
    if before_payload.get("status") != "available" or before_max_rowid is None:
        return unavailable(
            db_path,
            "zcode_model_usage_before_marker_unavailable",
            before_status=before_payload.get("status"),
            before_no_usage_reason=before_payload.get("no_usage_reason"),
        )
    conn, columns, failure = schema_guard(db_path)
    if failure is not None:
        return failure | {"before_max_rowid": before_max_rowid}
    assert conn is not None and columns is not None
    try:
        after_max_rowid = max_rowid(conn)
        rows = rows_after_marker(conn, columns, before_max_rowid)
    finally:
        conn.close()
    after_captured_at_ms = current_time_ms()
    row_ids = [int(row["rowid"]) for row in rows]
    if not rows:
        return unavailable(db_path, "zcode_model_usage_no_new_rows", before_max_rowid=before_max_rowid, after_max_rowid=after_max_rowid, row_ids=[])
    if len(rows) > 1:
        attribution_failure = multi_row_attribution_failure(
            rows,
            columns,
            before_payload,
            before_max_rowid=before_max_rowid,
            after_max_rowid=after_max_rowid,
            after_captured_at_ms=after_captured_at_ms,
        )
        if attribution_failure is not None:
            return unavailable(
                db_path,
                attribution_failure,
                before_max_rowid=before_max_rowid,
                after_max_rowid=after_max_rowid,
                row_ids=row_ids,
                row_count=len(rows),
            )
    before_captured_at_ms = token_int(before_payload.get("captured_at_ms"))
    record, no_usage_reason = db_delta_record(
        db_path,
        rows,
        columns,
        before_max_rowid=before_max_rowid,
        after_max_rowid=after_max_rowid,
        before_captured_at_ms=before_captured_at_ms,
        after_captured_at_ms=after_captured_at_ms,
    )
    if record is None:
        return unavailable(
            db_path,
            no_usage_reason or "zcode_model_usage_total_tokens_missing_null_or_zero",
            before_max_rowid=before_max_rowid,
            after_max_rowid=after_max_rowid,
            row_ids=row_ids,
            row_count=len(rows),
        )
    if ledger_path is not None:
        row_root = row_dir or ledger_path.parent
        if not path_is_inside(ledger_path, row_root):
            return unavailable(db_path, "zcode_model_usage_ledger_out_of_scope", before_max_rowid=before_max_rowid, after_max_rowid=after_max_rowid, row_ids=row_ids, row_count=len(rows))
        if ledger_has_records(ledger_path):
            return unavailable(db_path, "zcode_model_usage_provider_ledger_already_present", before_max_rowid=before_max_rowid, after_max_rowid=after_max_rowid, row_ids=row_ids, row_count=len(rows))
        record["provider_usage_ledger_path"] = ledger_path.name if ledger_path.parent == row_root else str(ledger_path)
        record["provider_usage_ledger_appended"] = True
        append_jsonl(ledger_path, record)
    return record


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON payload must be an object: {path}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    before = subparsers.add_parser("before", help="Capture max(rowid) before a delegated row.")
    before.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    before.add_argument("--out", type=Path, required=True)
    after = subparsers.add_parser("after", help="Capture post-row delta and optionally append provider ledger JSONL.")
    after.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    after.add_argument("--before", type=Path, required=True)
    after.add_argument("--ledger", type=Path)
    after.add_argument("--row-dir", type=Path)
    after.add_argument("--out", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "before":
        payload = capture_before_marker(args.db)
    else:
        payload = capture_after_delta(
            read_json(args.before),
            db_path=args.db,
            ledger_path=args.ledger,
            row_dir=args.row_dir,
        )
    write_json(args.out, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
