"""Structured logging.

Master spec section 47: every log line carries ``trace_id``, ``tenant_id``,
provider, operation, duration and result, and carries no secrets or PII.

The context fields are injected by a ``logging.Filter`` reading the ambient
:mod:`app.core.context`, and every message plus every extra field passes through
:mod:`app.core.redaction` on the way out. That keeps redaction unavoidable
rather than a per-call-site discipline.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.core.context import current_context
from app.core.redaction import redact_value

__all__ = ["bind", "configure_logging", "get_logger", "log_duration"]

#: Attributes present on every LogRecord; anything else is a caller extra.
_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()) | {
    "message",
    "asctime",
    "taskName",
}


class ContextFilter(logging.Filter):
    """Attach ambient trace/tenant identifiers to each record."""

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in current_context().log_fields().items():
            if not hasattr(record, key):
                setattr(record, key, value)
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with redaction applied to every field."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": redact_value(record.getMessage()),
        }

        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            payload[key] = redact_value(value, key=key)

        if record.exc_info:
            payload["exception"] = redact_value(self.formatException(record.exc_info))

        return json.dumps(payload, default=str, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Human-readable local development output. Redaction still applies."""

    def format(self, record: logging.LogRecord) -> str:
        base = (
            f"{self.formatTime(record, '%H:%M:%S')} "
            f"{record.levelname:<8} {record.name} :: {redact_value(record.getMessage())}"
        )
        extras = {
            key: redact_value(value, key=key)
            for key, value in record.__dict__.items()
            if key not in _RESERVED and not key.startswith("_")
        }
        extras.pop("trace_id", None)
        trace = getattr(record, "trace_id", None)
        if trace and trace != "-":
            base = f"{base} [trace={trace}]"
        if extras:
            base = f"{base} {json.dumps(extras, default=str, ensure_ascii=False)}"
        if record.exc_info:
            base = f"{base}\n{redact_value(self.formatException(record.exc_info))}"
        return base


def configure_logging(*, level: str = "INFO", json_output: bool = True) -> None:
    """Install the root handler. Idempotent; safe to call from app and worker."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_output else ConsoleFormatter())
    handler.addFilter(ContextFilter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    # Uvicorn installs its own colourised handlers; route them through ours so
    # access lines are redacted and carry trace ids too.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True

    # SQLAlchemy echo is controlled by the engine, not by log level.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def bind(**fields: Any) -> dict[str, Any]:
    """Build an ``extra=`` mapping. Values are redacted by the formatter."""
    return fields


@contextmanager
def log_duration(
    logger: logging.Logger, operation: str, /, level: int = logging.INFO, **fields: Any
) -> Iterator[dict[str, Any]]:
    """Time an operation and log its outcome exactly once.

    Yields a mutable mapping so the body can add result fields before the line
    is emitted::

        with log_duration(log, "courier.book", provider="steadfast") as span:
            span["result"] = "BOOKED"
    """
    span: dict[str, Any] = dict(fields)
    started = time.perf_counter()
    try:
        yield span
    except Exception as exc:
        span.setdefault("result", "error")
        span["error_type"] = type(exc).__name__
        logger.exception(
            "%s failed",
            operation,
            extra={
                **span,
                "operation": operation,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        raise
    else:
        span.setdefault("result", "ok")
        logger.log(
            level,
            "%s",
            operation,
            extra={
                **span,
                "operation": operation,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
