import logging
import os
from datetime import time
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

from .data.notion import db_id

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseModel):
    telegram_bot_token: str
    allowed_chat_ids: set[int]
    ollama_url: str
    ollama_model: str
    ollama_num_ctx: int
    sec_user_agent: str
    poll_minutes: int
    digest_time_ist: time
    db_path: Path
    notion_token: str
    notion_portfolio_db: str
    notion_wishlist_db: str
    notion_blueprint_db: str = ""  # the conviction blueprint's rules
    news_alerts: bool = True  # push news stories as alerts (filings and regulator releases always go)
    notion_columns: dict[str, str]  # field -> column name overrides
    notion_portfolio_market: str  # "IN" | "US" | "" : used for rows without an Exchange column
    notion_wishlist_market: str


def notion_link(name: str) -> str:
    """A Notion database setting, or "" when it is empty or still a placeholder without a database id."""
    value = os.getenv(name, "").strip()
    if not value:
        return ""
    try:
        db_id(value)
    except ValueError:
        log.info("%s has no Notion database id (%r); treating it as not set", name, value)
        return ""
    return value


def load_settings() -> Settings:
    load_dotenv(ROOT / ".env")
    ids = os.getenv("ALLOWED_CHAT_IDS", "")
    hh, mm = os.getenv("DIGEST_TIME_IST", "08:30").split(":")
    db_path = Path(os.getenv("DB_PATH", "data/stockbot.db"))
    return Settings(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        allowed_chat_ids={int(x) for x in ids.split(",") if x.strip()},
        ollama_url=os.getenv("OLLAMA_URL", "http://localhost:11434"),
        ollama_model=os.getenv("OLLAMA_MODEL", "qwen2.5:7b"),
        ollama_num_ctx=int(os.getenv("OLLAMA_NUM_CTX", "8192")),
        sec_user_agent=os.getenv("SEC_USER_AGENT", ""),
        poll_minutes=int(os.getenv("POLL_MINUTES", "15")),
        digest_time_ist=time(int(hh), int(mm)),
        db_path=db_path if db_path.is_absolute() else ROOT / db_path,
        notion_token=os.getenv("NOTION_TOKEN", ""),
        notion_portfolio_db=notion_link("NOTION_PORTFOLIO_DB"),
        notion_wishlist_db=notion_link("NOTION_WISHLIST_DB"),
        notion_blueprint_db=notion_link("NOTION_BLUEPRINT_DB"),
        news_alerts=os.getenv("NEWS_ALERTS", "true").strip().lower() not in ("false", "0", "no", "off"),
        notion_portfolio_market=os.getenv("NOTION_PORTFOLIO_MARKET", "").strip().upper(),
        notion_wishlist_market=os.getenv("NOTION_WISHLIST_MARKET", "").strip().upper(),
        notion_columns={
            field: os.environ[f"NOTION_COL_{field.upper()}"]
            for field in ("ticker", "name", "exchange", "qty", "avg_price")
            if os.getenv(f"NOTION_COL_{field.upper()}")
        },
    )
