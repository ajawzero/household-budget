"""The demo Settings: a copy of the real Settings from an allowlist (demo plan section 5).

Every Settings field is in exactly one of `COPIED` (kept from the real Settings) or `SET`
(replaced). A test fails when a new field is in neither.

Built with `model_copy`, never `Settings(...)`: a `Settings(...)` call reads every field
it is not given from the env again (`BUDGET_DB_PATH`, `BUDGET_PRIVATE_RULES`), and
`extra="ignore"` drops a misspelled name without a word. `model_copy` has traps of its
own: it does not validate (so only `Path` and `None` go in), and an update key that is
not a field name (an alias like `ANTHROPIC_API_KEY`, a typo) is kept as a stray entry
while the real value stays. So the update keys are checked against the fields here.
Settings is not hashable: build this once per app, never behind `lru_cache`.
"""

from __future__ import annotations

from pathlib import Path

from app.config import Settings

COPIED = frozenset(
    {
        "allowed_hosts",
        "categories_file",
        "demo_dir",
        "demo_runs",
        "dev",
        "import_poll_seconds",
        "public_port",
        "static_dir",
    }
)
SET = frozenset(
    {
        "anthropic_api_key",
        "backup_dir",
        "db_path",
        "import_dir",
        "private_rules",
        "tiingo_api_key",
    }
)


def demo_settings(real: Settings, *, db_file: Path, backup_dir: Path) -> Settings:
    """`real` with the demo database, no keys, no private rules and the runs folder.

    `import_dir` is the runs folder, read only (no demo request reads it; the build
    imports from it). `backup_dir` is a demo folder that is never made, so the
    backup list stays empty.
    """
    update: dict[str, object] = {
        "db_path": Path(db_file),
        "import_dir": Path(real.demo_runs),
        "backup_dir": Path(backup_dir),
        "private_rules": None,
        "anthropic_api_key": None,
        "tiingo_api_key": None,
    }
    if set(update) != SET or not SET <= set(Settings.model_fields):
        raise ValueError("demo settings name a field that is not a Settings field")
    return real.model_copy(update=update)
