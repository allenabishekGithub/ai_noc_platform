"""Utility helpers for timestamped event logging with elapsed timings."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import logging
from time import perf_counter
from typing import Any, Iterator


def utc_timestamp() -> str:
    """Return an ISO-8601 UTC timestamp with millisecond precision."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _kv_pairs(fields: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in sorted(fields):
        value = fields[key]
        parts.append(f"{key}={value!r}")
    return " ".join(parts)


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    """Emit a single event log line with an explicit UTC timestamp."""
    payload = {"event": event, "event_ts": utc_timestamp(), **fields}
    logger.info("EVENT %s", _kv_pairs(payload))


@contextmanager
def timed_event(
    logger: logging.Logger,
    event: str,
    **fields: Any,
) -> Iterator[None]:
    """Log START/END messages for an event and include elapsed milliseconds."""
    start = perf_counter()
    base = {"event": event, **fields}
    logger.info("EVENT_START %s", _kv_pairs({**base, "event_ts": utc_timestamp()}))
    try:
        yield
    except Exception as exc:
        elapsed_ms = (perf_counter() - start) * 1000.0
        logger.exception(
            "EVENT_END %s",
            _kv_pairs(
                {
                    **base,
                    "status": "error",
                    "error": str(exc),
                    "elapsed_ms": round(elapsed_ms, 3),
                    "event_ts": utc_timestamp(),
                }
            ),
        )
        raise
    elapsed_ms = (perf_counter() - start) * 1000.0
    logger.info(
        "EVENT_END %s",
        _kv_pairs(
            {
                **base,
                "status": "ok",
                "elapsed_ms": round(elapsed_ms, 3),
                "event_ts": utc_timestamp(),
            }
        ),
    )
