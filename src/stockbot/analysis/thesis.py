"""The investor's thesis for a stock: the body of its row in the Notion portfolio, read into sections.

The template (templates/thesis.md) uses these headings; they are matched ignoring case, punctuation and
anything in parentheses, so "THESIS BREAKERS (sell or re-think if)" is the Thesis breakers section.
"""

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from ..data import notion

SECTIONS = {
    "thesis": ["one line thesis", "thesis", "one liner"],
    "pillars": ["why i own it", "pillars", "why i own"],
    "entry": ["entry"],
    "risks": ["risks i accept", "risks"],
    "breakers": ["thesis breakers", "breakers", "sell if", "sell or re think if"],
    "plan": ["action plan", "plan"],
    "review": ["review log", "reviews"],
    "log": ["decision log", "decisions"],
    "checks": ["blueprint checks", "checks"],
}
TITLES = {
    "thesis": "One-line thesis", "pillars": "Why I own it", "entry": "Entry", "risks": "Risks I accept",
    "breakers": "Thesis breakers", "plan": "Action plan", "review": "Review log", "log": "Decision log",
    "checks": "Blueprint checks",
}
HEADINGS = {"heading_1", "heading_2", "heading_3"}
MATCH = 85  # how close a tick box's text must be to a judgment rule's


def _norm(s: str) -> str:
    s = re.sub(r"\([^)]*\)", " ", s or "")  # "(sell or re-think if)"
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


SECTION_OF = {_norm(name): key for key, names in SECTIONS.items() for name in names}


def block_text(block: dict) -> str:
    body = block.get(block.get("type", ""), {}) or {}
    return "".join(x.get("plain_text", "") for x in body.get("rich_text", []) or []).strip()


@dataclass
class Thesis:
    page_id: str
    sections: dict[str, list[str]] = field(default_factory=dict)  # section key -> lines; "header" before any heading
    todos: list[tuple[str, bool]] = field(default_factory=list)  # (text, ticked), anywhere on the page
    conviction: str | None = None
    started: str | None = None

    @property
    def empty(self) -> bool:
        return not any(self.sections.values()) and not self.todos

    def lines(self, key: str) -> list[str]:
        return self.sections.get(key, [])

    def answer(self, rule_text: str) -> bool | None:
        """Whether the page ticks the tick box for this judgment rule: True, False (unticked), None (no tick box)."""
        target = _norm(rule_text)
        best_score, best = 0.0, None
        for text, done in self.todos:
            t = _norm(text)
            score = max(fuzz.ratio(target, t), fuzz.token_sort_ratio(target, t))
            if score > best_score:
                best_score, best = score, done
        return best if best_score >= MATCH else None


def parse(page_id: str, blocks: list[dict]) -> Thesis:
    th = Thesis(page_id)
    current = "header"
    for block in blocks:
        kind, text = block.get("type"), block_text(block)
        if not text:
            continue
        key = SECTION_OF.get(_norm(text))
        if key and (kind in HEADINGS or kind == "paragraph"):
            current = key
            th.sections.setdefault(key, [])
            continue
        if kind == "to_do":
            th.todos.append((text, bool(block["to_do"].get("checked"))))
        # An unrecognised heading stays with the section before it, as an ordinary line.
        th.sections.setdefault(current, []).append(text)
    everything = "\n".join(line for lines in th.sections.values() for line in lines)
    if m := re.search(r"conviction\s*:\s*([a-z]+)", everything, re.IGNORECASE):
        th.conviction = notion.conviction_level(m.group(1))
    if m := re.search(r"started\s*:\s*(\d{4}-\d{2}-\d{2})", everything, re.IGNORECASE):
        th.started = m.group(1)
    return th
