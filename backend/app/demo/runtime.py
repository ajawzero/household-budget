"""Per-app demo state (demo plan sections 3 to 5).

One `DemoRuntime` per app: `create_app` builds it (a Settings copy only, no file
access) and the lifespan calls `start()`. Per app, not module globals, because tests
build many apps over different folders. Jobs never read it: it says whether the demo
database is ready, not whether a request is in demo.

`available` starts False, so an app whose lifespan never ran (a `TestClient` without
`with`, `cli openapi`) never offers demo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.demo import build
from app.demo.folders import demo_db_file, demo_dir_problem, remove_own_files, runs_problem
from app.demo.settings import demo_settings

log = logging.getLogger("app.demo")

NOT_CHECKED = "The demo folders have not been checked."


@dataclass(frozen=True, slots=True)
class Ready:
    """The demo database the build finished and checked: its path and marker fingerprint."""

    path: Path
    fingerprint: str


class DemoRuntime:
    def __init__(self, real: Settings) -> None:
        self.real = real
        self.db_file = demo_db_file(real)
        # Never made: the backup list in demo stays empty.
        self.settings = demo_settings(
            real, db_file=self.db_file, backup_dir=real.demo_dir / "backups"
        )
        self.available = False
        self.reason: str | None = NOT_CHECKED
        self.ready: Ready | None = None

    def start(self) -> None:
        """Startup: check the demo folder, then delete the demo's own files, then check
        the runs. Any problem turns demo off for this app run; the app still starts."""
        self.ready = None
        try:
            reason = self._check()
        except OSError, RuntimeError:  # unreadable folder, link loop
            reason = "The demo folders could not be checked."
        self.available, self.reason = reason is None, reason
        log.info("demo checked", extra={"available": self.available})

    def _check(self) -> str | None:
        if reason := demo_dir_problem(self.real):
            return reason
        remove_own_files(self.real.demo_dir)
        return runs_problem(self.real) or build.manifest_problem(self.real.demo_runs)

    def ensure_ready(self) -> Ready:
        """Build the demo database if needed (once, under the build lock) and record it.

        Raises `DemoUnavailable` when demo is off, or whatever the build raised.
        """
        if not self.available:
            raise build.DemoUnavailable(self.reason or NOT_CHECKED)
        self.ready = Ready(self.db_file, build.ensure_ready(self.real))
        return self.ready
