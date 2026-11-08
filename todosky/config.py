"""Settings, read from the environment and an optional .env file."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


class ConfigError(Exception):
    pass


def load_dotenv(path: Path) -> None:
    """Set variables from a simple KEY=value file without overriding the real environment."""
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


@dataclass(frozen=True)
class Config:
    todoist_token: str
    skylight_email: str
    skylight_password: str
    skylight_frame_id: str | None = None
    skylight_category: str = "Dad"
    todoist_filter: str = "#chores & @sky & !no date"
    poll_seconds: int = 120
    state_path: Path = Path("todosky.db")

    @classmethod
    def from_env(cls, env_file: Path | None = Path(".env")) -> "Config":
        if env_file:
            load_dotenv(env_file)
        missing = [k for k in ("TODOIST_TOKEN", "SKYLIGHT_EMAIL", "SKYLIGHT_PASSWORD") if not os.environ.get(k)]
        if missing:
            raise ConfigError(f"Missing required settings: {', '.join(missing)}")
        try:
            poll = int(os.environ.get("POLL_SECONDS", "120"))
        except ValueError as err:
            raise ConfigError("POLL_SECONDS must be a whole number") from err
        return cls(
            todoist_token=os.environ["TODOIST_TOKEN"],
            skylight_email=os.environ["SKYLIGHT_EMAIL"],
            skylight_password=os.environ["SKYLIGHT_PASSWORD"],
            skylight_frame_id=os.environ.get("SKYLIGHT_FRAME_ID") or None,
            skylight_category=os.environ.get("SKYLIGHT_CATEGORY", "Dad"),
            todoist_filter=os.environ.get("TODOIST_FILTER", "#chores & @sky & !no date"),
            poll_seconds=max(30, poll),
            state_path=Path(os.environ.get("STATE_PATH", "todosky.db")),
        )
