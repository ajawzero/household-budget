"""The demo folder and the sample runs folder: where they may be, and the demo's own files.

Startup checks the demo folder before anything is deleted (demo plan section 3):
- the folder itself is not a link (a link higher up is fine: on the Mac `/tmp` and
  `/var` are links to `/private/...`);
- it is not `/`, a home folder, `/app` or `/data`;
- it does not equal, hold or sit inside the real database, import, backups, private
  rules, static or sample runs folder (resolved paths, both ways).
Only then are the demo's own file names deleted, never a whole folder.

The runs folder must be a folder of runs (not a run folder: the importer's retry would
then search its parent) and must not overlap the real database, import or backups folder.

Every problem is a short, value-free reason: no path ever goes into it.
"""

from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.importer.runner import run_folders

DEMO_DB = "demo.db"
BUILD_DB = "demo-build.db"
# The only names startup and the build ever delete. Neither matches the backup names
# (budget-YYYY-MM-DD.db, pre-migrate-NNNN.db), so backup listing and pruning never see them.
OWN_NAMES = (
    "demo.db",
    "demo.db-journal",
    "demo.db-wal",
    "demo.db-shm",
    "demo-build.db",
    "demo-build.db-journal",
    "demo-build.db-wal",
    "demo-build.db-shm",
)
_SYSTEM_FOLDERS = (Path("/"), Path("/app"), Path("/data"))


def demo_db_file(settings: Settings) -> Path:
    return settings.demo_dir / DEMO_DB


def _overlaps(a: Path, b: Path) -> bool:
    """`a` equals, holds or sits inside `b` (both resolved)."""
    a, b = a.resolve(), b.resolve()
    return a.is_relative_to(b) or b.is_relative_to(a)


def _home() -> list[Path]:
    try:
        return [Path.home()]
    except RuntimeError:  # no home folder for this user
        return []


def _real_folders(settings: Settings) -> list[tuple[str, Path]]:
    from app.main import DEFAULT_STATIC  # main builds the app, so import it late

    folders = [
        ("database", settings.db_path.parent),
        ("import", settings.import_dir),
        ("backups", settings.backup_dir),
        ("static", settings.static_dir or DEFAULT_STATIC),
        ("sample runs", settings.demo_runs),
    ]
    if settings.private_rules is not None:
        folders.append(("private rules", settings.private_rules.parent))
    return folders


def demo_dir_problem(settings: Settings) -> str | None:
    """Why the demo folder is not safe to use, or None."""
    folder = settings.demo_dir
    if folder.is_symlink():
        return "The demo folder is a link."
    if folder.resolve() in {p.resolve() for p in (*_SYSTEM_FOLDERS, *_home())}:
        return "The demo folder is a system or home folder."
    for name, real in _real_folders(settings):
        if _overlaps(folder, real):
            return f"The demo folder overlaps the {name} folder."
    return None


def runs_problem(settings: Settings) -> str | None:
    """Why the sample runs folder cannot be used, or None. The manifests are checked
    separately (`build.manifest_problem`)."""
    runs = settings.demo_runs
    if runs.is_symlink() or not runs.is_dir():
        return "The sample runs folder is missing."
    for name, real in (
        ("database", settings.db_path.parent),
        ("import", settings.import_dir),
        ("backups", settings.backup_dir),
    ):
        if _overlaps(runs, real):
            return f"The sample runs folder overlaps the {name} folder."
    if run_folders(runs) == [runs]:
        return "The sample runs folder is a run folder, not a folder of runs."
    return None


def remove_own_files(folder: Path, names: tuple[str, ...] = OWN_NAMES) -> None:
    """Delete the demo's own files in `folder`. Call only after `demo_dir_problem` is None."""
    for name in names:
        (folder / name).unlink(missing_ok=True)


def make_demo_dir(folder: Path) -> None:
    """Create the demo folder mode 700, or take group and other access off it."""
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if folder.stat().st_mode & 0o077:
        folder.chmod(0o700)
