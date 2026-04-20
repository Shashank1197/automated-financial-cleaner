import argparse
import csv
from typing import Any, Optional

from cleaner.cleaner import clean_csv
from cleaner.sql_exporter import export_csv_to_db


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="automated-financial-cleaner",
        description="Automated Financial Data Cleaner: clean messy financial CSVs and export to SQL.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    clean_p = subparsers.add_parser("clean", help="Clean messy financial CSV")
    clean_p.add_argument("input_csv", help="Input messy CSV file path")
    clean_p.add_argument("output_csv", help="Output cleaned CSV file path")
    clean_p.add_argument("--report", default=None, help="Optional report path (e.g. report.json)")
    clean_p.add_argument(
        "--preview-rows",
        type=int,
        default=10,
        help="How many cleaned rows to print to the terminal (default: 10, set to 0 to disable)",
    )
    clean_p.add_argument(
        "--print-cleaned-all",
        action="store_true",
        help="Print the entire cleaned CSV to the terminal (use with small files only)",
    )
    clean_p.add_argument(
        "--verbose",
        action="store_true",
        help="Print each cleaned transaction row as it is processed",
    )
    clean_p.add_argument(
        "--enable-fuzzy-ghosts",
        action="store_true",
        help="Optional near-duplicate removal using amount+date+description fuzzy matching",
    )
    clean_p.add_argument(
        "--fuzzy-threshold",
        type=float,
        default=0.9,
        help="Similarity threshold for fuzzy ghost detection (default: 0.9)",
    )

    export_p = subparsers.add_parser("export-sql", help="Export cleaned CSV to a SQL database")
    export_p.add_argument("cleaned_csv", help="Input cleaned CSV file path")
    export_p.add_argument(
        "database",
        help="SQLite database file path (e.g. database.db) or Postgres connection URL (e.g. postgresql://user:pass@host:5432/db)",
    )
    export_p.add_argument("--table-name", default="financial_records", help="Target SQL table name")
    export_p.add_argument("--truncate", action="store_true", help="Truncate table if it exists")

    return parser


def _print_csv_preview(path: str, *, max_rows: Optional[int]) -> None:
    with open(path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []

        if max_rows == 0:
            return

        print(",".join(fieldnames))
        shown = 0
        for row in reader:
            values = ["" if row.get(c) is None else str(row.get(c)) for c in fieldnames]
            print(",".join(values))
            shown += 1
            if max_rows is not None and shown >= max_rows:
                break


def _print_clean_summary(stats: dict) -> None:
    print("\nClean Summary")
    print(f"Input rows: {stats.get('total_rows_input')}")
    print(f"Cleaned rows: {stats.get('total_rows_cleaned')}")
    print(f"Duplicates removed: {stats.get('duplicates_removed')}")
    print(f"Ghost transactions removed: {stats.get('ghost_transactions_removed')}")
    print(f"Currency symbols found: {stats.get('currency_symbols_found')}")
    print(f"Invalid dates fixed: {stats.get('invalid_dates_fixed')}")

    date_cols = stats.get("date_columns") or []
    amount_cols = stats.get("amount_columns") or []
    if date_cols:
        print(f"Date columns: {', '.join(date_cols)}")
    if amount_cols:
        print(f"Amount columns: {', '.join(amount_cols)}")

    print(f"Output CSV: {stats.get('output_csv_path')}")


def main(argv: Optional[list[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "clean":
        def on_row_cleaned(row_index: int, cleaned_row: dict, meta: dict) -> None:
            date_val: Any = cleaned_row.get("date", "")
            desc_val: Any = cleaned_row.get("description", "")
            amt_val: Any = cleaned_row.get("amount", "")
            date_fixed = meta.get("date_fixed", 0)
            currency_found = meta.get("currency_symbols_found", 0)
            print(
                f"[{row_index}] {date_val} | {desc_val} | {amt_val} "
                f"(date_fixed={date_fixed}, currency_found={currency_found})"
            )

        show_progress = not bool(args.verbose)
        stats = clean_csv(
            args.input_csv,
            args.output_csv,
            report_path=args.report,
            show_progress=show_progress,
            enable_fuzzy_ghost_detection=args.enable_fuzzy_ghosts,
            fuzzy_threshold=args.fuzzy_threshold,
            on_row_cleaned=on_row_cleaned if args.verbose else None,
        )
        _print_clean_summary(stats)

        # Print a preview so you can see the cleaned rows in the terminal.
        if args.print_cleaned_all:
            _print_csv_preview(args.output_csv, max_rows=None)
        else:
            preview = int(args.preview_rows)
            if preview < 0:
                preview = 0
            _print_csv_preview(args.output_csv, max_rows=preview)
        return

    if args.command == "export-sql":
        stats = export_csv_to_db(
            args.cleaned_csv,
            args.database,
            table_name=args.table_name,
            truncate=args.truncate,
        )
        print("\nExport Summary")
        print(f"Rows in CSV: {stats.get('rows_in_csv')}")
        print(f"Rows inserted: {stats.get('rows_inserted')}")
        print(f"Table: {stats.get('table_name')}")
        if stats.get("truncate"):
            print("Truncate: true")
        if stats.get("postgres"):
            print("Database: PostgreSQL")
        else:
            print("Database: SQLite")
        col_types = stats.get("column_types") or {}
        if col_types:
            print("Inferred column types:")
            for col, sql_t in col_types.items():
                print(f"  - {col}: {sql_t}")
        return

    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()

