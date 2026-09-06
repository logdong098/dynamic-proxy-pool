import os
from typing import List
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Server settings
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = False

    # Database
    DATA_DIR: str = "data"
    DB_PATH: str = "data/proxies.db"

    # Telegram UserBot (Collector)
    TG_API_ID: int = Field(default=0, description="Telegram App API ID")
    TG_API_HASH: str = Field(default="", description="Telegram App API Hash")
    TG_SESSION_PATH: str = "data/userbot.session"
    TG_TARGET_CHATS: str = Field(
        default="",
        description="Comma-separated chat usernames or IDs to monitor"
    )

    # Telegram Bot (User Query Interface)
    TG_BOT_TOKEN: str = Field(default="", description="Telegram Query Bot Token from @BotFather")

    # Checker settings
    CHECK_CONCURRENCY: int = 50
    CHECK_TIMEOUT: float = 8.0
    CHECK_BATCH_SIZE: int = 100
    CHECK_INTERVAL_MINUTES: int = 10
    FAST_CHECK: bool = False
    FAST_CHECK_THRESHOLD: int = 1000
    MAX_FAIL_COUNT: int = 3
    CHECK_TARGET_URL: str = "https://cloudflare.com/cdn-cgi/trace"
    BACKUP_TARGET_URL: str = "http://httpbin.org/ip"

    # Scrapers
    SCRAPE_INTERVAL_MINUTES: int = 15
    TARGET_COUNTRIES: str = "PH,US,HK,SG,JP,TW,KR,GB,DE,VN,TH,ID,MY"

    @property
    def target_country_list(self) -> List[str]:
        return [c.strip().upper() for c in self.TARGET_COUNTRIES.split(",") if c.strip()]

    @property
    def tg_target_chat_list(self) -> List[str]:
        return [c.strip() for c in self.TG_TARGET_CHATS.split(",") if c.strip()]


settings = Settings()

# Ensure data directory exists
os.makedirs(settings.DATA_DIR, exist_ok=True)
