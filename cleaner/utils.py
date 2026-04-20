import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional


class JsonLogFormatter(logging.Formatter):
    """
    JSON log lines for structured logging (one JSON object per line).
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # If we logged with extra={...}, include it.
        # logging passes unknown kwargs as record attributes.
        for key, value in record.__dict__.items():
            if key in {
                "name",
                "msg",
                "args",
                "levelname",
                "levelno",
                "pathname",
                "filename",
                "module",
                "exc_info",
                "exc_text",
                "stack_info",
                "lineno",
                "funcName",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
                "message",
            }:
                continue
            if key.startswith("_"):
                continue
            payload[key] = value

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False)


def setup_json_logger(name: str, log_path: str) -> logging.Logger:
    """
    Configure a JSON logger writing to `log_path`.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # Prevent duplicate handlers if called multiple times.
    if any(isinstance(h, logging.FileHandler) for h in logger.handlers):
        return logger

    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(JsonLogFormatter())

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.WARNING)
    stream_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    logger.propagate = False
    return logger


@dataclass(frozen=True)
class ColumnMapping:
    """
    Column mapping controls which input columns are treated as dates/amounts.
    """

    date_columns: Optional[list[str]] = None
    amount_columns: Optional[list[str]] = None
    text_columns: Optional[list[str]] = None
    ignore_columns: Optional[list[str]] = None
    output_columns: Optional[list[str]] = None


def is_nan_like(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and value != value:  # NaN
        return True
    s = str(value).strip()
    return s == "" or s.lower() in {"nan", "na", "null", "none"}


def batched(iterable: Iterable[Any], batch_size: int):
    batch = []
    for item in iterable:
        batch.append(item)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch

