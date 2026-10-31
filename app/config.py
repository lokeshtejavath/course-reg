"""Runtime configuration read from environment variables."""

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    database_url: str
    seed_file: Path | None


def load_settings() -> Settings:
    database_url = os.environ.get("DATABASE_URL", f"sqlite:///{PROJECT_ROOT / 'data' / 'course_reg.db'}")
    seed_env = os.environ.get("SEED_FILE", str(PROJECT_ROOT / "seed" / "seed.json"))
    # An empty SEED_FILE disables seeding.
    seed_file = Path(seed_env) if seed_env else None
    return Settings(database_url=database_url, seed_file=seed_file)
