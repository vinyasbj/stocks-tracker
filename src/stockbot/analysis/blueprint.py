"""The master conviction blueprint: the investor's own rules, applied the same way to every stock.

Rules are edited in a Notion database, but stocks are judged only by a version the investor has locked
(see Store.lock_blueprint), so editing Notion changes nothing until they lock again.
"""

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Callable

from ..data import notion

JUDGMENT = "judgment"
PILLARS = ["Business", "Quality", "Growth", "Safety", "Valuation"]
VALUATION = "Valuation"
SETTINGS = "Settings"
DEFAULT_PASS_BAR = 75.0


@dataclass(frozen=True)
class Metric:
    label: str
    unit: str  # "%", "x" or "years"
    get: Callable[[dict, dict], float | None]  # (metrics, fundamentals) -> value
    aliases: tuple[str, ...] = ()
    not_for_financials: bool = False  # banks run on leverage; these ratios mean something else there


def _m(key: str) -> Callable[[dict, dict], float | None]:
    return lambda m, f: m.get(key)


def _f(key: str) -> Callable[[dict, dict], float | None]:
    return lambda m, f: f.get(key)


METRICS: dict[str, Metric] = {
    "roe": Metric("ROE", "%", _m("roe"), ("return on equity",)),
    "net_margin": Metric("Net margin", "%", _m("net_margin"), ("net profit margin", "profit margin")),
    "op_margin": Metric("Operating margin", "%", _m("op_margin"), ("operating profit margin", "ebit margin")),
    "debt_to_equity": Metric("Debt/Equity", "x", _m("debt_to_equity"), ("de", "d e", "debt equity", "debt to equity"), True),
    "current_ratio": Metric("Current ratio", "x", _m("current_ratio"), (), True),
    "pe": Metric("P/E", "x", _m("pe"), ("pe", "p e", "pe ratio", "price to earnings", "trailing pe")),
    "forward_pe": Metric("Forward P/E", "x", _f("forward_pe"), ("forward pe", "fwd pe")),
    "pb": Metric("P/B", "x", _f("pb"), ("pb", "p b", "price to book")),
    "rev_yoy": Metric("Revenue growth YoY", "%", _m("rev_yoy"), ("sales growth yoy", "revenue yoy")),
    "ni_yoy": Metric("Profit growth YoY", "%", _m("ni_yoy"), ("net profit growth yoy", "profit yoy", "earnings growth yoy")),
    "rev_cagr": Metric("Revenue CAGR 3Y", "%", _m("rev_cagr"), ("revenue cagr", "sales cagr", "sales cagr 3y")),
    "ni_cagr": Metric("Profit CAGR 3Y", "%", _m("ni_cagr"), ("profit cagr", "net profit cagr", "earnings cagr")),
    "fcf_positive_years": Metric(
        "FCF positive years", "years", _m("fcf_positive_years"), ("positive fcf years", "free cash flow positive years"), True
    ),
    "loss_years": Metric(
        "Consecutive loss years", "years", _m("loss_years"), ("loss years", "loss making years", "years of losses")
    ),
    "dividend_yield": Metric("Dividend yield", "%", _f("dividend_yield"), ("dividend",)),
    "from_52w_high": Metric("From 52w high", "%", _m("from_52w_high"), ("from 52 week high", "below 52w high")),
}
JUDGMENT_ALIASES = {"judgment", "judgement", "manual", "qualitative", "my judgment", "my call", "your call"}


def norm(s: str) -> str:
    """Lower case, punctuation as spaces, so "Debt/Equity" and "debt equity" compare equal."""
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def metric_key(text: str | None) -> str | None:
    """The metric a Notion "Metric" cell names; JUDGMENT when empty or a judgment word; None if unknown."""
    t = norm(text or "")
    if not t or t in JUDGMENT_ALIASES:
        return JUDGMENT
    for key, m in METRICS.items():
        if t in {norm(key), norm(m.label), *(norm(a) for a in m.aliases)}:
            return key
    return None


def fmt_value(v: float | None, unit: str) -> str:
    if v is None:
        return "n/a"
    if unit == "%":
        return f"{v:.1f}%"
    if unit == "years":
        n = f"{v:g}"
        return f"{n} year" if n == "1" else f"{n} years"
    return f"{v:.2f}" if abs(v) < 10 else f"{v:.1f}"


@dataclass(frozen=True)
class Rule:
    text: str
    pillar: str
    metric: str  # a METRICS key or JUDGMENT
    op: str  # ">=" or "<="; "" for judgment rules
    threshold: float | None
    must: bool

    @property
    def is_judgment(self) -> bool:
        return self.metric == JUDGMENT

    def condition(self) -> str:
        """"ROE ≥ 15.0%", or "your judgment"."""
        if self.is_judgment:
            return "your judgment"
        m = METRICS[self.metric]
        return f"{m.label} {'≥' if self.op == '>=' else '≤'} {fmt_value(self.threshold, m.unit)}"


@dataclass
class Blueprint:
    rules: list[Rule]
    pass_bar: float = DEFAULT_PASS_BAR  # % of known scored rules a stock must pass

    @property
    def judgment_rules(self) -> list[Rule]:
        return [r for r in self.rules if r.is_judgment]

    def to_json(self) -> dict:
        return {"rules": [asdict(r) for r in sort_rules(self.rules)], "pass_bar": self.pass_bar}

    @classmethod
    def from_json(cls, data: dict) -> "Blueprint":
        return cls([Rule(**r) for r in data["rules"]], data.get("pass_bar", DEFAULT_PASS_BAR))

    def fingerprint(self) -> str:
        """Same rules and bar, same fingerprint, whatever order Notion returns the rows in."""
        return hashlib.sha256(json.dumps(self.to_json(), sort_keys=True).encode()).hexdigest()[:16]


@dataclass
class Locked:
    version: int
    locked_at: float  # unix time
    blueprint: Blueprint


# --- reading the Notion blueprint database ---

COLUMNS = {
    "rule": ["rule", "criterion", "criteria", "check", "name"],
    "pillar": ["pillar", "category", "area", "section"],
    "metric": ["metric", "measure", "ratio"],
    "condition": ["condition", "operator", "op", "comparison"],
    "threshold": ["threshold", "value", "target", "limit"],
    "must": ["must pass", "must", "mandatory", "required", "hard rule", "deal breaker"],
}
# Only "or equal" wordings: "> 15" or "above 15" is a different rule, so it is reported, never mapped to ≥.
OPS = {
    ">=": [">=", "≥", "at least", "min", "minimum"],
    "<=": ["<=", "≤", "at most", "max", "maximum"],
}
TRUE = {"yes", "y", "true", "1", "must", "✓", "✔", "x"}
PASS_BAR_SETTING = "scored rules pass bar"


def _op(text) -> str | None:
    t = str(text or "").strip().lower()
    return next((op for op, words in OPS.items() if t in words), None)


def _pillar(text) -> str | None:
    """One of the five pillars or Settings, in its proper case; None for anything else."""
    t = str(text or "").strip()
    return next((p for p in [*PILLARS, SETTINGS] if p.lower() == t.lower()), None)


def _must(v) -> bool:
    return v if isinstance(v, bool) else str(v or "").strip().lower() in TRUE


@dataclass
class Parsed:
    blueprint: Blueprint
    problems: list[str] = field(default_factory=list)  # one line per row that can't be used


def parse_rules(pages: list[dict]) -> Parsed:
    """The blueprint in the Notion database's rows. Rows that can't be used are reported, never guessed."""
    rules: list[Rule] = []
    problems: list[str] = []
    pass_bar = DEFAULT_PASS_BAR
    for page in pages:
        if page.get("in_trash") or page.get("archived"):
            continue
        props = page.get("properties", {})
        cols = notion.find_columns(props, COLUMNS)
        if cols["rule"] is None:
            cols["rule"] = next((n for n, p in props.items() if p.get("type") == "title"), None)

        def get(name):
            col = cols.get(name)
            return notion.value(props[col]) if col and col in props else None

        text = " ".join(str(get("rule") or "").split())
        if not text:
            continue  # empty row
        pillar = _pillar(get("pillar"))
        if pillar is None:
            problems.append(f"{text}: pillar must be {', '.join(PILLARS)} or {SETTINGS}, not {get('pillar')!r}")
            continue
        threshold = notion._number(get("threshold"))

        if pillar == SETTINGS:
            if PASS_BAR_SETTING not in norm(text):
                problems.append(f"{text}: unknown setting (the only setting is \"Scored rules pass bar\")")
            elif threshold is None or not 0 < threshold <= 100:
                problems.append(f"{text}: threshold must be a percentage between 1 and 100")
            else:
                pass_bar = threshold
            continue

        key = metric_key(get("metric"))
        if key is None:
            problems.append(f"{text}: unknown metric {get('metric')!r}")
            continue
        if key == JUDGMENT:
            rules.append(Rule(text, pillar, JUDGMENT, "", None, _must(get("must"))))
            continue
        op = _op(get("condition"))
        if op is None:
            problems.append(f"{text}: condition must be ≥ or ≤, not {get('condition')!r}")
            continue
        if threshold is None:
            problems.append(f"{text}: threshold is missing")
            continue
        if METRICS[key].unit == "%" and 0 < abs(threshold) < 1:
            problems.append(
                f"{text}: threshold {threshold:g} looks like a fraction; write 15 for 15% in a plain number column"
            )
            continue
        rules.append(Rule(text, pillar, key, op, threshold, _must(get("must"))))
    return Parsed(Blueprint(sort_rules(rules), pass_bar), problems)


def sort_rules(rules: list[Rule]) -> list[Rule]:
    """By pillar (in PILLARS order), must-pass first, then by text."""
    order = {p: i for i, p in enumerate(PILLARS)}
    return sorted(rules, key=lambda r: (order.get(r.pillar, len(PILLARS)), r.pillar, not r.must, r.text.lower()))


@dataclass
class Diff:
    added: list[Rule]
    removed: list[Rule]
    changed: list[tuple[Rule, Rule]]  # (locked, notion)
    pass_bar: tuple[float, float] | None = None  # (locked, notion) when it changed

    def __bool__(self) -> bool:
        return bool(self.added or self.removed or self.changed or self.pass_bar)

    def summary(self) -> str:
        """"1 changed, 1 added"."""
        parts = [
            f"{len(items)} {word}"
            for items, word in ((self.changed, "changed"), (self.added, "added"), (self.removed, "removed"))
            if items
        ]
        if self.pass_bar:
            parts.append(f"pass bar {self.pass_bar[0]:g}% → {self.pass_bar[1]:g}%")
        return ", ".join(parts)


def diff(locked: Blueprint, current: Blueprint) -> Diff:
    """Rules are matched by their text, ignoring case and punctuation."""
    old = {norm(r.text): r for r in locked.rules}
    new = {norm(r.text): r for r in current.rules}
    return Diff(
        added=[r for k, r in new.items() if k not in old],
        removed=[r for k, r in old.items() if k not in new],
        changed=[(old[k], new[k]) for k in old if k in new and old[k] != new[k]],
        pass_bar=(locked.pass_bar, current.pass_bar) if locked.pass_bar != current.pass_bar else None,
    )
