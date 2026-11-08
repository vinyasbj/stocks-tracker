"""Judging one stock against the locked blueprint: every rule to pass/fail/unknown/n.a., then a verdict.

Pure functions over plain data (no network, no model), so the same blueprint and the same numbers always
give the same verdict.
"""

from dataclasses import dataclass

from . import signals
from .blueprint import METRICS, VALUATION, Rule, fmt_value
from .thesis import Thesis

STALE_SENSITIVE = ("rev_yoy", "ni_yoy")  # describe the latest quarter, which may be out of date


@dataclass(frozen=True)
class Check:
    rule: Rule
    status: str  # "pass" | "fail" | "unknown" | "na"
    value: float | None = None
    note: str = ""  # the value as shown, or why there is none

    @property
    def icon(self) -> str:
        return {"pass": "✅", "fail": "❌", "unknown": "❓", "na": "➖"}[self.status]

    def short(self) -> str:
        """A few words for reasons and the report: "ROE 13.8% (need ≥ 15.0%)"."""
        if self.rule.is_judgment:
            return self.rule.text
        m = METRICS[self.rule.metric]
        if self.status == "unknown":
            return f"{m.label} n/a ({self.note})" if self.note and self.note != "no data" else f"{m.label} n/a"
        need = f"{'≥' if self.rule.op == '>=' else '≤'} {fmt_value(self.rule.threshold, m.unit)}"
        return f"{m.label} {self.note} (need {need})"


TO_CONFIRM = "yours to confirm before buying"


def evaluate(
    rules: list[Rule], m: dict, f: dict, thesis: Thesis | None = None, no_thesis: str = "no thesis in Notion",
    held: bool = True,
) -> list[Check]:
    """`m` is metrics.compute() output, `f` the fundamentals. `no_thesis` explains a missing thesis.

    A stock you don't own has no thesis to tick, so its judgment rules don't count for or against it:
    they are left for you to confirm before buying, and the BUY reason says so.
    """
    fin = signals.is_financial(f)
    stale = bool(m.get("quarter_stale"))
    out = []
    for r in rules:
        if r.is_judgment and not held:
            out.append(Check(r, "na", note=TO_CONFIRM))
            continue
        if r.is_judgment:
            ans = thesis.answer(r.text) if thesis else None
            if ans:
                out.append(Check(r, "pass", note="ticked in Notion"))
            elif thesis is None:
                out.append(Check(r, "unknown", note=no_thesis))
            else:
                out.append(Check(r, "unknown", note="not ticked in Notion" if ans is False else "no tick box for it in Notion"))
            continue
        metric = METRICS[r.metric]
        if metric.not_for_financials and fin:
            out.append(Check(r, "na", note="not meaningful for banks and financials"))
            continue
        if stale and r.metric in STALE_SENSITIVE:
            out.append(Check(r, "unknown", note="Yahoo's latest quarter is out of date"))
            continue
        v = metric.get(m, f)
        if r.metric == "pe" and ((v is not None and v <= 0) or (v is None and (m.get("net_margin") or 0) < 0)):
            # Yahoo leaves P/E empty when earnings are negative; a P/E limit is not met then.
            out.append(Check(r, "fail", v, "negative earnings"))
            continue
        if v is None:
            out.append(Check(r, "unknown", note="no data"))
            continue
        ok = v >= r.threshold if r.op == ">=" else v <= r.threshold
        note = fmt_value(v, metric.unit)
        if r.metric == "fcf_positive_years" and m.get("fcf_years"):
            note = f"{v:g} of {m['fcf_years']} years"
        elif r.metric in ("rev_cagr", "ni_cagr") and m.get(f"{r.metric}_years") not in (None, 3):
            note += f" over {m[f'{r.metric}_years']}y"
        out.append(Check(r, "pass" if ok else "fail", v, note))
    return out


# --- the verdict ---

NOT_HELD = {"BUY": "🟢", "WATCH": "🟡", "AVOID": "🔴"}
HELD = {"ADD": "🟢", "HOLD": "🔵", "REVIEW": "🟠", "SELL": "🔴"}
HELD_ORDER = ["SELL", "REVIEW", "HOLD", "ADD"]  # most urgent first


@dataclass
class Verdict:
    code: str
    reason: str
    checks: list[Check]
    held: bool
    brief: str = ""  # the deciding rules in a few words, for one-line reports

    @property
    def icon(self) -> str:
        return (HELD if self.held else NOT_HELD)[self.code]

    @property
    def passed(self) -> int:
        return sum(c.status == "pass" for c in self.checks)

    @property
    def counted(self) -> int:
        """Rules that apply to this stock (not-applicable ones excluded)."""
        return sum(c.status != "na" for c in self.checks)


def _names(checks: list[Check], limit: int = 3) -> str:
    """Up to `limit` rules by name; two or more judgment rules are counted, not spelled out."""
    judged = [c for c in checks if c.rule.is_judgment]
    names = [c.short() for c in checks if not c.rule.is_judgment or len(judged) == 1]
    if len(judged) > 1:
        names.append(f"{len(judged)} judgment rules")
    more = f" +{len(names) - limit} more" if len(names) > limit else ""
    return "; ".join(names[:limit]) + more


@dataclass
class Breakdown:
    """The checks grouped the way the verdict reads them."""
    must_fail: list[Check]  # must-pass rules outside Valuation that fail
    must_unknown: list[Check]  # ... that can't be confirmed
    scored: list[Check]  # the other rules outside Valuation with a result
    value_fail: list[Check]
    value_unknown: list[Check]

    @property
    def passed(self) -> int:
        return sum(c.status == "pass" for c in self.scored)

    @property
    def scored_fails(self) -> list[Check]:
        return [c for c in self.scored if c.status == "fail"]


def breakdown(checks: list[Check]) -> Breakdown:
    core = [c for c in checks if c.rule.pillar != VALUATION and c.status != "na"]
    value = [c for c in checks if c.rule.pillar == VALUATION and c.status != "na"]
    return Breakdown(
        [c for c in core if c.rule.must and c.status == "fail"],
        [c for c in core if c.rule.must and c.status == "unknown"],
        [c for c in core if not c.rule.must and c.status in ("pass", "fail")],
        [c for c in value if c.status == "fail"],
        [c for c in value if c.status == "unknown"],
    )


def decide(checks: list[Check], held: bool, pass_bar: float) -> Verdict:
    """BUY/WATCH/AVOID for a stock not in the portfolio; ADD/HOLD/REVIEW/SELL for a holding.

    Buying needs every must-pass rule confirmed; selling needs a must-pass rule actually failing, so an
    unknown never causes SELL or REVIEW. Valuation rules only decide whether the price is right.
    """
    b = breakdown(checks)
    must_fail, must_unknown, scored = b.must_fail, b.must_unknown, b.scored
    passed, scored_fails = b.passed, b.scored_fails
    value_fail, value_unknown = b.value_fail, b.value_unknown
    below_bar = bool(scored) and passed / len(scored) * 100 < pass_bar
    score = f"passes only {passed} of {len(scored)} scored rules (needs {pass_bar:g}%)"

    def v(code: str, reason: str, brief: str) -> Verdict:
        return Verdict(code, reason, checks, held, brief)

    everything = "Passes every rule in your blueprint, valuation included"
    low = f"{passed}/{len(scored)} scored rules · {_names(scored_fails)}"
    if held:
        if must_fail:
            return v("SELL", f"Breaks a must-pass rule: {_names(must_fail)}", _names(must_fail))
        if below_bar:
            return v("REVIEW", f"Weakening: {score}. Fails: {_names(scored_fails)}", low)
        if value_fail:
            return v("HOLD", f"Still fits, but too expensive to add: {_names(value_fail)}", f"too expensive to add: {_names(value_fail)}")
        if must_unknown or value_unknown:
            unsure = _names(must_unknown + value_unknown)
            return v("HOLD", f"Fits so far; confirm: {unsure}", f"confirm: {unsure}")
        return v("ADD", everything, "passes every rule")
    if must_fail:
        return v("AVOID", f"Fails a must-pass rule: {_names(must_fail)}", _names(must_fail))
    if below_bar:
        return v("AVOID", f"Not good enough: {score}. Fails: {_names(scored_fails)}", low)
    if must_unknown:
        return v("WATCH", f"Numbers fit so far; before buying, confirm: {_names(must_unknown)}", f"confirm: {_names(must_unknown)}")
    if value_fail:
        return v("WATCH", f"Good business by your rules, but the price fails: {_names(value_fail)}", _names(value_fail))
    if value_unknown:
        return v("WATCH", f"Business fits, but valuation can't be checked: {_names(value_unknown)}", _names(value_unknown))
    to_confirm = [c for c in checks if c.rule.is_judgment and c.status == "na"]
    if to_confirm:
        n = "1 judgment rule" if len(to_confirm) == 1 else f"{len(to_confirm)} judgment rules"
        return v("BUY", f"Numbers pass every rule, valuation included; before buying, confirm {n}", f"confirm {n}")
    return v("BUY", everything, "passes every rule")


def mismatch(conviction: str | None, verdict: Verdict) -> str | None:
    """When the investor's own conviction level disagrees with the blueprint's verdict."""
    if conviction == "HIGH" and verdict.code in ("SELL", "REVIEW", "AVOID"):
        return f"Your conviction is HIGH, but the blueprint says {verdict.code}"
    if conviction == "LOW" and verdict.code in ("ADD", "BUY"):
        return f"Your conviction is LOW, but the blueprint says {verdict.code}"
    return None
