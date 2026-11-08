"""Exercise the bot without Telegram.

    python -m stockbot.cli analyze RELIANCE
    python -m stockbot.cli results AAPL
    python -m stockbot.cli news "hdfc bank"
    python -m stockbot.cli ask "how were infosys results?"
    python -m stockbot.cli sync        # read the Notion portfolio and wishlist
    python -m stockbot.cli portfolio   # P&L and wishlist prices
    python -m stockbot.cli digest      # the daily digest
    python -m stockbot.cli monitor     # sync, then one alert pass (alerts print here)
    python -m stockbot.cli why TCS     # judged by your conviction blueprint
    python -m stockbot.cli report      # every portfolio holding judged by your blueprint
    python -m stockbot.cli blueprint [lock]
"""

import asyncio
import html
import logging
import re
import sys

from .alerts import digest
from .analysis import conviction as cv
from .analysis import report
from .config import load_settings
from .engine import Engine

CLI_CHAT = 0


def plain(s: str) -> str:
    s = re.sub(r'<a href="([^"]*)">(.*?)</a>', r"\2 (\1)", s)
    return html.unescape(re.sub(r"<[^>]+>", "", s))


async def one(eng: Engine, query: str):
    matches = await eng.resolve(query)
    if not matches:
        sys.exit(f"No stock matches {query!r}")
    if len(matches) > 1:
        print("Ambiguous; using the first of:", ", ".join(s.label for s in matches))
    return matches[0]


async def main(argv: list[str]) -> None:
    if not argv:
        sys.exit(__doc__)
    cmd, args = argv[0], argv[1:]
    eng = Engine(load_settings())
    if cmd == "analyze":
        r = await eng.research(await one(eng, " ".join(args)))
        print(plain(report.render_analysis(r, await eng.summary(r))))
    elif cmd == "results":
        r = await eng.research(await one(eng, " ".join(args)))
        print(plain(report.render_results(r, await eng.results_summary(r))))
    elif cmd == "news":
        print(plain(await eng.news(await one(eng, " ".join(args)))))
    elif cmd == "ask":
        text = " ".join(args)
        intent = await eng.parse_intent(text)
        print(f"[intent] {intent}")
        if not intent.company:
            sys.exit("No company found in the question.")
        query = f"{'NSE' if intent.market == 'IN' else 'US'}:{intent.company}" if intent.market else intent.company
        sec = await one(eng, query)
        print(plain(await eng.answer(CLI_CHAT, sec, text)))
    elif cmd == "sync":
        rep = await eng.sync()
        for h in rep.holdings:
            extra = f" qty {h.qty:g} @ {h.avg_price}" if h.qty else ""
            print(f"{h.icon} {h.list_type:9} {h.sec.label}{extra}")
        for row, why in rep.unmatched:
            print(f"!! {row.list_type:9} {row.label}: {why}")
        print(f"added {[x.symbol for x in rep.added]} removed {[x.symbol for x in rep.removed]}")
    elif cmd == "why":
        sec = await one(eng, " ".join(args))
        c = await eng.conviction(sec)
        if c is None:
            sys.exit(plain(cv.SETUP))
        profile = await eng.business_profile(sec, c.research)
        summary = None if c.fund else await eng.conviction_summary(c)
        print(plain(cv.render_check(c, profile, summary)))
    elif cmd == "report":
        if eng.store.locked_blueprint() is None:
            sys.exit(plain(cv.SETUP))
        if not any(h.list_type == "portfolio" for h in eng.store.holdings()):
            sys.exit("Nothing to report: the Notion portfolio is empty (run sync first).")
        for msg in cv.render_report(await eng.conviction_report()):
            print(plain(msg))
    elif cmd == "blueprint":
        pages = cv.render_lock(await eng.lock_blueprint()) if args[:1] == ["lock"] else cv.render_blueprint(await eng.blueprint_status())
        print("\n".join(plain(x) for x in pages))
    elif cmd in ("portfolio", "digest"):
        print(plain(await digest.build(eng.store.holdings(), with_news=cmd == "digest") or "No holdings; run sync."))
    elif cmd == "monitor":
        async def send(chat_id, msg):
            print(f"--- alert ---\n{plain(msg)}\n")

        print("messages sent:", await eng.check([CLI_CHAT], send))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main(sys.argv[1:]))
