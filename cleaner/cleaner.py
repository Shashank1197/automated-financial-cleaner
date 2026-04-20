import csv
import json
import os
import re
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from tqdm import tqdm

from .utils import ColumnMapping, batched, is_nan_like, setup_json_logger


# Common currency symbols (explicit list keeps regex predictable).
CURRENCY_SYMBOL_RE = re.compile(r"([$€£¥₹₩₺₫₽₪₴₦₲₵₱])")

# Detect typical amount column names.
AMOUNT_COL_RE = re.compile(
    r"(?i)\b(amount|amt|value|total|price|expense|income|debit|credit|balance)\b|_amount\b"
)

# Detect typical date column names.
DATE_COL_RE = re.compile(r"(?i)\b(date|txn_date|transaction_date|posting_date)\b")


def _extract_date_token(s: str) -> Optional[str]:
    """
    Extract the most likely date substring from a messy field.
    """
    if not s:
        return None
    text = " ".join(str(s).strip().split())
    if not text:
        return None

    # ISO-ish prefixes: 2026-04-16T12:30:00Z -> 2026-04-16
    iso_m = re.search(r"(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})", text)
    if iso_m:
        return iso_m.group(1)

    # Numeric with separators: 16/04/2026 or 04-16-2026
    numeric_m = re.search(r"(\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})", text)
    if numeric_m:
        return numeric_m.group(1)

    # Month name: 16 Apr 2026 / Apr 16 2026
    month_m = re.search(
        r"(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4}|\b[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{2,4}\b)",
        text,
    )
    if month_m:
        return month_m.group(1)

    return None


def normalize_date(value: Any) -> Tuple[Optional[str], bool]:
    """
    Normalize dates into YYYY-MM-DD.
    Returns (normalized_date, fixed_invalid_date_flag).
    """
    if is_nan_like(value):
        return None, False

    raw = str(value).strip()
    if not raw:
        return None, False

    canonical_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    if canonical_re.match(raw):
        return raw, False

    token = _extract_date_token(raw)
    if not token:
        return None, False

    # 1) Attempt ISO-like numeric formats: 2026/04/16 or 2026.04.16
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$", token)
    if m:
        try:
            y = int(m.group(1))
            mo = int(m.group(2))
            d = int(m.group(3))
            dt = date(y, mo, d)
            return dt.isoformat(), True
        except ValueError:
            return None, False

    # 2) Attempt ambiguous numeric formats: DD/MM/YYYY or MM/DD/YYYY (or with '-' and '.').
    m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})$", token)
    if m:
        p1 = int(m.group(1))
        p2 = int(m.group(2))
        y_raw = int(m.group(3))
        y = y_raw + 2000 if y_raw < 100 else y_raw

        candidates: List[Tuple[int, int]] = []
        if p1 > 12 and p2 <= 12:
            candidates = [(p1, p2)]  # DD/MM
        elif p2 > 12 and p1 <= 12:
            candidates = [(p2, p1)]  # DD/MM via swap
        else:
            candidates = [(p1, p2), (p2, p1)]  # DD/MM then MM/DD

        for d_try, mo_try in candidates:
            try:
                dt = date(y, mo_try, d_try)
                return dt.isoformat(), True
            except ValueError:
                continue
        return None, False

    # 3) Month name formats.
    cleaned = token.replace(",", " ").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    month_formats = [
        "%d %b %Y",
        "%d %B %Y",
        "%b %d %Y",
        "%B %d %Y",
    ]
    for fmt in month_formats:
        try:
            dt = datetime.strptime(cleaned, fmt).date()
            return dt.isoformat(), True
        except ValueError:
            continue

    # 4) Last attempt: ISO-ish fromisoformat (covers YYYY-MM-DD).
    try:
        dt = datetime.fromisoformat(token.replace(".", "-").replace("/", "-")).date()
        return dt.isoformat(), True
    except Exception:
        return None, False


def parse_amount(value: Any) -> Tuple[Optional[float], int]:
    """
    Parse currency amounts into float.
    Returns (amount_float_or_none, currency_symbols_found_count).
    """
    if is_nan_like(value):
        return None, 0

    raw = str(value).strip()
    if not raw:
        return None, 0

    currency_symbols_found = len(CURRENCY_SYMBOL_RE.findall(raw))

    negative = False
    s = raw
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1]
    s = s.strip()

    if s.startswith("-"):
        negative = True
        s = s[1:]

    # Remove currency symbols and common separators.
    s = CURRENCY_SYMBOL_RE.sub("", s)
    s = s.replace(",", "")
    s = s.replace(" ", "")

    # Keep only digits and optional decimal point.
    m = re.search(r"(\d+(\.\d+)?)", s)
    if not m:
        return None, currency_symbols_found

    try:
        amount = float(m.group(1))
        if negative:
            amount = -amount
        return amount, currency_symbols_found
    except ValueError:
        return None, currency_symbols_found


def infer_column_mapping(fieldnames: Sequence[str]) -> ColumnMapping:
    """
    Infer which columns should be treated as dates, amounts, and text.
    """
    dates = [f for f in fieldnames if DATE_COL_RE.search(f)]
    amounts = [f for f in fieldnames if AMOUNT_COL_RE.search(f)]

    if not dates:
        dates = [f for f in fieldnames if re.search(r"(?i)date", f)]
    if not amounts:
        amounts = [f for f in fieldnames if re.search(r"(?i)(amount|amt|value|total|price)", f)]

    return ColumnMapping(
        date_columns=dates or None,
        amount_columns=amounts or None,
        text_columns=None,
        ignore_columns=None,
        output_columns=list(fieldnames),
    )


def _normalize_text_for_dedupe(v: Any) -> str:
    if v is None:
        return ""
    s = str(v)
    s = " ".join(s.split())
    return s.strip().lower()


def _normalize_amount_for_dedupe(v: Any) -> str:
    if v is None:
        return ""
    try:
        f = float(v)
        return f"{round(f, 2):.2f}"
    except Exception:
        return _normalize_text_for_dedupe(v)


def _normalize_date_for_dedupe(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    return s


def dedupe_records(
    records: List[Dict[str, Any]],
    output_columns: Sequence[str],
    date_columns: Sequence[str],
    amount_columns: Sequence[str],
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Remove exact duplicates after normalization.
    Returns (unique_records, duplicates_removed_count).
    """
    seen = set()
    unique_rows: List[Dict[str, Any]] = []
    duplicates_removed = 0

    for r in records:
        key_parts: List[str] = []
        for c in output_columns:
            v = r.get(c)
            if c in date_columns:
                key_parts.append(_normalize_date_for_dedupe(v))
            elif c in amount_columns:
                key_parts.append(_normalize_amount_for_dedupe(v))
            else:
                key_parts.append(_normalize_text_for_dedupe(v))
        key = tuple(key_parts)
        if key in seen:
            duplicates_removed += 1
            continue
        seen.add(key)
        unique_rows.append(r)
    return unique_rows, duplicates_removed


def fuzzy_is_ghost(
    a: Dict[str, Any],
    b: Dict[str, Any],
    *,
    date_column: Optional[str],
    amount_column: Optional[str],
    description_column: Optional[str],
    amount_tol: float,
    date_tol_days: int,
    similarity_threshold: float,
) -> bool:
    if amount_column and a.get(amount_column) is not None and b.get(amount_column) is not None:
        if abs(float(a[amount_column]) - float(b[amount_column])) > amount_tol:
            return False

    if date_column and a.get(date_column) and b.get(date_column):
        da = datetime.strptime(a[date_column], "%Y-%m-%d").date()
        db = datetime.strptime(b[date_column], "%Y-%m-%d").date()
        if abs((da - db).days) > date_tol_days:
            return False

    if description_column:
        da = a.get(description_column) or ""
        db = b.get(description_column) or ""
        ratio = SequenceMatcher(None, str(da).strip().lower(), str(db).strip().lower()).ratio()
        if ratio < similarity_threshold:
            return False

    return True


def remove_fuzzy_ghosts(
    records: List[Dict[str, Any]],
    *,
    date_column: Optional[str],
    amount_column: Optional[str],
    description_column: Optional[str],
    amount_tol: float,
    date_tol_days: int,
    similarity_threshold: float,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Optional near-duplicate removal based on amount+date+description similarity.
    """
    kept: List[Dict[str, Any]] = []
    ghosts_removed = 0

    for r in records:
        matched = False
        for k in kept:
            if fuzzy_is_ghost(
                r,
                k,
                date_column=date_column,
                amount_column=amount_column,
                description_column=description_column,
                amount_tol=amount_tol,
                date_tol_days=date_tol_days,
                similarity_threshold=similarity_threshold,
            ):
                matched = True
                break
        if matched:
            ghosts_removed += 1
            continue
        kept.append(r)

    return kept, ghosts_removed


def _write_csv(path: str, rows: Iterable[Dict[str, Any]], fieldnames: Sequence[str]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            out_row: Dict[str, Any] = {}
            for c in fieldnames:
                v = r.get(c)
                if v is None:
                    out_row[c] = ""
                elif isinstance(v, float):
                    vv = round(v, 2)
                    s = f"{vv:.2f}".rstrip("0").rstrip(".")
                    out_row[c] = s
                else:
                    out_row[c] = v
            writer.writerow(out_row)


def _generate_report(report_path: str, stats: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
    ext = os.path.splitext(report_path)[1].lower()
    if ext == ".json" or ext == "":
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        return

    if ext == ".csv":
        with open(report_path, "w", newline="", encoding="utf-8") as f:
            keys = list(stats.keys())
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerow({k: stats.get(k, "") for k in keys})
        return

    # Default JSON for unknown extensions.
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)


def clean_csv(
    input_csv_path: str,
    output_csv_path: str,
    column_mapping: Optional[ColumnMapping] = None,
    *,
    show_progress: bool = True,
    enable_fuzzy_ghost_detection: bool = False,
    fuzzy_threshold: float = 0.9,
    amount_tol: float = 0.01,
    date_tol_days: int = 0,
    log_dir: str = "logs",
    report_path: Optional[str] = None,
    on_row_cleaned: Optional[Callable[[int, Dict[str, Any], Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """
    Clean messy financial CSV and write a cleaned CSV.

    Returns a stats dictionary.
    """
    logger = setup_json_logger("automated_financial_cleaner", os.path.join(log_dir, "cleaner.log"))

    if not os.path.exists(input_csv_path):
        raise FileNotFoundError(input_csv_path)

    logger.info("clean_start", extra={"input_csv_path": input_csv_path, "output_csv_path": output_csv_path})

    try:
        with open(input_csv_path, "r", newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                raise ValueError("CSV header not found (malformed or empty CSV).")

            fieldnames = [c.strip() for c in reader.fieldnames]
            mapping = column_mapping or infer_column_mapping(fieldnames)
            date_columns = [c for c in (mapping.date_columns or []) if c in fieldnames]
            amount_columns = [c for c in (mapping.amount_columns or []) if c in fieldnames]
            output_columns = list(mapping.output_columns or fieldnames)
            ignore_columns = set(mapping.ignore_columns or [])

            records: List[Dict[str, Any]] = []
            currency_symbols_found = 0
            invalid_dates_fixed = 0

            rows = list(reader)
            for idx, row in enumerate(tqdm(rows, desc="Cleaning rows", unit="row", disable=not show_progress)):
                cleaned: Dict[str, Any] = {}
                date_fixed_this_row = 0
                currency_symbols_found_this_row = 0

                for c in output_columns:
                    if c in ignore_columns:
                        continue

                    raw_v = row.get(c, None)
                    if is_nan_like(raw_v):
                        cleaned[c] = None
                        continue

                    if c in date_columns:
                        norm, fixed = normalize_date(raw_v)
                        if norm is not None and fixed:
                            invalid_dates_fixed += 1
                            date_fixed_this_row += 1
                        cleaned[c] = norm
                        continue

                    if c in amount_columns:
                        amt, found = parse_amount(raw_v)
                        currency_symbols_found += found
                        currency_symbols_found_this_row += found
                        cleaned[c] = amt
                        continue

                    # Default: text.
                    s = str(raw_v).strip()
                    cleaned[c] = s if s != "" else None

                # Skip rows that are entirely empty after cleaning.
                if all(cleaned.get(c) in (None, "") for c in output_columns):
                    continue
                records.append(cleaned)
                if on_row_cleaned:
                    on_row_cleaned(
                        idx,
                        cleaned,
                        {
                            "date_fixed": date_fixed_this_row,
                            "currency_symbols_found": currency_symbols_found_this_row,
                        },
                    )

            unique_records, duplicates_removed = dedupe_records(
                records,
                output_columns=output_columns,
                date_columns=date_columns,
                amount_columns=amount_columns,
            )

            ghosts_removed = 0
            if enable_fuzzy_ghost_detection:
                date_column = date_columns[0] if date_columns else None
                amount_column = amount_columns[0] if amount_columns else None
                description_column = None
                for cand in ("description", "details", "memo", "narration", "details_text"):
                    if cand in fieldnames:
                        description_column = cand
                        break
                unique_records, ghosts_removed = remove_fuzzy_ghosts(
                    unique_records,
                    date_column=date_column,
                    amount_column=amount_column,
                    description_column=description_column,
                    amount_tol=amount_tol,
                    date_tol_days=date_tol_days,
                    similarity_threshold=fuzzy_threshold,
                )

            _write_csv(output_csv_path, unique_records, fieldnames=output_columns)

            stats: Dict[str, Any] = {
                "total_rows_input": len(rows),
                "total_rows_cleaned": len(unique_records),
                "duplicates_removed": duplicates_removed,
                "ghost_transactions_removed": ghosts_removed,
                "currency_symbols_found": currency_symbols_found,
                "invalid_dates_fixed": invalid_dates_fixed,
                "date_columns": date_columns,
                "amount_columns": amount_columns,
                "output_columns": output_columns,
                "input_csv_path": input_csv_path,
                "output_csv_path": output_csv_path,
            }
            logger.info("clean_complete", extra=stats)

            if report_path:
                _generate_report(report_path, stats)

            return stats
    except csv.Error as e:
        logger.exception("clean_csv_error", extra={"error": str(e)})
        raise ValueError(f"Malformed CSV: {e}") from e


def generate_report(stats: Dict[str, Any], report_path: str) -> None:
    """
    Public wrapper to generate report summary.
    """
    _generate_report(report_path, stats)

