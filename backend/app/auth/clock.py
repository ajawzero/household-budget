"""The auth clock. Tests replace `now` to move time."""

from __future__ import annotations

from datetime import UTC, datetime


def now() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    """UTC ISO at seconds precision. Strings in this form sort in time order."""
    return dt.astimezone(UTC).isoformat(timespec="seconds")
