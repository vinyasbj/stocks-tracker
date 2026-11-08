"""Results of the conviction features and their Telegram rendering (HTML): /why, /report, /blueprint."""

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from html import escape

from ..models import Holding, Security
from . import fmt, signals
from .blueprint import METRICS, PILLARS, Blueprint, Diff, Locked, Parsed, Rule, fmt_value
from .report import Research, header, statement_currency
from .thesis import TITLES, Thesis
from .verdict import HELD_ORDER, Check, Verdict, breakdown

DISCLAIMER = (
    "<i>Blueprint v{version} · your own rules, not investment advice · "
    "Data: Yahoo Finance, NSE/SEC filings, Google News</i>"
)
TELEGRAM_LIMIT = 4000
SETUP = (
    "📘 <b>No conviction blueprint locked yet.</b>\n"
    "1. Import <code>templates/blueprint.csv</code> into Notion (or build the table yourself) and edit the "
    "rules to match how you invest. Use plain numbers: 15 means 15%.\n"
    "2. Share the database with the bot's integration (⋯ → Connections) and put its link in "
    "<code>.env</code> as <code>NOTION_BLUEPRINT_DB</code>, then restart the bot.\n"
    "3. Review it with /blueprint, then fix it with /blueprint lock."
)


# --- results ---

@dataclass
class BlueprintStatus:
    locked: Locked | None
    parsed: Parsed | None = None  # what Notion holds now
    diff: Diff | None = None  # Notion versus the locked version
    error: str | None = None  # Notion not set up or unreachable


@dataclass
class LockResult:
    status: BlueprintStatus
    version: int | None = None  # the new version, when one was stored
    unchanged: bool = False


@dataclass
class Profile:
    text: str | None  # how and where the company makes money
    source: str  # "model" | "description" | "none"


@dataclass
class Conviction:
    sec: Security
    research: Research
    holding: Holding | None
    locked: Locked
    verdict: Verdict
    thesis: Thesis | None = None
    thesis_note: str | None = None  # why there is no thesis, or why it could not be read
    conviction: str | None = None  # the investor's own level
    mismatch: str | None = None
    unlocked: Diff | None = None  # Notion edits not yet locked

    @property
    def held(self) -> bool:
        return self.verdict.held

    @property
    def fund(self) -> bool:
        """An ETF or fund: shown, but not judged, since the blueprint's rules are for companies."""
        return signals.is_fund(self.research.fundamentals)


@dataclass
class ReportEntry:
    holding: Holding
    conviction: Conviction | None = None
    error: str | None = None


@dataclass
class ReportResult:
    locked: Locked | None
    entries: list[ReportEntry] = field(default_factory=list)
    unlocked: Diff | None = None
    sync_error: str | None = None  # Notion couldn't be re-read; the last synced list was used


# --- helpers ---

def split_messages(lines: list[str], limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Pack whole lines into as few messages as fit; nothing is ever cut."""
    out, cur = [], ""
    for line in lines:
        piece = line if not cur else "\n" + line
        if cur and len(cur) + len(piece) > limit:
            out.append(cur)
            cur = line
        else:
            cur += piece
    if cur.strip():
        out.append(cur)
    return out


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%-d %b %Y")


def _version_line(locked: Locked) -> str:
    return f"v{locked.version}, locked {_day(locked.locked_at)}"


def unlocked_notice(diff: Diff | None, locked: Locked) -> str | None:
    if not diff:
        return None
    return (
        f"⚠️ Notion differs: {escape(diff.summary())}. Still using v{locked.version}. "
        "To adopt it: /blueprint lock"
    )


def _cut(text: str, n: int = 220) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


# --- /why ---

def profit_change(pct: float | None, now: float | None, base: float | None) -> str:
    """"+16.7%", or in words when a loss is involved: "-96.6%" on a loss means the loss widened."""
    if pct is None or now is None or base is None:
        return fmt.pct(pct)
    if now < 0 and base < 0:
        return f"loss {'widened' if pct < 0 else 'narrowed'} {abs(pct):.0f}%"
    if now < 0 <= base:
        return "swung to a loss"
    if base < 0 <= now:
        return "back to profit"
    return fmt.pct(pct)


def profit_lines(r: Research) -> list[str]:
    """Company-level profit, computed by code (never by the model)."""
    m, f = r.metrics, r.fundamentals
    cur = statement_currency(f)
    lines = []
    q = m.get("q_net_income")
    if q is not None:
        when = fmt.quarter_label(m.get("quarter")) + (" — out of date" if m.get("quarter_stale") else "")
        yoy = profit_change(m.get("ni_yoy"), q, m.get("ni_yoy_base"))
        qoq = profit_change(m.get("ni_qoq"), q, m.get("ni_qoq_base"))
        growth = f" · YoY {yoy} · QoQ {qoq}"
        if q < 0:
            lines.append(f"<b>Loss-making</b>: net loss {fmt.money(abs(q), cur)} in the latest quarter ({when}){growth}")
        else:
            lines.append(f"Latest quarter ({when}): net profit {fmt.money(q, cur)}{growth}")
    annual = f.get("annual") or []
    if annual and annual[0].get("net_income") is not None:
        ni = annual[0]["net_income"]
        word = "net profit" if ni >= 0 else "net loss"
        cagr = (
            f" · profit CAGR {fmt.pct(m['ni_cagr'], False)} over {m.get('ni_cagr_years')}y"
            if m.get("ni_cagr") is not None
            else ""
        )
        lines.append(f"Full year to {fmt.quarter_label(annual[0]['period'])}: {word} {fmt.money(abs(ni), cur)}{cagr}")
    margins = [
        f"{label} {fmt.pct(m.get(key), False)}" for key, label in (("net_margin", "net margin"), ("op_margin", "operating margin"))
        if m.get(key) is not None
    ]
    if margins:
        lines.append(" · ".join(margins).capitalize())
    if not lines:
        lines.append("No profit figures available from Yahoo.")
    return lines


def profile_block(r: Research, profile: Profile | None) -> str:
    if profile is None:
        text = "<i>reading the company description…</i>"
    elif profile.text is None:
        text = "<i>Yahoo has no company description for this stock.</i>"
    else:
        text = escape(profile.text)
    return "\n".join(["<b>🏢 Business & profit</b>", text, *profit_lines(r)])


def _must(checks: list[Check]) -> str:
    return " · <i>must</i>" if any(c.rule.must for c in checks) else ""


def _passed(c: Check) -> str:
    return f"{METRICS[c.rule.metric].label} {c.note}"


def rule_lines(v: Verdict, has_thesis: bool) -> list[str]:
    """Problems first, one per line; the rules that pass fold into one line per pillar."""
    rank = {p: i for i, p in enumerate(PILLARS)}
    checks = sorted(v.checks, key=lambda c: rank.get(c.rule.pillar, len(PILLARS)))
    by = {s: [c for c in checks if c.status == s] for s in ("pass", "fail", "unknown", "na")}
    out = [f"❌ {escape(c.rule.pillar)}: {escape(c.short())}{_must([c])}" for c in sorted(by["fail"], key=lambda c: not c.rule.must)]
    judged = [c for c in by["unknown"] if c.rule.is_judgment]
    # One line when none is confirmed for the same reason (no thesis, or nothing ticked yet); by name once some are.
    none_confirmed = len(judged) == sum(c.rule.is_judgment for c in checks) and len({c.note for c in judged}) == 1
    if judged and (not has_thesis or (none_confirmed and len(judged) > 1)):
        what = "1 judgment rule" if len(judged) == 1 else f"{len(judged)} judgment rules"
        out.append(f"❓ {what} unconfirmed: {escape(judged[0].note)}{_must(judged)}")
    else:
        out += [f"❓ {escape(c.rule.pillar)}: {escape(c.rule.text)} · {escape(c.note)}{_must([c])}" for c in judged]
    unknown: dict[str, list[Check]] = {}
    for c in by["unknown"]:
        if not c.rule.is_judgment:
            unknown.setdefault(c.rule.pillar, []).append(c)
    out += [f"❓ {escape(p)}: {escape(' · '.join(c.short() for c in cs))}{_must(cs)}" for p, cs in unknown.items()]
    for note in dict.fromkeys(c.note for c in by["na"]):
        same = [c for c in by["na"] if c.note == note]
        judged = sum(c.rule.is_judgment for c in same)
        names = [METRICS[c.rule.metric].label for c in same if not c.rule.is_judgment]
        if judged:
            names.append("1 judgment rule" if judged == 1 else f"{judged} judgment rules")
        out.append(f"➖ {escape(', '.join(names))}: {escape(note)}")
    pillars: dict[str, list[Check]] = {}
    for c in by["pass"]:
        pillars.setdefault(c.rule.pillar, []).append(c)
    for pillar, passed in pillars.items():
        ticked = sum(c.rule.is_judgment for c in passed)
        items = [_passed(c) for c in passed if not c.rule.is_judgment]
        if ticked:
            items.append("1 judgment rule ticked" if ticked == 1 else f"{ticked} judgment rules ticked")
        out.append(f"✅ {escape(pillar)}: {escape(' · '.join(items))}")
    return out


def thesis_lines(c: Conviction, short: bool = False) -> list[str]:
    """The thesis; `short` keeps only the one-line thesis, for a reply that would not fit otherwise."""
    if c.thesis is None:
        if c.held:
            why = f" {escape(c.thesis_note)}." if c.thesis_note else ""
            return [f"📝 <i>No thesis in Notion yet.{why} Write it in this stock's portfolio row using templates/thesis.md.</i>"]
        return ["📝 <i>No thesis: this stock is not in your portfolio.</i>"]
    out = ["<b>📝 Your thesis</b>"]
    for key in ("thesis",) if short else ("thesis", "pillars", "breakers", "plan"):
        lines = [x for x in c.thesis.lines(key) if x.strip()][:3]
        if len(lines) == 1:
            out.append(f"<u>{TITLES[key]}</u>: {escape(_cut(lines[0], 160))}")
        elif lines:
            out.append(f"<u>{TITLES[key]}</u>")
            out += [f"• {escape(_cut(x, 140))}" for x in lines]
    if short:
        out.append("<i>The rest of your thesis is in Notion.</i>")
    if c.thesis_note:
        out.append(f"<i>{escape(c.thesis_note)}</i>")
    return out


def verdict_lines(c: Conviction) -> list[str]:
    v = c.verdict
    out = [f"{v.icon} <b>VERDICT: {v.code}</b> · blueprint v{c.locked.version}", escape(v.reason)]
    if c.conviction:
        out.append(f"Your conviction: <b>{c.conviction}</b>")
    elif c.held:
        out.append("Your conviction: not set")
    if c.mismatch:
        out.append(f"⚠️ {escape(c.mismatch)}")
    if notice := unlocked_notice(c.unlocked, c.locked):
        out.append(notice)
    return out


FUND_DISCLAIMER = "<i>Funds aren't judged by your blueprint · not investment advice · Data: Yahoo Finance</i>"


def fund_lines(r: Research) -> list[str]:
    """What Yahoo knows about a fund, by code: who runs it, size, cost, returns and the 52-week range."""
    f, m, cur = r.fundamentals, r.metrics, r.fundamentals.get("currency")
    lines = []
    if who := " · ".join(x for x in (f.get("fund_family"), f.get("category")) if x):
        lines.append(escape(who))
    size = [f"Assets {fmt.money(f['total_assets'], cur)}" if f.get("total_assets") else None,
            f"Expense ratio {f['expense_ratio_pct']:.2f}%" if f.get("expense_ratio_pct") is not None else None,
            f"Yield {f['fund_yield'] * 100:.2f}%" if f.get("fund_yield") is not None else None]
    returns = [f"YTD {fmt.pct(f['ytd_return_pct'])}" if f.get("ytd_return_pct") is not None else None,
               f"3y avg {fmt.pct(f['return_3y'] * 100)}/yr" if f.get("return_3y") is not None else None,
               f"5y avg {fmt.pct(f['return_5y'] * 100)}/yr" if f.get("return_5y") is not None else None]
    for parts in (size, returns):
        if parts := [x for x in parts if x]:
            lines.append(" · ".join(parts))
    if f.get("low_52w") and f.get("high_52w"):
        below = f" · {abs(m['from_52w_high']):.1f}% below the high" if m.get("from_52w_high") is not None else ""
        lines.append(f"52w range {fmt.price(f['low_52w'], cur)}–{fmt.price(f['high_52w'], cur)}{below}")
    return lines or ["No fund figures available from Yahoo."]


def render_fund(c: Conviction, profile: Profile | None) -> str:
    """An ETF or fund: its figures, what it holds, and the investor's thesis, but no verdict."""
    kind = (c.research.fundamentals.get("quote_type") or "fund").upper().replace("MUTUALFUND", "mutual fund")
    top = [f"⚪ <b>NOT JUDGED</b> · {escape(kind)}",
           "Your blueprint's rules are for companies (ROE, debt, profit), which a fund doesn't have, so there is no verdict."]
    if c.held:
        top.append(f"In your portfolio · your conviction: {f'<b>{c.conviction}</b>' if c.conviction else 'not set'}")
    else:
        top.append("Not in your portfolio.")
    if profile is None:
        about = "<i>reading the fund description…</i>"
    else:
        about = escape(profile.text) if profile.text else "<i>Yahoo has no description for this fund.</i>"
    parts = [
        header(c.research),
        "\n".join(top),
        "\n".join(["<b>📊 Fund</b>", *fund_lines(c.research)]),
        "\n".join(["<b>🏢 What it holds</b>", about]),
        "\n".join(thesis_lines(c)),
        FUND_DISCLAIMER,
    ]
    return clip("\n\n".join(parts), TELEGRAM_LIMIT)


def render_check(c: Conviction, profile: Profile | None, summary: str | None) -> str:
    """Verdict, rules, business, thesis, then the model's summary. Too long for Telegram: the thesis
    shrinks first, then the middle is cut, so the summary and the disclaimer always show."""
    if c.fund:
        return render_fund(c, profile)
    v = c.verdict

    def body(short: bool) -> str:
        return "\n\n".join([
            header(c.research),
            "\n".join(verdict_lines(c)),
            "\n".join([f"<b>📏 Rules</b> · {v.passed} of {v.counted} pass", *rule_lines(v, c.thesis is not None)]),
            profile_block(c.research, profile),
            "\n".join(thesis_lines(c, short)),
        ])

    tail = "\n\n".join([
        "🧠 " + (escape(summary) if summary else "<i>writing summary with the local model…</i>"),
        DISCLAIMER.format(version=c.locked.version),
    ])
    room = TELEGRAM_LIMIT - len(tail) - 2
    text = body(False)
    if len(text) > room:
        text = clip(body(True), room)
    return text + "\n\n" + tail


def clip(html: str, limit: int) -> str:
    """Cut on a line so tags stay balanced."""
    if len(html) <= limit:
        return html
    return html[: html.rfind("\n", 0, limit - 2)] + "\n…"


def summary_data(c: Conviction) -> str:
    """The extra DATA the model gets beside report.data_block(): rule results, verdict, thesis."""
    v = c.verdict
    lines = [f"BLUEPRINT v{c.locked.version} RULE RESULTS:"]
    for ch in v.checks:
        lines.append(f"  [{ch.status.upper()}] {ch.rule.text} ({ch.rule.condition()}{', must pass' if ch.rule.must else ''}): {ch.note}")
    lines.append(f"VERDICT: {v.code} — {v.reason}")
    lines.append(f"The investor {'holds' if v.held else 'does not hold'} this stock.")
    if c.thesis and not c.thesis.empty:
        lines.append("THESIS (written by the investor):")
        for key in ("thesis", "pillars", "risks", "breakers", "plan"):
            for x in c.thesis.lines(key)[:6]:
                lines.append(f"  {TITLES[key]}: {_cut(x, 200)}")
    else:
        lines.append("THESIS: none written.")
    return "\n".join(lines)


# --- /report ---

# Short metric names, so a report table stays narrow enough for a phone (about 32 characters).
SHORT = {
    "loss_years": "Loss years", "fcf_positive_years": "FCF years", "op_margin": "Op margin",
    "rev_yoy": "Revenue YoY", "ni_yoy": "Profit YoY", "rev_cagr": "Rev CAGR 3Y", "ni_cagr": "Profit CAGR 3Y",
    "dividend_yield": "Div yield", "from_52w_high": "From 52w high",
}
GRID = 4  # symbols per row when a group is only a list of symbols


def _short(c: Check) -> str:
    return "judgment" if c.rule.is_judgment else SHORT.get(c.rule.metric, METRICS[c.rule.metric].label)


def _limit(r: Rule) -> str:
    """"ROE ≥ 15.0%": a rule's limit, stated once under the table."""
    m = METRICS[r.metric]
    return f"{SHORT.get(r.metric, m.label)} {'≥' if r.op == '>=' else '≤'} {fmt_value(r.threshold, m.unit)}"


def _unsure(checks: list[Check]) -> str:
    """"4 judgment rules" or "1 judgment rule, P/E": what a HOLD waits on."""
    judged = sum(c.rule.is_judgment for c in checks)
    names = [] if not judged else ["1 judgment rule" if judged == 1 else f"{judged} judgment rules"]
    return ", ".join(names + [_short(c) for c in checks if not c.rule.is_judgment])


def table(rows: list[tuple[str, ...]], right: set[int] = frozenset()) -> list[str]:
    """Rows as aligned text; columns in `right` are right-aligned (numbers)."""
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return [
        "  ".join(cell.rjust(w) if i in right else cell.ljust(w) for i, (cell, w) in enumerate(zip(r, widths))).rstrip()
        for r in rows
    ]


def grid(symbols: list[str]) -> list[str]:
    rows = [symbols[i:i + GRID] for i in range(0, len(symbols), GRID)]
    return ["  ".join(s.ljust(6) for s in row).rstrip() for row in rows]


def pre(lines: list[str]) -> str:
    """One monospace block; kept as one piece so a message split never breaks it."""
    return "<pre>" + escape("\n".join(lines)) + "</pre>"


def _limits(rules: list[Rule]) -> str:
    return "<i>Limits: " + escape(" · ".join(_limit(r) for r in dict.fromkeys(rules))) + "</i>"


def render_report(rep: ReportResult, today: date | None = None) -> list[str]:
    """Holdings by verdict, most urgent first, each verdict as a small aligned table."""
    locked = rep.locked
    sell: dict[Rule, list[tuple]] = {}  # first rule broken -> (row, rule, symbol or '' on an 'also' row)
    review: list[tuple[str, ...]] = []
    unsure: dict[str, list[str]] = {}
    pricey: list[tuple[str, ...]] = []
    review_rules: list[Rule] = []
    add, funds, unavailable = [], [], []
    counts = dict.fromkeys(HELD_ORDER, 0)
    no_thesis, unreadable, mismatches = [], [], []
    for e in rep.entries:
        sym, c = e.holding.sec.symbol, e.conviction
        if c is None:
            unavailable.append(f"<b>{escape(sym)}</b> {escape(_cut(e.error or 'unknown error', 100))}")
            continue
        if c.fund:
            funds.append(sym)
            continue
        v, b = c.verdict, breakdown(c.verdict.checks)
        counts[v.code] += 1
        if v.code == "SELL":
            first, *rest = b.must_fail
            rows = [((sym, _short(first), first.note), first.rule, sym)]
            rows += [(("", "+" + _short(x), x.note), x.rule, "") for x in rest]
            sell.setdefault(first.rule, []).extend(rows)
        elif v.code == "REVIEW":
            fails = b.scored_fails
            review += [(sym if i == 0 else "", f"{b.passed}/{len(b.scored)}" if i == 0 else "", _short(x), x.note)
                       for i, x in enumerate(fails)]
            review_rules += [x.rule for x in fails]
        elif v.code == "HOLD" and b.value_fail:
            pricey += [(sym if i == 0 else "", _short(x), x.note) for i, x in enumerate(b.value_fail)]
        elif v.code == "HOLD":
            unsure.setdefault(f"Confirm {_unsure(b.must_unknown + b.value_unknown)}", []).append(sym)
        else:
            add.append(sym)
        if c.thesis is None:
            (unreadable if c.thesis_note else no_thesis).append(sym)
        if c.mismatch:
            mismatches.append(f"{sym} ({c.conviction}, but {v.code})")

    when = (today or date.today()).strftime("%-d %b %Y")
    icons = {"SELL": "🔴", "REVIEW": "🟠", "HOLD": "🔵", "ADD": "🟢"}
    tally = " · ".join(f"{icons[code]} {counts[code]} {code.lower()}" for code in HELD_ORDER)
    extra = (f" · ⚪ {len(funds)} funds" if funds else "") + (f" · ❔ {len(unavailable)} unavailable" if unavailable else "")
    lines = [
        f"📋 <b>Conviction report</b> · {when} · blueprint v{locked.version}",
        f"{len(rep.entries)} holdings · {tally}{extra}",
    ]
    if rep.sync_error:
        lines.append(f"⚠️ Couldn't re-read Notion, so this uses the last synced list ({escape(_cut(rep.sync_error, 100))}).")
    if notice := unlocked_notice(rep.unlocked, locked):
        lines.append(notice)
    if sell:
        groups = sorted(sell.values(), key=lambda g: -sum(1 for _, _, sym in g if sym))  # most stocks first
        rows = [row for g in groups for row, _, _ in g]
        broken = [rule for g in groups for _, rule, _ in g]
        lines += ["", "🔴 <b>SELL</b> · breaks a must-pass rule",
                  pre(table([("Stock", "Rule broken", "Value"), *rows], right={2})), _limits(broken)]
    if review:
        lines += ["", f"🟠 <b>REVIEW</b> · passes under {locked.blueprint.pass_bar:g}% of scored rules",
                  pre(table([("Stock", "Pass", "Fails", "Value"), *review], right={3})), _limits(review_rules)]
    if unsure or pricey:
        body: list[str] = []
        for why, syms in sorted(unsure.items(), key=lambda kv: -len(kv[1])):
            body += ([""] if body else []) + [why, *grid(syms)]
        if pricey:
            body += ([""] if body else []) + ["Too expensive to add", *table(pricey, right={2})]
        lines += ["", "🔵 <b>HOLD</b>", pre(body)]
    if add:
        lines += ["", "🟢 <b>ADD</b> · passes every rule", pre(grid(add))]
    if funds:
        lines += ["", "⚪ <b>Not judged</b> · ETFs and funds", pre(grid(funds))]
    if unavailable:
        lines += ["", "❔ <b>Unavailable</b>", *unavailable]
    lines.append("")
    if no_thesis:
        lines.append("📝 No thesis yet: " + escape(", ".join(no_thesis)))
    if unreadable:
        lines.append("⚠️ Thesis page unreadable (share it with your Notion integration): " + escape(", ".join(unreadable)))
    if mismatches:
        lines.append("⚠️ Conviction mismatch: " + escape("; ".join(mismatches)))
    lines.append("<i>/why TICKER for details · your own rules, not investment advice</i>")
    return split_messages(lines)


# --- /blueprint ---

def rule_line(r: Rule) -> str:
    star = "⭐ " if r.must else ""
    return f"• {star}{escape(r.text)} — <i>{escape(r.condition())}</i>"


def blueprint_lines(bp: Blueprint) -> list[str]:
    lines = []
    for p in PILLARS + sorted({r.pillar for r in bp.rules} - set(PILLARS)):
        rules = [r for r in bp.rules if r.pillar == p]
        if rules:
            lines.append(f"<b>{escape(p)}</b>")
            lines += [rule_line(r) for r in rules]
    lines.append(f"Scored rules pass bar: <b>{bp.pass_bar:g}%</b>")
    return lines


def checks_block(bp: Blueprint) -> list[str]:
    if not bp.judgment_rules:
        return []
    body = "\n".join(["## Blueprint checks", *(f"- [ ] {r.text}" for r in bp.judgment_rules)])
    return ["Paste this into each holding's page in Notion and tick what is true:", f"<pre>{escape(body)}</pre>"]


def diff_lines(d: Diff) -> list[str]:
    out = []
    for old, new in d.changed:
        out.append(f"• changed: {escape(new.text)} — <i>{escape(old.condition())}{' (must)' if old.must else ''}"
                   f" → {escape(new.condition())}{' (must)' if new.must else ''}</i>")
    out += [f"• added: {escape(r.text)} — <i>{escape(r.condition())}</i>" for r in d.added]
    out += [f"• removed: {escape(r.text)}" for r in d.removed]
    if d.pass_bar:
        out.append(f"• pass bar: {d.pass_bar[0]:g}% → {d.pass_bar[1]:g}%")
    return out


def problem_lines(parsed: Parsed) -> list[str]:
    if not parsed.problems:
        return []
    return ["⚠️ <b>Rows I can't use</b> (fix them in Notion):", *(f"• {escape(p)}" for p in parsed.problems)]


def render_blueprint(st: BlueprintStatus) -> list[str]:
    if st.locked is None:
        if st.parsed is None:
            return split_messages([SETUP, *([f"\n⚠️ {escape(st.error)}"] if st.error else [])])
        lines = ["📘 <b>Your blueprint in Notion</b> · <i>not locked yet</i>", *blueprint_lines(st.parsed.blueprint)]
        lines += problem_lines(st.parsed)
        lines.append("\nWhen it looks right, send /blueprint lock. Stocks are only judged by a locked version.")
        return split_messages(lines)
    lines = [f"📘 <b>Your conviction blueprint</b> · {_version_line(st.locked)}",
             "<i>⭐ = must pass</i>", *blueprint_lines(st.locked.blueprint)]
    if st.error:
        lines.append(f"\n<i>Couldn't compare with Notion: {escape(st.error)}</i>")
    elif st.diff:
        lines += ["", unlocked_notice(st.diff, st.locked), *diff_lines(st.diff)]
    if st.parsed:
        lines += problem_lines(st.parsed)
    lines += ["", *checks_block(st.locked.blueprint)]
    return split_messages(lines)


def render_lock(res: LockResult) -> list[str]:
    st = res.status
    if st.error:
        return render_blueprint(st)
    if st.parsed and st.parsed.problems:
        return split_messages(["🔒 <b>Not locked.</b> Fix these rows in Notion first:", *problem_lines(st.parsed)[1:]])
    if st.parsed and not st.parsed.blueprint.rules:
        return ["🔒 <b>Not locked.</b> The Notion blueprint has no rules yet."]
    if res.unchanged:
        return [f"🔒 Nothing changed in Notion since v{st.locked.version}; it stays locked."]
    lines = [f"🔒 <b>Locked blueprint v{res.version}.</b> Every verdict now uses it."]
    if st.locked and st.diff:
        lines += [f"Changes from v{st.locked.version}: {escape(st.diff.summary())}", *diff_lines(st.diff)]
    else:
        lines += blueprint_lines(st.parsed.blueprint)
    lines += ["", *checks_block(st.parsed.blueprint)]
    return split_messages(lines)


def to_plain(html: str) -> str:
    """For the CLI."""
    import html as h

    html = re.sub(r'<a href="([^"]*)">(.*?)</a>', r"\2 (\1)", html)
    return h.unescape(re.sub(r"<[^>]+>", "", html))
