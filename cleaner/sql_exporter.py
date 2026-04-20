import csv
import os
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

from tqdm import tqdm

from .utils import batched, is_nan_like, setup_json_logger


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class SqlColumnType:
    sql_type: str
    py_type: Any


def _infer_column_sql_types(
    fieldnames: Sequence[str],
    rows: Iterable[Dict[str, Any]],
) -> Dict[str, SqlColumnType]:
    """
    Infer schema from cleaned CSV using the requested mapping rules:
    - Dates -> DATE
    - Currency/amount -> FLOAT
    - Text -> VARCHAR
    """
    inferred: Dict[str, SqlColumnType] = {}

    sample: Dict[str, List[str]] = {c: [] for c in fieldnames}
    for r in rows:
        for c in fieldnames:
            v = r.get(c)
            if v is None:
                continue
            if isinstance(v, str) and v.strip() == "":
                continue
            if len(sample[c]) >= 20:
                continue
            sample[c].append(str(v).strip())
        if all(len(sample[c]) >= 1 for c in fieldnames):
            break

    def looks_like_date(col: str) -> bool:
        if "date" not in col.lower():
            return False
        values = sample[col]
        if not values:
            return False
        return any(DATE_RE.match(v) for v in values) and all(DATE_RE.match(v) for v in values)

    amount_keywords = ("amount", "amt", "value", "total", "price", "expense", "income", "balance")

    def looks_like_amount(col: str) -> bool:
        if not any(k in col.lower() for k in amount_keywords):
            return False
        values = sample[col]
        if not values:
            return False
        for v in values:
            try:
                float(v)
            except Exception:
                return False
        return True

    for c in fieldnames:
        if looks_like_date(c):
            inferred[c] = SqlColumnType(sql_type="DATE", py_type=str)
        elif looks_like_amount(c):
            inferred[c] = SqlColumnType(sql_type="FLOAT", py_type=float)
        else:
            inferred[c] = SqlColumnType(sql_type="VARCHAR", py_type=str)

    return inferred


def _quote_ident(identifier: str, *, dialect: str) -> str:
    escaped = identifier.replace('"', '""')
    return f'"{escaped}"'


def _table_exists_sqlite(conn: sqlite3.Connection, table_name: str) -> bool:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    )
    return cur.fetchone() is not None


def _table_exists_postgres(conn, table_name: str, schema: str) -> bool:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema=%s AND table_name=%s
        )
        """,
        (schema, table_name),
    )
    exists = cur.fetchone()[0]
    cur.close()
    return bool(exists)


def export_csv_to_db(
    cleaned_csv_path: str,
    db_url_or_path: str,
    *,
    table_name: str = "financial_records",
    batch_size: int = 1000,
    truncate: bool = False,
    log_dir: str = "logs",
    postgres_schema: str = "public",
    show_progress: bool = True,
) -> Dict[str, Any]:
    """
    Export a cleaned CSV into a SQL table (SQLite or PostgreSQL).
    Returns a stats dictionary.
    """
    logger = setup_json_logger("automated_financial_cleaner", os.path.join(log_dir, "cleaner.log"))

    if not os.path.exists(cleaned_csv_path):
        raise FileNotFoundError(cleaned_csv_path)

    logger.info(
        "sql_export_start",
        extra={"cleaned_csv_path": cleaned_csv_path, "db_url_or_path": db_url_or_path, "table_name": table_name},
    )

    is_postgres = db_url_or_path.lower().startswith(("postgres://", "postgresql://"))

    with open(cleaned_csv_path, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("CSV header not found (malformed or empty cleaned CSV).")
        fieldnames = [c.strip() for c in reader.fieldnames]
        rows = list(reader)

    if not fieldnames:
        raise ValueError("CSV header not found (empty cleaned CSV).")

    inferred_types = _infer_column_sql_types(fieldnames, rows)
    column_types = {c: inferred_types[c].sql_type for c in fieldnames}
    inserted = 0

    if is_postgres:
        try:
            import psycopg2  # type: ignore
        except ImportError as e:
            raise RuntimeError("psycopg2-binary is required for PostgreSQL export") from e

        conn = psycopg2.connect(db_url_or_path)
        try:
            exists = _table_exists_postgres(conn, table_name, postgres_schema)
            cur = conn.cursor()

            col_defs = []
            for c in fieldnames:
                sql_t = inferred_types[c].sql_type
                if sql_t.upper() == "FLOAT":
                    sql_t = "FLOAT8"
                col_defs.append(f"{_quote_ident(c, dialect='postgres')} {sql_t}")

            full_table = f"{_quote_ident(postgres_schema, dialect='postgres')}.{_quote_ident(table_name, dialect='postgres')}"
            create_sql = f"CREATE TABLE IF NOT EXISTS {full_table} ({', '.join(col_defs)});"
            cur.execute(create_sql)

            if exists and truncate:
                cur.execute(f"TRUNCATE TABLE {full_table};")
                conn.commit()

            placeholders = ", ".join(["%s"] * len(fieldnames))
            col_list = ", ".join(_quote_ident(c, dialect='postgres') for c in fieldnames)
            insert_sql = f"INSERT INTO {full_table} ({col_list}) VALUES ({placeholders});"

            def convert_value(col: str, v: Any) -> Any:
                if v is None:
                    return None
                if isinstance(v, str) and v.strip() == "":
                    return None
                if is_nan_like(v):
                    return None
                t = inferred_types[col]
                if t.py_type is float:
                    return float(v)
                return str(v)

            for batch in tqdm(
                list(batched(rows, batch_size)),
                desc="SQL insert batches",
                unit="batch",
                disable=not show_progress,
            ):
                params = [[convert_value(c, r.get(c)) for c in fieldnames] for r in batch]
                cur.executemany(insert_sql, params)
                inserted += cur.rowcount if cur.rowcount != -1 else len(params)
                conn.commit()

            cur.close()
        finally:
            conn.close()
    else:
        conn = sqlite3.connect(db_url_or_path)
        try:
            exists = _table_exists_sqlite(conn, table_name)

            col_defs = []
            for c in fieldnames:
                sql_t = inferred_types[c].sql_type
                col_defs.append(f"{_quote_ident(c, dialect='sqlite')} {sql_t}")

            create_sql = f"CREATE TABLE IF NOT EXISTS {_quote_ident(table_name, dialect='sqlite')} ({', '.join(col_defs)});"
            conn.execute(create_sql)
            conn.commit()

            if exists and truncate:
                conn.execute(f"DELETE FROM {_quote_ident(table_name, dialect='sqlite')};")
                conn.commit()

            placeholders = ", ".join(["?"] * len(fieldnames))
            col_list = ", ".join(_quote_ident(c, dialect='sqlite') for c in fieldnames)
            insert_sql = f"INSERT INTO {_quote_ident(table_name, dialect='sqlite')} ({col_list}) VALUES ({placeholders});"

            def convert_value(col: str, v: Any) -> Any:
                if v is None:
                    return None
                if isinstance(v, str) and v.strip() == "":
                    return None
                if is_nan_like(v):
                    return None
                t = inferred_types[col]
                if t.py_type is float:
                    return float(v)
                return str(v)

            cur = conn.cursor()
            for batch in tqdm(
                list(batched(rows, batch_size)),
                desc="SQL insert batches",
                unit="batch",
                disable=not show_progress,
            ):
                params = [[convert_value(c, r.get(c)) for c in fieldnames] for r in batch]
                cur.executemany(insert_sql, params)
                inserted += cur.rowcount if cur.rowcount != -1 else len(params)
                conn.commit()
            cur.close()
        finally:
            conn.close()

    stats: Dict[str, Any] = {
        "cleaned_csv_path": cleaned_csv_path,
        "db_url_or_path": db_url_or_path,
        "table_name": table_name,
        "rows_in_csv": len(rows),
        "rows_inserted": inserted,
        "truncate": truncate,
        "postgres": is_postgres,
        "column_types": column_types,
    }
    logger.info("sql_export_complete", extra=stats)
    return stats

