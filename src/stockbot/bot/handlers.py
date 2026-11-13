import asyncio
import contextlib
import logging
from functools import wraps
from html import escape

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ..alerts import digest
from ..analysis import conviction as cv
from ..analysis import report
from ..engine import Engine
from ..models import Security

log = logging.getLogger(__name__)

HELP = (
    "<b>Stock research bot</b> (India NSE + US)\n\n"
    "/analyze <code>TICKER</code> — strengths, weaknesses, latest quarter\n"
    "/results <code>TICKER</code> — latest quarterly results explained\n"
    "/news <code>TICKER</code> — latest filings and news with impact tags\n"
    "/why <code>TICKER</code> — the stock judged by your conviction blueprint\n"
    "/report — every holding judged by your blueprint\n"
    "/blueprint — your locked blueprint; <code>/blueprint lock</code> adopts your Notion edits\n"
    "/portfolio — your Notion portfolio (with P&L) and wishlist\n"
    "/sync — re-read the Notion portfolio and wishlist now\n"
    "/checknow — check your portfolio and wishlist for updates now\n"
    "/digest — the daily digest, now\n\n"
    "Alerts on new filings, news and regulator actions cover only the stocks in your Notion "
    "portfolio 💼 and wishlist ⭐. Add or remove stocks there.\n\n"
    "Or just ask: <i>how were Infosys results?</i> · <i>is AAPL's debt a concern?</i>\n"
    "Tickers: <code>RELIANCE</code>, <code>INFY.NS</code>, <code>AAPL</code>, or a company name. "
    "Prefix <code>NSE:</code> or <code>US:</code> when a name exists in both markets."
)

NO_HOLDINGS = (
    "No stocks to track yet. Add them to your Notion portfolio or wishlist (and check the NOTION_* "
    "settings in .env), then send /sync."
)

# Callback data is "<action>|<symbol>|<market>"; Telegram caps it at 64 bytes.
ACTIONS = {"an": "analyze", "rs": "results", "nw": "news", "qa": "answer", "ck": "check"}


def engine(ctx: ContextTypes.DEFAULT_TYPE) -> Engine:
    return ctx.application.bot_data["engine"]


def allowed_only(fn):
    @wraps(fn)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        if chat is None or chat.id not in engine(ctx).settings.allowed_chat_ids:
            log.info("ignored update from chat %s", chat and chat.id)
            return
        return await fn(update, ctx)

    return wrapper


@contextlib.asynccontextmanager
async def typing(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Keep Telegram's "typing…" visible while the local model works (it expires after ~5s)."""

    async def loop():
        while True:
            with contextlib.suppress(Exception):
                await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
            await asyncio.sleep(4)

    task = asyncio.create_task(loop())
    try:
        yield
    finally:
        task.cancel()


async def reply(update: Update, html: str, **kw):
    return await update.effective_message.reply_text(
        report.clip(html), parse_mode=ParseMode.HTML, disable_web_page_preview=True, **kw
    )


async def pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE, query: str, action: str) -> Security | None:
    """Resolve a ticker or name; when it is ambiguous, show buttons and return None."""
    if not query:
        await reply(update, f"Which stock? e.g. <code>/{ACTIONS[action]} RELIANCE</code>")
        return None
    matches = await engine(ctx).resolve(query)
    if not matches:
        await reply(update, f"I couldn't find a listed stock matching <b>{escape(query)}</b>.")
        return None
    if len(matches) == 1:
        return matches[0]
    buttons = [
        [InlineKeyboardButton(s.label[:60], callback_data=f"{action}|{s.symbol}|{s.market}")] for s in matches
    ]
    await reply(update, f"Which one did you mean by <b>{escape(query)}</b>?", reply_markup=InlineKeyboardMarkup(buttons))
    return None


# --- actions (shared by commands, buttons and free text) ---

async def do_analyze(update, ctx, sec: Security):
    eng = engine(ctx)
    async with typing(update, ctx):
        r = await eng.research(sec)
        msg = await reply(update, report.render_analysis(r, None))
        summary = await eng.summary(r)
    await msg.edit_text(report.clip(report.render_analysis(r, summary)), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    ctx.chat_data["last_sec"] = sec


async def do_results(update, ctx, sec: Security):
    eng = engine(ctx)
    async with typing(update, ctx):
        r = await eng.research(sec)
        msg = await reply(update, report.render_results(r, None))
        summary = await eng.results_summary(r)
    await msg.edit_text(report.clip(report.render_results(r, summary)), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    ctx.chat_data["last_sec"] = sec


async def do_news(update, ctx, sec: Security):
    async with typing(update, ctx):
        html = await engine(ctx).news(sec)
    await reply(update, html)
    ctx.chat_data["last_sec"] = sec


async def do_answer(update, ctx, sec: Security, question: str | None = None):
    question = question or ctx.chat_data.pop("pending_question", None) or f"Give me the latest update on {sec.name}."
    async with typing(update, ctx):
        text = await engine(ctx).answer(update.effective_chat.id, sec, question)
    await reply(update, f"<b>{escape(sec.label)}</b>\n\n{escape(text)}\n\n{report.DISCLAIMER}")
    ctx.chat_data["last_sec"] = sec


async def do_check(update, ctx, sec: Security):
    """The verdict and rules first; the business profile and the model's summary are edited in after."""
    eng = engine(ctx)
    async with typing(update, ctx):
        c = await eng.conviction(sec)
        if c is None:
            await reply(update, cv.SETUP)
            return
        msg = await reply(update, cv.render_check(c, None, None))
        profile = await eng.business_profile(sec, c.research)
        if c.fund:  # no verdict, so nothing for the model to explain
            await msg.edit_text(cv.render_check(c, profile, None), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            ctx.chat_data["last_sec"] = sec
            return
        with contextlib.suppress(Exception):
            await msg.edit_text(cv.render_check(c, profile, None), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        summary = await eng.conviction_summary(c)
    await msg.edit_text(cv.render_check(c, profile, summary), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    ctx.chat_data["last_sec"] = sec


DISPATCH = {"an": do_analyze, "rs": do_results, "nw": do_news, "qa": do_answer, "ck": do_check}


def command(action: str):
    @allowed_only
    async def handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        sec = await pick(update, ctx, " ".join(ctx.args), action)
        if sec:
            await DISPATCH[action](update, ctx, sec)

    return handler


# --- other handlers ---

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    allowed = engine(ctx).settings.allowed_chat_ids
    if chat_id in allowed:
        await reply(update, HELP)
    elif not allowed:
        # Setup mode: nobody is allowed yet, so tell the owner their id.
        await reply(update, f"Your chat id is <code>{chat_id}</code>. Put it in ALLOWED_CHAT_IDS in .env and restart the bot.")
        log.info("setup: /start from chat %s", chat_id)
    else:
        log.info("ignored /start from chat %s", chat_id)


@allowed_only
async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await reply(update, HELP)


def chats(ctx: ContextTypes.DEFAULT_TYPE) -> list[int]:
    return sorted(engine(ctx).settings.allowed_chat_ids)


@allowed_only
async def portfolio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    async with typing(update, ctx):
        html = await digest.build(engine(ctx).store.holdings(), with_news=False)
    await reply(update, html or NO_HOLDINGS)


@allowed_only
async def sync_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        async with typing(update, ctx):
            rep = await engine(ctx).sync()
    except Exception as e:
        await reply(update, f"⚠️ Notion sync failed: {escape(str(e))}")
        return
    port = sum(1 for h in rep.holdings if h.list_type == "portfolio")
    lines = [f"🔄 Synced from Notion: {port} in portfolio, {len(rep.holdings) - port} in wishlist."]
    if rep.added:
        lines.append("Added: " + ", ".join(escape(x.symbol) for x in rep.added))
    if rep.removed:
        lines.append("Removed: " + ", ".join(escape(x.symbol) for x in rep.removed))
    if rep.sold:
        lines.append("Sold (Qty 0), moved to Notion trash: " + ", ".join(escape(r.label) for r in rep.sold))
    if rep.unmatched:
        lines.append("\n⚠️ <b>Rows I can't track</b>")
        lines += [f"• {escape(row.label)} ({row.list_type}): {escape(why)}" for row, why in rep.unmatched]
    await reply(update, "\n".join(lines))


@allowed_only
async def checknow(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    async with typing(update, ctx):
        sent = await engine(ctx).check(chats(ctx), sender(ctx))
    if not engine(ctx).store.holdings():
        await reply(update, NO_HOLDINGS)
    elif not sent:
        await reply(update, "✅ Checked your portfolio and wishlist: nothing new since the last check.")


@allowed_only
async def digest_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    async with typing(update, ctx):
        html = await digest.build(engine(ctx).store.holdings(), with_news=True)
    await reply(update, html or NO_HOLDINGS)


@allowed_only
async def report_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    eng = engine(ctx)
    async with typing(update, ctx):
        if eng.store.locked_blueprint() is None:
            await reply(update, cv.SETUP)
            return
        if not any(h.list_type == "portfolio" for h in eng.store.holdings()):
            await reply(update, "Nothing to report: your Notion portfolio is empty. Add your stocks there, then send /sync.")
            return
        rep = await eng.conviction_report()
    for html in cv.render_report(rep):
        await update.effective_message.reply_text(html, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


@allowed_only
async def blueprint_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    eng = engine(ctx)
    lock = bool(ctx.args) and ctx.args[0].lower() == "lock"
    async with typing(update, ctx):
        pages = cv.render_lock(await eng.lock_blueprint()) if lock else cv.render_blueprint(await eng.blueprint_status())
    for html in pages:
        await update.effective_message.reply_text(html, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


@allowed_only
async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    action, symbol, mkt = q.data.split("|")
    sec = (await engine(ctx).resolver()).find(symbol, mkt)
    await q.edit_message_reply_markup(None)
    if sec and action in DISPATCH:
        await DISPATCH[action](update, ctx, sec)


@allowed_only
async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.effective_message.text.strip()
    eng = engine(ctx)
    async with typing(update, ctx):
        intent = await eng.parse_intent(text)
    sec = ctx.chat_data.get("last_sec")
    if intent.company:
        query = f"{'NSE' if intent.market == 'IN' else 'US'}:{intent.company}" if intent.market else intent.company
        ctx.chat_data["pending_question"] = text
        if intent.intent == "check":
            action = "ck"
        else:
            action = "an" if intent.intent == "analyze" and "?" not in text else "qa"
        sec = await pick(update, ctx, query, action)
        if sec is None:
            return  # buttons shown; the question is answered after the pick
        ctx.chat_data.pop("pending_question", None)
    if sec is None:
        await reply(update, "Which stock do you mean? e.g. <i>how were Infosys results?</i> or <code>/analyze TCS</code>")
        return
    if intent.intent == "check":
        await do_check(update, ctx, sec)
    elif intent.intent == "analyze" and "?" not in text:
        await do_analyze(update, ctx, sec)
    else:
        await do_answer(update, ctx, sec, text)


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    log.error("handler error", exc_info=ctx.error)
    if isinstance(update, Update) and update.effective_chat and update.effective_chat.id in engine(ctx).settings.allowed_chat_ids:
        with contextlib.suppress(Exception):
            await ctx.bot.send_message(update.effective_chat.id, f"⚠️ Something went wrong: {escape(str(ctx.error))[:300]}")


# --- jobs ---

def sender(ctx: ContextTypes.DEFAULT_TYPE):
    async def send(chat_id: int, html: str):
        await ctx.bot.send_message(chat_id, html, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

    return send


def heartbeat_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10)


async def heartbeat(url: str, ok: bool) -> None:
    """Dead-man's switch: tell an outside service (e.g. healthchecks.io) that a monitor pass ran.

    When the pings stop, because the bot hung or the server went down, that service alerts the investor.
    A failed ping is only logged, never raised, and the URL is not logged: it identifies the check.
    """
    if not url:
        return
    try:
        async with heartbeat_client() as client:
            (await client.get(url if ok else f"{url}/fail")).raise_for_status()
    except Exception as e:
        log.warning("heartbeat ping failed (%s)", type(e).__name__)


async def monitor_job(ctx: ContextTypes.DEFAULT_TYPE):
    eng = engine(ctx)
    try:
        sent = await eng.check(chats(ctx), sender(ctx))
        log.info("monitor pass: %d alerts", sent)
        ok = True
    except Exception:
        log.exception("monitor pass failed")
        ok = False
    await heartbeat(eng.settings.heartbeat_url, ok)


async def digest_job(ctx: ContextTypes.DEFAULT_TYPE):
    try:
        html = await digest.build(engine(ctx).store.holdings(), with_news=True)
    except Exception:
        log.exception("digest failed")
        return
    if html:
        for chat_id in chats(ctx):
            await sender(ctx)(chat_id, html)


def register(app: Application) -> None:
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("analyze", command("an")))
    app.add_handler(CommandHandler("results", command("rs")))
    app.add_handler(CommandHandler("news", command("nw")))
    app.add_handler(CommandHandler("why", command("ck")))
    app.add_handler(CommandHandler("report", report_cmd))
    app.add_handler(CommandHandler("blueprint", blueprint_cmd))
    app.add_handler(CommandHandler("portfolio", portfolio))
    app.add_handler(CommandHandler("sync", sync_cmd))
    app.add_handler(CommandHandler("checknow", checknow))
    app.add_handler(CommandHandler("digest", digest_cmd))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
