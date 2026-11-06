import json
from pathlib import Path

from stockbot.analysis import thesis

FIX = Path(__file__).parent / "fixtures"


def googl():
    return thesis.parse("page1", json.loads((FIX / "notion_thesis_blocks.json").read_text())["results"])


def test_sections_from_heading_blocks_and_uppercase_paragraphs():
    th = googl()
    assert th.lines("thesis") == ["Search is still a cash machine, and Cloud plus AI are becoming a second engine."]
    assert th.lines("pillars") == [
        "Search keeps growing despite AI chatbots → track: Search revenue YoY > 8%",
        "Not expensive → track: P/E < 35",
    ]
    assert th.lines("plan") == ["Add below $150 · Trim if it grows past 15% of my portfolio"]


def test_nested_lines_and_unknown_headings_stay_in_their_section():
    assert googl().lines("breakers") == [
        "Search revenue falls 2 quarters in a row",
        "nested: watch the Q4 call",
        "My notes",
        "Antitrust case ruling due in 2027",
    ]


def test_header_line_gives_conviction_and_start_date():
    th = googl()
    assert th.conviction == "HIGH" and th.started == "2026-11-06"
    assert not th.empty


def test_tick_boxes():
    th = googl()
    assert th.todos[0] == ("I understand how the company makes money", True)
    assert th.answer("I understand how the company makes money") is True
    assert th.answer("It has a durable moat (brand, network, cost or switching costs)") is False
    assert th.answer("Management is honest and allocates capital well") is True  # punctuation differs
    assert th.answer("Management is honest and allocates capital sensibly") is True  # small rewording
    assert th.answer("No promoter pledging or governance red flags") is None


def test_a_short_unrelated_tick_box_does_not_confirm_a_rule():
    th = thesis.Thesis("p", todos=[("management", True), ("call the broker", True)])
    assert th.answer("Management is honest and allocates capital well") is None


def test_empty_page():
    th = thesis.parse("p", [{"type": "paragraph", "paragraph": {"rich_text": []}}])
    assert th.empty and th.conviction is None


def test_template_tick_boxes_match_the_starter_judgment_rules():
    import re

    from stockbot.analysis import blueprint as bp

    from .test_blueprint import csv_pages

    root = Path(__file__).parents[1]
    template = (root / "templates" / "thesis.md").read_text(encoding="utf-8")
    boxes = re.findall(r"^- \[ \] (.+)$", template, re.MULTILINE)
    judgment = [r.text for r in bp.parse_rules(csv_pages()).blueprint.judgment_rules]
    assert sorted(boxes) == sorted(judgment)


def test_template_headings_are_all_recognised():
    import re

    root = Path(__file__).parents[1]
    template = (root / "templates" / "thesis.md").read_text(encoding="utf-8")
    headings = re.findall(r"^## (.+)$", template, re.MULTILINE)
    assert sorted(thesis.SECTION_OF[thesis._norm(h)] for h in headings) == sorted(thesis.SECTIONS)
