"""The conviction handlers, driven with stand-ins for Telegram's Update and Context."""

import asyncio
from types import SimpleNamespace

from stockbot.bot import handlers
from stockbot.models import Holding

from .test_conviction import GOOGL, eng  # noqa: F401  (pytest fixture)

CHAT = 42


class Message:
    def __init__(self, log):
        self.log = log

    async def reply_text(self, text, **kw):
        self.log.append(("reply", text, kw.get("reply_markup")))
        return Message(self.log)

    async def edit_text(self, text, **kw):
        self.log.append(("edit", text, None))

    async def edit_reply_markup(self, markup):
        pass


def make(e, args=(), text=""):
    log = []
    msg = Message(log)
    msg.text = text
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=CHAT), effective_message=msg, callback_query=None)

    async def send_chat_action(*a, **k):
        pass

    ctx = SimpleNamespace(
        application=SimpleNamespace(bot_data={"engine": e}), bot=SimpleNamespace(send_chat_action=send_chat_action),
        args=list(args), chat_data={},
    )
    e.settings = e.settings.model_copy(update={"allowed_chat_ids": {CHAT}})
    return update, ctx, log


def test_check_without_a_lock_explains_the_setup(eng):  # noqa: F811
    update, ctx, log = make(eng, ["GOOGL"])
    eng._resolver = SimpleNamespace(resolve=lambda q: [GOOGL])
    asyncio.run(handlers.command("ck")(update, ctx))
    assert "No conviction blueprint locked yet" in log[-1][1]


def test_check_sends_then_edits_in_profile_and_summary(eng):  # noqa: F811
    asyncio.run(eng.lock_blueprint())
    eng.llm.reply = "Google Services sells ads worldwide."
    update, ctx, log = make(eng, ["GOOGL"])
    eng._resolver = SimpleNamespace(resolve=lambda q: [GOOGL])
    asyncio.run(handlers.command("ck")(update, ctx))
    kinds = [k for k, _, _ in log]
    assert kinds == ["reply", "edit", "edit"]
    assert "VERDICT: BUY" in log[0][1] and "reading the company description" in log[0][1]
    assert "Google Services sells ads worldwide." in log[1][1]
    assert "writing summary" not in log[2][1]
    assert ctx.chat_data["last_sec"] == GOOGL


def test_report_and_blueprint_commands(eng):  # noqa: F811
    update, ctx, log = make(eng, ["lock"])
    asyncio.run(handlers.blueprint_cmd(update, ctx))
    assert "Locked blueprint v1" in log[-1][1]
    update, ctx, log = make(eng)
    asyncio.run(handlers.report_cmd(update, ctx))
    assert "portfolio is empty" in log[-1][1]
    eng.store.replace_holdings([Holding(GOOGL, "portfolio", 1, 1)])
    update, ctx, log = make(eng)
    asyncio.run(handlers.report_cmd(update, ctx))
    assert "Conviction report" in log[0][1] and "GOOGL" in log[0][1]
    update, ctx, log = make(eng)
    asyncio.run(handlers.blueprint_cmd(update, ctx))
    assert "Your conviction blueprint</b> · v1" in log[0][1]


def test_free_text_check_question_routes_to_check(eng, monkeypatch):  # noqa: F811
    from stockbot.llm.prompts import Intent

    asyncio.run(eng.lock_blueprint())
    eng._resolver = SimpleNamespace(resolve=lambda q: [GOOGL])

    async def parse_intent(text):
        return Intent(company="google", intent="check")

    monkeypatch.setattr(eng, "parse_intent", parse_intent)
    update, ctx, log = make(eng, text="why do I own Google?")
    asyncio.run(handlers.on_text(update, ctx))
    assert "Blueprint v1" in log[0][1]


def test_ambiguous_name_shows_check_buttons(eng):  # noqa: F811
    from stockbot.models import Security

    infy = [Security("INFY", "IN", "Infosys Limited"), Security("INFY", "US", "Infosys Limited")]
    eng._resolver = SimpleNamespace(resolve=lambda q: infy)
    update, ctx, log = make(eng, ["infosys"])
    asyncio.run(handlers.command("ck")(update, ctx))
    buttons = [b.callback_data for row in log[-1][2].inline_keyboard for b in row]
    assert buttons == ["ck|INFY|IN", "ck|INFY|US"]


def test_why_for_an_etf_has_no_summary_step(eng, monkeypatch):  # noqa: F811
    from stockbot.analysis import report

    from .test_conviction import ETF, QQQM, research

    asyncio.run(eng.lock_blueprint())
    monkeypatch.setattr(report, "gather", lambda sec, *a, **k: research(sec, data=ETF))
    update, ctx, log = make(eng, ["QQQM"])
    eng._resolver = SimpleNamespace(resolve=lambda q: [QQQM])
    asyncio.run(handlers.command("ck")(update, ctx))
    assert [k for k, _, _ in log] == ["reply", "edit"]  # no third edit: Telegram rejects an unchanged edit
    assert "NOT JUDGED" in log[-1][1] and "Not in your portfolio." in log[-1][1]
    assert eng.llm.calls == 0 and ctx.chat_data["last_sec"] == QQQM
