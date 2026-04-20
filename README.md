# Automated Financial Data Cleaner

## Overview
This project cleans messy financial CSVs and exports the cleaned data into SQL (SQLite or PostgreSQL).

Cleaning includes:
- Date normalization to `YYYY-MM-DD`
- Currency symbol removal + numeric amount conversion
- Duplicate removal (ghost transactions)
- NaN/empty handling

Exports include:
- Auto-create table from the cleaned CSV schema
- Batch inserts
- Optional truncate for existing tables

All actions are logged as structured JSON to `logs/cleaner.log`.

## Install
```bash
cd automated-financial-cleaner
python -m venv .venv
```

Windows:
```bash
.venv\Scripts\activate
pip install -r requirements.txt
```

## Run CLI
Clean CSV:
```bash
python cli.py clean sample_data/messy_transactions.csv sample_data/cleaned_transactions.csv
```

Export to SQLite:
```bash
python cli.py export-sql sample_data/cleaned_transactions.csv database.db --truncate
```

## Test
```bash
pytest
```

## Sample Data
See:
- `sample_data/messy_transactions.csv`
- `sample_data/cleaned_transactions.csv`

### Example input (messy)
```csv
date,description,amount
16/04/2026,Coffee Shop,"₹1,200.00"
2026-04-16,coffee shop,1200
```

### Example output (cleaned)
```csv
date,description,amount
2026-04-16,Coffee Shop,1200
2026-04-17,Groceries,45.5
```

## Optional Features

### Fuzzy ghost transaction removal
Enable near-duplicate removal with:
```bash
python cli.py clean sample_data/messy_transactions.csv out.csv --enable-fuzzy-ghosts
```

### Generate a cleaning report
Create a JSON (or CSV) report with:
```bash
python cli.py clean sample_data/messy_transactions.csv out.csv --report report.json
```

## Terminal Output (what you asked for)
After `clean`, the CLI prints a `Clean Summary` and a preview of the cleaned CSV.

- Print first N cleaned rows:
```bash
python cli.py clean sample_data/messy_transactions.csv out.csv --preview-rows 10
```
- Print every cleaned row (only for small files):
```bash
python cli.py clean sample_data/messy_transactions.csv out.csv --print-cleaned-all
```
- Verbose mode (prints each cleaned transaction as it is processed):
```bash
python cli.py clean sample_data/messy_transactions.csv out.csv --verbose --preview-rows 0
```

