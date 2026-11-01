"""Delete Ask threads older than the retention setting (PLAN 3.6, 7.8).

A thread goes, with its messages, once it started more than
`thread_retention_days` ago (90 by default), even if it is still in use, so no
question or answer is kept longer than that. Usage rows stay (metadata only)
and lose their thread link. Runs once a day. Backups still hold deleted threads
until they age out (nightly copies: 14 days).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.ai import settings as ai_settings
from app.db.connect import connect, write_transaction

JOB_NAME = "ai_cleanup"


def delete_old_threads(db_path: str | Path, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    conn = connect(db_path)
    try:
        days = ai_settings.load(conn).thread_retention_days
        cutoff = ai_settings.iso(now - timedelta(days=days))
        with write_transaction(conn):
            cur = conn.execute("DELETE FROM ai_threads WHERE created_at < ?", (cutoff,))
            return cur.rowcount
    finally:
        conn.close()


def cleanup_job_run(db_path: str | Path) -> dict | None:
    deleted = delete_old_threads(db_path)
    return {"threads_deleted": deleted} if deleted else None
