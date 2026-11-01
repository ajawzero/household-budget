"""Settings from the environment (pydantic-settings).

Every setting reads `BUDGET_<NAME>` from the environment. Paths default to the
git-ignored `var/` folder in the repo, so local dev needs no env file.

Secrets are `SecretStr` and never go into a response or a log. Phase 1 has
none in use, but the pattern is here for the API keys of later phases.
"""

from __future__ import annotations

import tempfile
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.demo.context import pinned_date

REPO_ROOT = Path(__file__).resolve().parents[2]
VAR_DIR = REPO_ROOT / "var"
HOUSEHOLD_TZ = ZoneInfo("America/Chicago")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BUDGET_", extra="ignore", frozen=True)

    db_path: Path = VAR_DIR / "budget.db"
    import_dir: Path = VAR_DIR / "import"
    backup_dir: Path = VAR_DIR / "backups"
    # private_rules.json (personal subscription names, employer terms). Unset: no lists.
    private_rules: Path | None = None
    categories_file: Path = REPO_ROOT / "schema" / "categories.json"
    # Comma-separated host names for TrustedHostMiddleware.
    allowed_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["budget.home.example"]
    )
    # HTTPS port browsers use to reach NPM when it is not 443 (Origin includes it).
    public_port: int | None = Field(default=None, ge=1, le=0xFFFF)
    dev: bool = False
    import_poll_seconds: int = Field(default=60, ge=1)
    # Built frontend. Unset: backend/app/static when it holds an index.html.
    static_dir: Path | None = None
    # Demo mode (docs/plans/demo-mode.md). The sample runs the demo database is built from.
    # The image sets it. In the image this default is /samples/data (REPO_ROOT is /),
    # which does not exist, so demo stays off.
    demo_runs: Path = REPO_ROOT / "samples" / "data"
    # The demo database folder. Only the demo's own file names in it are ever deleted.
    demo_dir: Path = Path(tempfile.gettempdir()) / "budget-demo-dev"

    # Secrets (used from later phases). Read without the BUDGET_ prefix too.
    anthropic_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY", "BUDGET_ANTHROPIC_API_KEY")
    )
    tiingo_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("TIINGO_API_KEY", "BUDGET_TIINGO_API_KEY")
    )

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def _split_hosts(cls, v: object) -> object:
        if isinstance(v, str):
            return [h.strip() for h in v.split(",") if h.strip()]
        return v

    @field_validator(
        "private_rules",
        "static_dir",
        "public_port",
        "anthropic_api_key",
        "tiingo_api_key",
        mode="before",
    )
    @classmethod
    def _blank_is_none(cls, v: object) -> object:
        """An empty value means unset. An empty key leaves its feature off."""
        return None if v == "" else v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process-wide settings. Tests call `get_settings.cache_clear()` after changing env."""
    return Settings()


def household_now() -> datetime:
    """Now in America/Chicago. A demo request pins the date (app/demo/context.py)."""
    now = datetime.now(HOUSEHOLD_TZ)
    day = pinned_date()
    return now if day is None else datetime.combine(day, now.timetz())


def household_today() -> date:
    """Today in America/Chicago. Every "today" and "this month" decision uses this."""
    return household_now().date()
