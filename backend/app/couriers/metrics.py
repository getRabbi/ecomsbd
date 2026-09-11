"""Courier integration metrics.

Brief section 48. ``docs/OBSERVABILITY.md`` is honest that this deployment has
no Prometheus scraper: the *data* exists — every metric there names the table it
comes from — but nothing pulls it. This module does not pretend otherwise. It
does two real things:

*   emits a structured log line per observation, which the existing JSON log
    pipeline already carries and which is what an operator greps today;
*   keeps an in-process counter so the admin ops endpoint can show a live
    snapshot and the test suite can assert that a code path actually fired.

The rule that matters is the one ``docs/OBSERVABILITY.md`` opens with: **no
customer PII in a label.** A metric labelled by phone number is a phone-number
dataset with a graph on top. The label allowlist below is enforced, not
documented — :func:`record_metric` drops anything not in it rather than trusting
each call site to remember.
"""

from __future__ import annotations

import threading
from collections import Counter
from enum import StrEnum
from typing import Any

from app.core.logging import get_logger

__all__ = [
    "ALLOWED_LABELS",
    "CourierMetric",
    "metrics_snapshot",
    "observe_latency",
    "record_metric",
    "reset_metrics",
]

log = get_logger("app.couriers.metrics")


class CourierMetric(StrEnum):
    """The metric names from brief section 48, plus the ones they imply."""

    CREATE_SUCCESS = "steadfast_create_success"
    CREATE_AMBIGUOUS = "steadfast_create_ambiguous"
    CREATE_FAILED = "steadfast_create_failed"
    #: A second booking that ecomsbd refused to send. Every increment here is a
    #: duplicate parcel that did not happen.
    DUPLICATE_PREVENTED = "steadfast_duplicate_prevented"
    UNKNOWN_RECOVERED = "steadfast_unknown_recovered"
    UNKNOWN_UNRESOLVED = "steadfast_unknown_unresolved"
    STATUS_SYNC_LAG = "steadfast_status_sync_lag"
    STATUS_SYNC_SUCCESS = "steadfast_status_sync_success"
    #: A delivery status the supplied documentation does not list. Any
    #: non-zero value means the provider added a state and the mapping table
    #: needs a decision.
    STATUS_UNKNOWN_VALUE = "steadfast_status_unknown_value"
    PAYMENT_SYNC_COUNT = "steadfast_payment_sync_count"
    PAYMENT_SYNC_ERROR = "steadfast_payment_sync_error"
    UNMATCHED_PAYMENT_AMOUNT = "steadfast_unmatched_payment_amount"
    AUTH_FAILURE = "steadfast_auth_failure"
    LATENCY = "steadfast_latency"
    RETURN_REQUESTED = "steadfast_return_requested"
    RETURN_DUPLICATE_PREVENTED = "steadfast_return_duplicate_prevented"
    CREDENTIAL_CONNECTED = "steadfast_credential_connected"
    CREDENTIAL_DISCONNECTED = "steadfast_credential_disconnected"
    WEBHOOK_RECEIVED = "steadfast_webhook_received"
    WEBHOOK_NOT_CONFIGURED = "steadfast_webhook_not_configured"
    WEBHOOK_DUPLICATE = "steadfast_webhook_duplicate"


#: Labels a metric may carry. Everything else is dropped.
#:
#: ``tenant_id`` is absent on purpose even though it is an opaque UUID: these
#: counters are process-global and a per-tenant cardinality explosion on a
#: single VPS buys nothing that the audit log and the database do not already
#: answer better.
ALLOWED_LABELS: frozenset[str] = frozenset(
    {"provider", "result", "reason", "kind", "capability", "source", "state"}
)

_lock = threading.Lock()
_counters: Counter[str] = Counter()
_sums: Counter[str] = Counter()


def _key(metric: str, labels: dict[str, str]) -> str:
    if not labels:
        return metric
    rendered = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
    return f"{metric}{{{rendered}}}"


def _safe_labels(raw: dict[str, Any]) -> dict[str, str]:
    """Keep only allowlisted labels, stringified and length-bounded.

    Dropping rather than raising: a metric call is never important enough to
    fail the operation it is measuring, and an operator noticing a missing
    label is a better outcome than a booking failing because someone passed a
    phone number to a counter.
    """
    labels: dict[str, str] = {}
    for name, value in raw.items():
        if name not in ALLOWED_LABELS or value is None:
            continue
        labels[name] = str(value)[:60]
    return labels


def record_metric(metric: CourierMetric | str, *, value: int = 1, **labels: Any) -> None:
    """Count one observation."""
    safe = _safe_labels(labels)
    key = _key(str(metric), safe)
    with _lock:
        _counters[key] += value
    log.info(
        "courier metric",
        extra={"metric": str(metric), "metric_value": value, **safe},
    )


def observe_latency(metric: CourierMetric | str, *, milliseconds: int, **labels: Any) -> None:
    """Record a duration.

    Kept as a count plus a sum rather than as a histogram: without a scraper a
    histogram's buckets would be a guess, and count-and-sum still yields a mean
    and is exactly what a real backend would ingest later.
    """
    safe = _safe_labels(labels)
    key = _key(str(metric), safe)
    with _lock:
        _counters[f"{key}#count"] += 1
        _sums[f"{key}#sum_ms"] += milliseconds
    log.info(
        "courier latency",
        extra={"metric": str(metric), "duration_ms": milliseconds, **safe},
    )


def metrics_snapshot() -> dict[str, int]:
    """Current in-process counters, for the admin ops screen and for tests.

    Process-local and reset on restart. That is a real limitation and the
    reason this is a diagnostic aid rather than a source of truth — anything
    that must survive a deploy is in a table.
    """
    with _lock:
        return {**_counters, **_sums}


def reset_metrics() -> None:
    """Clear the counters. Tests only."""
    with _lock:
        _counters.clear()
        _sums.clear()
