from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    token: str
    timezone: ZoneInfo
    database_path: Path


def load_settings() -> Settings:
    load_dotenv()

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required in .env")

    timezone_name = os.getenv("BOT_TIMEZONE", "Asia/Shanghai").strip()
    database_path = Path(os.getenv("DATABASE_PATH", "data/bot.sqlite3")).resolve()

    return Settings(
        token=token,
        timezone=ZoneInfo(timezone_name),
        database_path=database_path,
    )
