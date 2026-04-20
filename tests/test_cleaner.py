import csv

import pytest

from cleaner.cleaner import clean_csv, normalize_date, parse_amount


def test_date_formatting_edge_cases():
    assert normalize_date("2026/04/16")[0] == "2026-04-16"
    assert normalize_date("16-04-2026")[0] == "2026-04-16"
    assert normalize_date("2026-04-16T12:30:00Z")[0] == "2026-04-16"
    # Impossible calendar date should not be normalized.
    assert normalize_date("31/02/2026")[0] is None


def test_currency_removal():
    amt, found = parse_amount("₹1,234.50")
    assert found == 1
    assert amt == pytest.approx(1234.5)

    amt2, found2 = parse_amount("(€5,000.00)")
    assert found2 == 1
    assert amt2 == pytest.approx(-5000.0)


def test_duplicate_removal(tmp_path):
    inp = tmp_path / "messy.csv"
    out = tmp_path / "cleaned.csv"
    inp.write_text(
        "date,description,amount\n"
        '16/04/2026,Coffee Shop,"₹1,200.00"\n'
        "2026-04-16,coffee shop,1200\n",
        encoding="utf-8",
    )

    stats = clean_csv(str(inp), str(out), show_progress=False)
    assert stats["duplicates_removed"] == 1
    assert stats["total_rows_cleaned"] == 1

    with out.open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 1
    assert rows[0]["date"] == "2026-04-16"
    assert rows[0]["description"] == "Coffee Shop"
    assert rows[0]["amount"] == "1200"


def test_empty_and_malformed_csv(tmp_path):
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ValueError):
        clean_csv(str(empty), str(tmp_path / "out.csv"), show_progress=False)

    header_only = tmp_path / "header_only.csv"
    header_only.write_text("date,description,amount\n", encoding="utf-8")
    out2 = tmp_path / "out2.csv"
    stats = clean_csv(str(header_only), str(out2), show_progress=False)
    assert stats["total_rows_cleaned"] == 0
    with out2.open("r", encoding="utf-8") as f:
        assert len(list(csv.DictReader(f))) == 0

    malformed = tmp_path / "malformed.csv"
    # Missing closing quote.
    malformed.write_text('date,description,amount\n"2026-04-16,Coffee Shop,10\n', encoding="utf-8")
    out3 = tmp_path / "out3.csv"
    # Some malformed cases can still be parsed by Python's csv module; ensure we handle it gracefully.
    stats3 = clean_csv(str(malformed), str(out3), show_progress=False)
    assert "total_rows_cleaned" in stats3
    assert out3.exists()

