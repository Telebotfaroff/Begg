from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    bot_token: str = os.getenv("BOT_TOKEN", "")
    api_id: int = int(os.getenv("API_ID", "0") or 0)
    api_hash: str = os.getenv("API_HASH", "")
    admin_id: int = int(os.getenv("ADMIN_ID", "0") or 0)
    bot_name: str = os.getenv("BOT_NAME", "BEGG")
    download_dir: str = os.getenv("DOWNLOAD_DIR", "downloads")
    download_concurrency: int = max(1, min(5, int(os.getenv("DOWNLOAD_CONCURRENCY", "5"))))
    upload_concurrency: int = max(1, min(2, int(os.getenv("UPLOAD_CONCURRENCY", "2"))))
    search_limit: int = max(1, min(20, int(os.getenv("SEARCH_LIMIT", "10"))))
    update_interval: int = max(2, int(os.getenv("UPDATE_INTERVAL", "3")))
    video_quality: str = os.getenv("VIDEO_QUALITY", "best")


settings = Settings()
