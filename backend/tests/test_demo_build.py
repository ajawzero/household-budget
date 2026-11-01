"""The demo database: the folder checks, the startup cleanup, the build, the ready
marker, the build lock and the demo writer (demo plan section 3)."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import os
import sqlite3
import stat
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest
from api_helpers import import_samples
from bad_cases import copy_samples, read_json, write_json
from conftest import PRIVATE_RULES_EXAMPLE, SAMPLES

from app.auth.passwords import verify_password
from app.auth.sessions import token_hash
from app.config import Settings
from app.db.connect import connect, read_connection
from app.db.migrate import list_migrations, migrate
from app.demo import build, folders, runtime, store
from app.demo.build import DemoUnavailable
from app.demo.context import DemoGuardError, guarded, pinned
from app.demo.folders import OWN_NAMES, demo_dir_problem, runs_problem
from app.demo.runtime import DemoRuntime
from app.importer.runner import import_path
from app.jobs.backup import NIGHTLY, PRE_MIGRATE
from app.main import DEFAULT_STATIC
from app.services.fingerprint import fingerprint


def _started(settings: Settings, **update: object) -> DemoRuntime:
    rt = DemoRuntime(settings.model_copy(update=update) if update else settings)
    rt.start()
    return rt


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _listing(folder: Path) -> dict[str, tuple[int, str]]:
    """Every file under `folder`: size and sha256."""
    if not folder.exists():
        return {}
    return {
        str(p.relative_to(folder)): (p.stat().st_size, _sha(p))
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    }


def _real_listings(s: Settings) -> list[dict[str, tuple[int, str]]]:
    return [_listing(f) for f in (s.db_path.parent, s.import_dir, s.backup_dir)]


def _ro(db_file: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{db_file.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _journal_mode(db_file: Path) -> str:
    conn = _ro(db_file)
    try:
        return conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()


def _side_files(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir() if p.name.endswith(("-journal", "-wal", "-shm")))


def _count_builds(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    real_build = build.build

    def counting(real: Settings) -> None:
        calls.append(1)
        real_build(real)

    monkeypatch.setattr(build, "build", counting)
    return calls


def _plant(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "demo.db").write_text("planted")
    (folder / "keep.txt").write_text("keep")


def _no_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("cleanup ran on a refused folder")

    monkeypatch.setattr(runtime, "remove_own_files", refuse)


# --------------------------------------------------------------------------
# Names and the startup cleanup
# --------------------------------------------------------------------------


def test_own_names_never_look_like_backups() -> None:
    for name in OWN_NAMES:
        assert not NIGHTLY.match(name) and not PRE_MIGRATE.match(name)
        assert not fnmatch.fnmatch(name, "pre-migrate-*.db")  # migrate's prune glob
        assert name.startswith((folders.DEMO_DB, folders.BUILD_DB))


def test_startup_deletes_only_the_demo_names(settings: Settings) -> None:
    folder = settings.demo_dir
    (folder / "sub").mkdir(parents=True)
    for name in OWN_NAMES:
        (folder / name).write_text("old")
    for name in ("keep.txt", "budget-2026-10-01.db", "demo.db.bak", "sub/demo.db"):
        (folder / name).write_text("keep")
    rt = _started(settings)
    assert rt.available and rt.reason is None and rt.ready is None
    assert sorted(p.name for p in folder.iterdir()) == [
        "budget-2026-10-01.db",
        "demo.db.bak",
        "keep.txt",
        "sub",
    ]
    assert (folder / "sub" / "demo.db").read_text() == "keep"


def test_runtime_is_off_until_started(settings: Settings) -> None:
    rt = DemoRuntime(settings)
    assert rt.available is False and rt.ready is None
    assert rt.settings.db_path == settings.demo_dir / "demo.db" == rt.db_file
    with pytest.raises(DemoUnavailable):
        rt.ensure_ready()
    assert not settings.demo_dir.exists()


# --------------------------------------------------------------------------
# Folder checks: a refused demo folder turns demo off and deletes nothing
# --------------------------------------------------------------------------

# (demo folder, real folder) for each way the two can overlap.
RELATIONS: dict[str, Callable[[Path], tuple[Path, Path]]] = {
    "equal to": lambda t: (t / "x", t / "x"),
    "inside": lambda t: (t / "x" / "demo", t / "x"),
    "holding": lambda t: (t / "x", t / "x" / "real"),
}
# The Settings update that makes `folder` the real folder of each kind.
REAL_FOLDERS: dict[str, Callable[[Path], dict[str, object]]] = {
    "database": lambda f: {"db_path": f / "budget.db"},
    "import": lambda f: {"import_dir": f},
    "backups": lambda f: {"backup_dir": f},
    "private rules": lambda f: {"private_rules": f / "private_rules.json"},
    "static": lambda f: {"static_dir": f},
    "sample runs": lambda f: {"demo_runs": f},
}


@pytest.mark.parametrize("relation", RELATIONS)
@pytest.mark.parametrize("kind", REAL_FOLDERS)
def test_demo_folder_overlapping_a_real_folder_is_refused(
    settings: Settings, tmp_path: Path, relation: str, kind: str
) -> None:
    demo_dir, real = RELATIONS[relation](tmp_path)
    _plant(demo_dir)
    rt = _started(settings, demo_dir=demo_dir, **REAL_FOLDERS[kind](real))
    assert rt.available is False
    assert rt.reason == f"The demo folder overlaps the {kind} folder."
    assert (demo_dir / "demo.db").read_text() == "planted"
    assert (demo_dir / "keep.txt").exists()


def test_demo_folder_holding_every_real_folder_is_refused(
    settings: Settings, tmp_path: Path
) -> None:
    (tmp_path / "demo.db").write_text("planted")
    rt = _started(settings, demo_dir=tmp_path)  # tmp_path holds db/, import/ and backups/
    assert rt.available is False and rt.reason is not None
    assert (tmp_path / "demo.db").read_text() == "planted"


def test_demo_folder_in_the_default_static_folder_is_refused(settings: Settings) -> None:
    assert settings.static_dir is None
    problem = demo_dir_problem(settings.model_copy(update={"demo_dir": DEFAULT_STATIC / "demo"}))
    assert problem == "The demo folder overlaps the static folder."


@pytest.mark.parametrize("folder", ["/", "/app", "/data", "home"])
def test_system_and_home_folders_are_refused(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, folder: str
) -> None:
    _no_cleanup(monkeypatch)
    demo_dir = Path.home() if folder == "home" else Path(folder)
    rt = _started(settings, demo_dir=demo_dir)
    assert rt.available is False
    assert rt.reason == "The demo folder is a system or home folder."


def test_a_linked_demo_folder_is_refused(settings: Settings, tmp_path: Path) -> None:
    target = tmp_path / "elsewhere"
    _plant(target)
    link = tmp_path / "demo-link"
    link.symlink_to(target, target_is_directory=True)
    rt = _started(settings, demo_dir=link)
    assert rt.available is False and rt.reason == "The demo folder is a link."
    assert (target / "demo.db").read_text() == "planted"
    assert (target / "keep.txt").exists()


def test_a_link_higher_up_is_fine(settings: Settings, tmp_path: Path) -> None:
    """On the Mac /tmp and /var are links: only the demo folder itself must not be one."""
    (tmp_path / "real-parent").mkdir()
    (tmp_path / "parent-link").symlink_to(tmp_path / "real-parent", target_is_directory=True)
    rt = _started(settings, demo_dir=tmp_path / "parent-link" / "demo")
    assert rt.available and rt.reason is None


def test_no_home_folder_is_not_an_error(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_home() -> Path:
        raise RuntimeError("no home")

    monkeypatch.setattr(Path, "home", staticmethod(no_home))
    assert demo_dir_problem(settings) is None


# --------------------------------------------------------------------------
# The sample runs folder and its manifests
# --------------------------------------------------------------------------


def _bad_skill(data: Path, run: str = "2026-10-12") -> None:
    path = data / run / "manifest.json"
    manifest = read_json(path)
    manifest["generator"]["skill"] = "my-exporter"
    write_json(path, manifest)


@pytest.mark.parametrize(
    ("runs", "reason"),
    [
        (lambda s, t: t / "missing", "The sample runs folder is missing."),
        (lambda s, t: s.import_dir, "The sample runs folder overlaps the import folder."),
        (lambda s, t: s.import_dir / "runs", "The sample runs folder overlaps the import folder."),
        (lambda s, t: t, "The sample runs folder overlaps the database folder."),
        (lambda s, t: s.db_path.parent, "The sample runs folder overlaps the database folder."),
        (lambda s, t: s.backup_dir, "The sample runs folder overlaps the backups folder."),
        (
            lambda s, t: SAMPLES / "2026-10-06",
            "The sample runs folder is a run folder, not a folder of runs.",
        ),
    ],
)
def test_unusable_runs_folder_is_refused(
    settings: Settings,
    tmp_path: Path,
    runs: Callable[[Settings, Path], Path],
    reason: str,
) -> None:
    folder = runs(settings, tmp_path)
    if folder != tmp_path / "missing":
        folder.mkdir(parents=True, exist_ok=True)
    assert runs_problem(settings.model_copy(update={"demo_runs": folder})) == reason


def test_a_missing_runs_folder_turns_demo_off(settings: Settings, tmp_path: Path) -> None:
    """The image default (/samples/data) until Demo-c ships the runs."""
    rt = _started(settings, demo_runs=tmp_path / "missing")
    assert rt.available is False and rt.reason == "The sample runs folder is missing."
    with pytest.raises(DemoUnavailable):
        rt.ensure_ready()
    assert not settings.demo_dir.exists()


def test_a_linked_runs_folder_is_refused(settings: Settings, tmp_path: Path) -> None:
    link = tmp_path / "runs-link"
    link.symlink_to(SAMPLES, target_is_directory=True)
    assert runs_problem(settings.model_copy(update={"demo_runs": link})) is not None


def test_an_empty_runs_folder_turns_demo_off(settings: Settings, tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    rt = _started(settings, demo_runs=tmp_path / "empty")
    assert rt.available is False and rt.reason == "No sample runs found."
    with pytest.raises(DemoUnavailable):
        build.build(rt.real)


def test_a_run_not_made_by_make_samples_is_refused(settings: Settings, tmp_path: Path) -> None:
    data = copy_samples(tmp_path / "copy")
    _bad_skill(data)
    rt = _started(settings, demo_runs=data)
    assert rt.available is False
    assert rt.reason == "A sample run was not made by make_samples."
    with pytest.raises(DemoUnavailable, match="make_samples"):
        build.build(rt.real)
    assert not settings.demo_dir.exists()


def test_a_skipped_folder_with_a_real_manifest_is_refused(
    settings: Settings, tmp_path: Path
) -> None:
    """As check.sh does: every */manifest.json, even under names the import skips."""
    data = copy_samples(tmp_path / "copy")
    (data / "_old").mkdir()
    write_json(data / "_old" / "manifest.json", {"generator": {"skill": "my-exporter"}})
    assert build.manifest_problem(data) == "A sample run was not made by make_samples."


def test_the_build_checks_the_manifests_again(settings: Settings, tmp_path: Path) -> None:
    data = copy_samples(tmp_path / "copy")
    rt = _started(settings, demo_runs=data)
    assert rt.available
    _bad_skill(data, "2026-10-06")
    with pytest.raises(DemoUnavailable, match="make_samples"):
        rt.ensure_ready()
    assert rt.ready is None
    assert not (settings.demo_dir / "demo.db").exists()


# --------------------------------------------------------------------------
# The build
# --------------------------------------------------------------------------


def test_sample_rules_equal_the_example_file() -> None:
    example = json.loads(PRIVATE_RULES_EXAMPLE.read_text(encoding="utf-8"))
    assert set(example) == {"personal_subscriptions", "employer_terms"}
    assert list(build.SAMPLE_RULES.subscriptions) == example["personal_subscriptions"]
    assert list(build.SAMPLE_RULES.employer_terms) == example["employer_terms"]


def test_fingerprint_matches_a_sample_import(use_private_rules: Settings) -> None:
    migrate(settings=use_private_rules)
    import_samples(use_private_rules)
    with read_connection(use_private_rules.db_path) as conn:
        want = fingerprint(conn)
    ready = _started(use_private_rules).ensure_ready()
    assert ready.fingerprint == want
    conn = connect(ready.path, readonly=True)
    try:
        assert fingerprint(conn) == want
    finally:
        conn.close()


def test_build_contents(settings: Settings, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.demo")
    rt = _started(settings)
    ready = rt.ensure_ready()
    assert rt.ready == ready and ready.path == rt.db_file
    assert sorted(p.name for p in settings.demo_dir.iterdir()) == ["demo.db"]
    assert stat.S_IMODE(settings.demo_dir.stat().st_mode) == 0o700
    assert _journal_mode(rt.db_file) == "delete"
    conn = _ro(rt.db_file)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == list_migrations()[-1].version
        marker = conn.execute("SELECT value_json FROM settings WHERE key = 'demo_build'")
        assert json.loads(marker.fetchone()[0]) == {"fingerprint": ready.fingerprint}
        users = [dict(r) for r in conn.execute("SELECT * FROM users")]
        labels = [r[0] for r in conn.execute("SELECT label FROM index_series")]
        runs = conn.execute("SELECT run_date, started_at, finished_at FROM import_runs").fetchall()
        events = [r[0] for r in conn.execute("SELECT at FROM events")]
        ai_rows = conn.execute("SELECT count(*) FROM settings WHERE key != 'demo_build'")
        assert ai_rows.fetchone()[0] == 0  # no AI settings row: AI off
    finally:
        conn.close()
    assert len(users) == 1
    user = users[0]
    assert (user["id"], user["username"], user["display_name"], user["role"]) == (
        900,
        "sample",
        "Alex",
        "member",
    )
    assert user["password_hash"] == "!" and user["active"] == 1
    assert not verify_password(user["password_hash"], "")
    assert len(labels) == 4
    for label in labels:
        for name in ("Austin", "Texas", "West South Central", "TX"):
            assert name not in label
    assert len(runs) == 4
    for run_date, started, finished in runs:
        assert started.startswith(run_date) and finished.startswith(run_date)
    assert events and all(at < "2026-10-20" for at in events)
    stages = [r.stage for r in caplog.records if r.getMessage() == "demo build"]
    assert stages == ["start", "done"]


def test_build_never_touches_the_real_folders(
    samples: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _real_listings(samples)
    seen: list[dict] = []
    real_migrate = build.migrate

    def spy(**kwargs: object) -> int:
        seen.append(kwargs)
        return real_migrate(**kwargs)

    monkeypatch.setattr(build, "migrate", spy)
    _started(samples).ensure_ready()
    assert _real_listings(samples) == before
    assert len(seen) == 1
    assert seen[0]["db_path"] == samples.demo_dir / "demo-build.db"
    assert seen[0]["backup_dir"] == samples.demo_dir / "build-backups"
    assert seen[0]["settings"].db_path == samples.demo_dir / "demo-build.db"
    assert seen[0]["settings"].import_dir == samples.demo_runs
    assert not (samples.demo_dir / "build-backups").exists()
    assert sorted(p.name for p in samples.demo_dir.iterdir()) == ["demo.db"]


def test_left_out_settings_raise_under_the_build_guard(samples: Settings) -> None:
    """migrate() and import_path() fall back to the real Settings when an argument is
    left out. Under the build guard that is an error, and the real file stays as it was."""
    real_hash = _sha(samples.db_path)
    before = _real_listings(samples)
    samples.demo_dir.mkdir()
    tmp = samples.demo_dir / "demo-build.db"
    tmp.touch()
    with guarded(tmp, writable=True), pinned():
        with pytest.raises(DemoGuardError):
            migrate()
        with pytest.raises(DemoGuardError):
            import_path(SAMPLES)
    assert _sha(samples.db_path) == real_hash
    assert _real_listings(samples) == before
    assert tmp.stat().st_size == 0


def test_a_failed_build_leaves_no_files(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: object, **kwargs: object) -> list:
        raise RuntimeError("import broke")

    monkeypatch.setattr(build, "import_path", broken)
    rt = _started(settings)
    with pytest.raises(RuntimeError, match="import broke"):
        rt.ensure_ready()
    assert rt.ready is None
    assert list(settings.demo_dir.iterdir()) == []


def test_build_takes_group_and_other_access_off_the_folder(settings: Settings) -> None:
    settings.demo_dir.mkdir()
    settings.demo_dir.chmod(0o755)
    _started(settings).ensure_ready()
    assert stat.S_IMODE(settings.demo_dir.stat().st_mode) == 0o700


# --------------------------------------------------------------------------
# Ready, and the build lock
# --------------------------------------------------------------------------


def test_an_empty_premade_file_is_not_ready_and_is_rebuilt(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    rt = _started(settings)
    settings.demo_dir.mkdir()
    rt.db_file.touch()  # a stray open left an empty file
    assert not build.is_ready(rt.db_file)
    calls = _count_builds(monkeypatch)
    ready = rt.ensure_ready()
    assert calls == [1] and build.is_ready(rt.db_file)
    assert rt.ensure_ready() == ready
    assert calls == [1]


def test_a_file_without_the_marker_is_not_ready(settings: Settings) -> None:
    rt = _started(settings)
    rt.ensure_ready()
    with store.write(rt.db_file) as conn:
        conn.execute("DELETE FROM settings WHERE key = 'demo_build'")
    assert not build.is_ready(rt.db_file)


def test_a_wal_file_is_not_ready(settings: Settings) -> None:
    rt = _started(settings)
    rt.ensure_ready()
    conn = sqlite3.connect(rt.db_file)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.close()
    assert not build.is_ready(rt.db_file)


def test_two_callers_build_once(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    rt = _started(settings)
    calls = _count_builds(monkeypatch)
    barrier = threading.Barrier(2)
    out = []

    def start() -> None:
        barrier.wait()
        out.append(rt.ensure_ready())

    threads = [threading.Thread(target=start) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls == [1]
    assert len(out) == 2 and out[0] == out[1]


def test_a_new_app_start_deletes_the_built_file(settings: Settings) -> None:
    _started(settings).ensure_ready()
    rt = _started(settings)
    assert rt.available and rt.ready is None
    assert not rt.db_file.exists()


# --------------------------------------------------------------------------
# The demo writer
# --------------------------------------------------------------------------


def test_writer_keeps_delete_mode_and_an_eight_hour_session(settings: Settings) -> None:
    rt = _started(settings)
    rt.ensure_ready()
    token = store.create_session(rt.db_file)
    assert _journal_mode(rt.db_file) == "delete"
    assert _side_files(settings.demo_dir) == []
    conn = _ro(rt.db_file)
    try:
        row = conn.execute(
            "SELECT * FROM sessions WHERE token_hash = ?", (token_hash(token),)
        ).fetchone()
    finally:
        conn.close()
    assert row["user_id"] == store.DEMO_USER_ID
    assert row["last_seen_at"] == row["created_at"]
    created, expires = (datetime.fromisoformat(row[k]) for k in ("created_at", "expires_at"))
    assert expires - created == store.LIFETIME
    assert store.LIFETIME.total_seconds() == 8 * 60 * 60
    assert build.is_ready(rt.db_file)

    store.delete_session(rt.db_file, token)
    assert _journal_mode(rt.db_file) == "delete"
    assert _side_files(settings.demo_dir) == []
    conn = _ro(rt.db_file)
    try:
        assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
    finally:
        conn.close()


def test_writer_never_creates_a_file(settings: Settings) -> None:
    settings.demo_dir.mkdir()
    with pytest.raises(sqlite3.OperationalError):
        store.open_rw(settings.demo_dir / "demo.db")
    assert list(settings.demo_dir.iterdir()) == []


def test_writer_refuses_a_linked_file(samples: Settings) -> None:
    samples.demo_dir.mkdir()
    (samples.demo_dir / "demo.db").symlink_to(samples.db_path)
    real_hash = _sha(samples.db_path)
    with pytest.raises(DemoGuardError):
        store.create_session(samples.demo_dir / "demo.db")
    assert _sha(samples.db_path) == real_hash
    assert not build.is_ready(samples.demo_dir / "demo.db")
    assert os.path.islink(samples.demo_dir / "demo.db")
