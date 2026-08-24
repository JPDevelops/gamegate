import os
from dataclasses import dataclass
from functools import lru_cache

# Load a local .env if one is present so the documented `cp .env.example .env`
# + `uvicorn` setup actually configures the app. Under systemd the unit's
# EnvironmentFile= already populates the environment; load_dotenv() does not
# override existing variables, so the two paths agree. No-op if the optional
# python-dotenv dependency is absent.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - dotenv is a declared dependency
    pass


@dataclass(frozen=True)
class Settings:
    env: str = "development"
    db_path: str = "gamegate.db"
    api_token: str | None = None
    # True only when GAMEGATE_ENV was explicitly set to "development" — used to
    # decide whether an unset token may run open (dev) or must fail closed.
    explicit_dev: bool = False
    # NOTE: urgent break-through is a per-user DB setting (urgent_breakthrough,
    # editable in the dashboard), not an env var — see SettingsService. The old
    # GAMEGATE_URGENT_BREAKTHROUGH field was removed because nothing read it.


@lru_cache
def get_settings() -> Settings:
    return Settings(
        explicit_dev=(os.environ.get("GAMEGATE_ENV") == "development"),
        env=os.environ.get("GAMEGATE_ENV", "development"),
        db_path=os.environ.get("GAMEGATE_DB_PATH", "gamegate.db"),
        api_token=os.environ.get("GAMEGATE_API_TOKEN") or None,
    )
