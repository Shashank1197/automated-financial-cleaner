import sqlite3

from cleaner.sql_exporter import export_csv_to_db


def _write_cleaned_csv(path):
    path.write_text(
        "date,description,amount\n"
        "2026-04-16,Coffee Shop,1200\n"
        ",Bad Date,10\n"
        "2026-04-18,Empty Amount,\n",
        encoding="utf-8",
    )


def test_sql_export_sqlite(tmp_path):
    cleaned_csv = tmp_path / "cleaned.csv"
    _write_cleaned_csv(cleaned_csv)

    db_path = tmp_path / "database.db"
    stats = export_csv_to_db(
        str(cleaned_csv),
        str(db_path),
        table_name="financial_records",
        truncate=True,
        show_progress=False,
    )
    assert stats["rows_in_csv"] == 3
    assert stats["rows_inserted"] == 3

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            ("financial_records",),
        )
        assert cur.fetchone() is not None

        count = conn.execute("SELECT COUNT(*) FROM financial_records").fetchone()[0]
        assert count == 3

        date_val = conn.execute(
            "SELECT date FROM financial_records WHERE description=? LIMIT 1",
            ("Coffee Shop",),
        ).fetchone()[0]
        assert date_val == "2026-04-16"

        amount_type = conn.execute(
            "SELECT typeof(amount) FROM financial_records WHERE description=? LIMIT 1",
            ("Coffee Shop",),
        ).fetchone()[0]
        assert amount_type == "real"
    finally:
        conn.close()


def test_sql_export_truncate(tmp_path):
    cleaned_csv = tmp_path / "cleaned.csv"
    _write_cleaned_csv(cleaned_csv)

    db_path = tmp_path / "database.db"

    export_csv_to_db(
        str(cleaned_csv),
        str(db_path),
        table_name="financial_records",
        truncate=False,
        show_progress=False,
    )
    export_csv_to_db(
        str(cleaned_csv),
        str(db_path),
        table_name="financial_records",
        truncate=True,
        show_progress=False,
    )

    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM financial_records").fetchone()[0]
        assert count == 3
    finally:
        conn.close()

