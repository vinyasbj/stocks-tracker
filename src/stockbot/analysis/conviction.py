"""Results of the conviction features and their Telegram rendering (HTML): /check, /report, /blueprint."""

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from html import escape

from ..models import Holding, Security
from . import fmt
from .blueprint import PILLARS, Blueprint, Diff, Locked, Parsed, Rule
from .report import Research, header, statement_currency
from .thesis import TITLES, Thesis
from .verdict import HELD_ORDER, Verdict

DISCLAIMER = (
    "<i>The verdict applies your own blueprint v{version}; it is not the bot's advice. "
    "Data: Yahoo Finance, NSE/SEC filings, Google News.</i>"
)
NO_SPLIT = "A split of profit by segment or country is not available from Yahoo."
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


# --- /check ---

def profit_lines(r: Research) -> list[str]:
    """Company-level profit, computed by code (never by the model)."""
    m, f = r.metrics, r.fundamentals
    cur = statement_currency(f)
    lines = []
    q = m.get("q_net_income")
    if q is not None:
        when = fmt.quarter_label(m.get("quarter")) + (" — out of date" if m.get("quarter_stale") else "")
        growth = f" · YoY {fmt.pct(m.get('ni_yoy'))} · QoQ {fmt.pct(m.get('ni_qoq'))}"
        if q < 0:
            lines.append(f"• <b>Loss-making</b>: net loss {fmt.money(abs(q), cur)} in the latest quarter ({when}){growth}")
        else:
            lines.append(f"• Latest quarter ({when}): net profit {fmt.money(q, cur)}{growth}")
    annual = f.get("annual") or []
    if annual and annual[0].get("net_income") is not None:
        ni = annual[0]["net_income"]
        word = "net profit" if ni >= 0 else "net loss"
        cagr = (
            f" · profit CAGR {fmt.pct(m['ni_cagr'], False)} over {m.get('ni_cagr_years')}y"
            if m.get("ni_cagr") is not None
            else ""
        )
        lines.append(f"• Full year to {fmt.quarter_label(annual[0]['period'])}: {word} {fmt.money(abs(ni), cur)}{cagr}")
    margins = [
        f"{label} {fmt.pct(m.get(key), False)}" for key, label in (("net_margin", "net margin"), ("op_margin", "operating margin"))
        if m.get(key) is not None
    ]
    if margins:
        lines.append("• " + " · ".join(margins).capitalize())
    if not lines:
        lines.append("• No profit figures available from Yahoo.")
    return lines


def profile_block(r: Research, profile: Profile | None) -> str:
    country = r.fundamentals.get("country")
    title = "<b>🏢 How and where it makes money</b>" + (f" · {escape(country)}" if country else "")
    if profile is None:
        text = "<i>reading the company description…</i>"
    elif profile.text is None:
        text = "<i>Yahoo has no company description for this stock.</i>"
    else:
        label = "summarised from Yahoo's company description" if profile.source == "model" else "Yahoo's company description"
        text = f"{escape(profile.text)}\n<i>({label})</i>"
    return "\n".join([title, text, "<b>Profit</b> (whole company)", *profit_lines(r), f"<i>{NO_SPLIT}</i>"])


def rule_lines(v: Verdict) -> list[str]:
    by_pillar: dict[str, list[str]] = {}
    for c in v.checks:
        r = c.rule
        must = " · <i>must</i>" if r.must else ""
        detail = c.note if r.is_judgment or c.status in ("unknown", "na") else f"{c.note}, rule {r.condition()}"
        by_pillar.setdefault(r.pillar, []).append(f"{c.icon} {escape(r.text)}{must} — {escape(detail)}")
    order = PILLARS + sorted(p for p in by_pillar if p not in PILLARS)
    out = []
    for p in order:
        if p in by_pillar:
            out.append(f"<b>{escape(p)}</b>")
            out += by_pillar[p]
    return out


def thesis_lines(c: Conviction) -> list[str]:
    if c.thesis is None:
        if c.held:
            why = c.thesis_note or "no thesis in Notion"
            return ["<b>📝 Your thesis</b>", f"<i>None yet ({escape(why)}). Write it in this stock's portfolio row in "
                    "Notion using templates/thesis.md.</i>"]
        return ["<b>📝 Your thesis</b>", "<i>None: this stock is not in your portfolio. Once you buy it, write the "
                "thesis in its portfolio row in Notion using templates/thesis.md.</i>"]
    out = ["<b>📝 Your thesis</b>"]
    for key in ("thesis", "pillars", "breakers", "plan"):
        lines = [x for x in c.thesis.lines(key) if x.strip()][:5]
        if not lines:
            continue
        if len(lines) == 1:
            out.append(f"<u>{TITLES[key]}</u>: {escape(_cut(lines[0]))}")
        else:
            out.append(f"<u>{TITLES[key]}</u>")
            out += [f"• {escape(_cut(x, 160))}" for x in lines]
    if c.thesis_note:
        out.append(f"<i>{escape(c.thesis_note)}</i>")
    return out


def render_check(c: Conviction, profile: Profile | None, summary: str | None) -> str:
    v, locked = c.verdict, c.locked
    top = [f"{v.icon} <b>{v.code}</b> — {escape(v.reason)}"]
    if c.conviction:
        top.append(f"Your conviction: <b>{c.conviction}</b>")
    if c.mismatch:
        top.append(f"⚠️ {escape(c.mismatch)}")
    top.append(f"<i>Blueprint {_version_line(locked)} · {v.passed} of {v.counted} rules pass</i>")
    if notice := unlocked_notice(c.unlocked, locked):
        top.append(notice)
    parts = [
        header(c.research),
        "\n".join(top),
        profile_block(c.research, profile),
        "\n".join(["<b>📏 Your blueprint</b>", *rule_lines(v)]),
        "\n".join(thesis_lines(c)),
        "<b>🧠 Summary</b>\n" + (escape(summary) if summary else "<i>writing summary with the local model…</i>"),
        DISCLAIMER.format(version=locked.version),
    ]
    return clip_check("\n\n".join(parts))


def clip_check(html: str) -> str:
    """Telegram's limit; cut on a line so tags stay balanced (the rules and verdict come first)."""
    if len(html) <= TELEGRAM_LIMIT:
        return html
    cut = html.rfind("\n", 0, TELEGRAM_LIMIT - 2)
    return html[:cut] + "\n…"


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

def render_report(rep: ReportResult, today: date | None = None) -> list[str]:
    locked = rep.locked
    entries = rep.entries
    groups: dict[str, list[str]] = {code: [] for code in HELD_ORDER}
    unavailable, no_thesis, mismatches = [], [], []
    for e in entries:
        sym = escape(e.holding.sec.symbol)
        if e.conviction is None:
            unavailable.append(f"• <b>{sym}</b> data unavailable: {escape(_cut(e.error or 'unknown error', 120))}")
            continue
        c, v = e.conviction, e.conviction.verdict
        groups[v.code].append(f"• <b>{sym}</b> {v.passed}/{v.counted} · {escape(v.brief)}")
        if c.thesis is None:
            no_thesis.append(e.holding.sec.symbol)
        if c.mismatch:
            mismatches.append(f"{e.holding.sec.symbol} ({c.conviction}, but {v.code})")
    counts = " · ".join(f"{len(groups[code])} {code.lower()}" for code in HELD_ORDER)
    when = (today or date.today()).strftime("%-d %b %Y")
    lines = [
        f"📋 <b>Conviction report</b> · {when} · blueprint {_version_line(locked)}",
        f"{len(entries)} holdings: {counts}" + (f" · {len(unavailable)} unavailable" if unavailable else ""),
    ]
    if notice := unlocked_notice(rep.unlocked, locked):
        lines.append(notice)
    icons = {"SELL": "🔴", "REVIEW": "🟠", "HOLD": "🔵", "ADD": "🟢"}
    for code in HELD_ORDER:
        if groups[code]:
            lines += ["", f"{icons[code]} <b>{code}</b>", *groups[code]]
    if unavailable:
        lines += ["", "⚪ <b>Unavailable</b>", *unavailable]
    lines.append("")
    if no_thesis:
        lines.append("📝 No thesis yet: " + escape(", ".join(no_thesis)))
    if mismatches:
        lines.append("⚠️ Conviction mismatch: " + escape("; ".join(mismatches)))
    lines.append("<i>/check TICKER for the details. Verdicts apply your own blueprint; they are not advice.</i>")
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
