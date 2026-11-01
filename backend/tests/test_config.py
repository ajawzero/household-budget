"""docs/budget.env.example documents every setting, with fake values and empty keys."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.config import Settings
from app.importer.sanitize import REDACTION_ENV

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "docs" / "budget.env.example"
DEPLOY = REPO / "scripts" / "deploy.sh"
LINE = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*)=(.*)$")


def _vars() -> dict[str, tuple[str, bool]]:
    """NAME -> (value, commented out)."""
    out = {}
    for raw in EXAMPLE.read_text(encoding="utf-8").splitlines():
        m = LINE.match(raw.strip())
        if m:
            out[m[1]] = (m[2], raw.startswith("#"))
    return out


def test_every_setting_is_documented() -> None:
    names = set(_vars())
    expected = {f"BUDGET_{f.upper()}" for f in Settings.model_fields} - {
        "BUDGET_ANTHROPIC_API_KEY",
        "BUDGET_TIINGO_API_KEY",
    }
    assert expected | {REDACTION_ENV, "TZ", "ANTHROPIC_API_KEY", "TIINGO_API_KEY"} <= names


def test_keys_are_empty_and_load_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    v = _vars()
    assert v["ANTHROPIC_API_KEY"] == ("", False) and v["TIINGO_API_KEY"] == ("", False)
    for name in list(v):
        monkeypatch.delenv(name, raising=False)
    s = Settings(_env_file=EXAMPLE)
    assert s.anthropic_api_key is None and s.tiingo_api_key is None
    assert s.dev is False
    assert s.allowed_hosts == ["budget.home.example"]
    assert s.db_path == Path("/data/db/budget.db")
    assert s.backup_dir == Path("/data/backups")
    assert s.private_rules == Path("/data/config/private_rules.json")


def test_active_lines_match_deploy_setup_defaults() -> None:
    """deploy.sh setup writes the same active lines when there is no env file yet."""
    if not DEPLOY.exists():
        pytest.skip("no deploy.sh")
    active = [
        line
        for line in EXAMPLE.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    text = DEPLOY.read_text(encoding="utf-8")
    blocks = [b.split("\nEOF\n", 1)[0] for b in text.split("<<'EOF'\n")[1:]]
    env_blocks = [b.splitlines() for b in blocks if "BUDGET_DB_PATH=" in b]
    assert env_blocks == [active]
