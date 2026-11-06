import logging
import sys
from datetime import timedelta
from zoneinfo import ZoneInfo

import httpx
from telegram import BotCommand
from telegram.ext import Application

from .bot import handlers
from .config import load_settings
from .engine import Engine

log = logging.getLogger("stockbot")

COMMANDS = [
    BotCommand("analyze", "Strengths and weaknesses of a stock"),
    BotCommand("results", "Latest quarterly results"),
    BotCommand("news", "Latest filings and news"),
    BotCommand("check", "A stock judged by your conviction blueprint"),
    BotCommand("why", "Why you own a stock: thesis and blueprint"),
    BotCommand("report", "Every holding judged by your blueprint"),
    BotCommand("blueprint", "Your blueprint; /blueprint lock adopts Notion edits"),
    BotCommand("portfolio", "Portfolio P&L and wishlist"),
    BotCommand("sync", "Re-read Notion portfolio and wishlist"),
    BotCommand("checknow", "Check portfolio and wishlist for updates"),
    BotCommand("digest", "Daily digest, now"),
    BotCommand("help", "How to use the bot"),
]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # it logs every request URL, bot token included
    settings = load_settings()
    if not settings.telegram_bot_token:
        sys.exit("TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")
    if not settings.sec_user_agent or "example.com" in settings.sec_user_agent:
        log.warning("SEC_USER_AGENT is not set to a real contact; SEC EDGAR may reject requests.")
    if not settings.allowed_chat_ids:
        log.warning("ALLOWED_CHAT_IDS is empty: send /start to the bot to get your chat id.")
    try:
        tags = httpx.get(f"{settings.ollama_url}/api/tags", timeout=5).json()
        if not any(m["name"] == settings.ollama_model for m in tags.get("models", [])):
            log.warning("Ollama has no model %s; run: ollama pull %s", settings.ollama_model, settings.ollama_model)
    except Exception:
        log.warning("Ollama is not reachable at %s; summaries and Q&A will fail until it runs.", settings.ollama_url)

    eng = Engine(settings)

    async def post_init(app: Application) -> None:
        await app.bot.set_my_commands(COMMANDS)
        await eng.resolver()  # download the NSE and SEC ticker lists once, up front
        try:
            rep = await eng.sync()
            log.info("Notion: tracking %d stocks, %d rows unmatched", len(rep.holdings), len(rep.unmatched))
        except Exception as e:
            log.warning("Notion sync at startup failed: %s", e)

    app = Application.builder().token(settings.telegram_bot_token).post_init(post_init).build()
    app.bot_data["engine"] = eng
    handlers.register(app)
    app.job_queue.run_repeating(
        handlers.monitor_job, interval=timedelta(minutes=settings.poll_minutes), first=timedelta(seconds=60)
    )
    app.job_queue.run_daily(
        handlers.digest_job, time=settings.digest_time_ist.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
    )
    log.info("stock bot running (model %s, polling every %d min)", settings.ollama_model, settings.poll_minutes)
    app.run_polling()


if __name__ == "__main__":
    main()
