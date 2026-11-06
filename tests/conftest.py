from datetime import time
from pathlib import Path

import pytest

from stockbot.config import Settings


def make_settings(**overrides) -> Settings:
    base = dict(
        telegram_bot_token="",
        allowed_chat_ids=set(),
        ollama_url="http://localhost:11434",
        ollama_model="test",
        ollama_num_ctx=2048,
        sec_user_agent="stock-bot test@example.com",
        poll_minutes=15,
        digest_time_ist=time(8, 30),
        db_path=Path(":memory:"),
        notion_token="secret",
        notion_portfolio_db="1a2b3c4d5e6f40718293a4b5c6d7e8f9",
        notion_wishlist_db="",
        notion_columns={},
        notion_portfolio_market="",
        notion_wishlist_market="",
    )
    return Settings(**{**base, **overrides})


@pytest.fixture
def settings():
    return make_settings()
