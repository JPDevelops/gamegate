import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    env: str = "development"
    db_path: str = "gamegate.db"
    api_token: str | None = None
    urgent_breaks_through_gaming: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings(
        env=os.environ.get("GAMEGATE_ENV", "development"),
        db_path=os.environ.get("GAMEGATE_DB_PATH", "gamegate.db"),
        api_token=os.environ.get("GAMEGATE_API_TOKEN") or None,
        urgent_breaks_through_gaming=(
            os.environ.get("GAMEGATE_URGENT_BREAKTHROUGH", "true").lower() != "false"
        ),
    )
