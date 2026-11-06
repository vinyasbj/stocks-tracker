"""SEC EDGAR filings: 8-K (events, earnings), 10-Q, 10-K and the foreign-filer equivalents."""

from ..models import Item
from . import http

FORMS = {"8-K", "8-K/A", "10-Q", "10-K", "6-K", "20-F", "40-F"}

ITEMS_8K = {
    "1.01": "Material agreement",
    "1.02": "Agreement terminated",
    "1.03": "Bankruptcy",
    "2.01": "Acquisition or disposal completed",
    "2.02": "Results of operations (earnings)",
    "2.03": "New debt obligation",
    "2.05": "Restructuring / exit costs",
    "2.06": "Material impairment",
    "3.01": "Delisting notice",
    "4.01": "Auditor change",
    "4.02": "Prior financials no longer reliable",
    "5.01": "Change in control",
    "5.02": "Director/officer change",
    "5.07": "Shareholder vote results",
    "7.01": "Regulation FD disclosure",
    "8.01": "Other events",
}


def parse_submissions(data: dict, cik: int, limit: int) -> list[Item]:
    rec = data.get("filings", {}).get("recent", {})
    items = []
    for i, form in enumerate(rec.get("form", [])):
        if form not in FORMS:
            continue
        acc = rec["accessionNumber"][i]
        doc = rec["primaryDocument"][i]
        codes = [c for c in (rec.get("items", [""] * (i + 1))[i] or "").split(",") if c and c != "9.01"]
        what = ", ".join(ITEMS_8K.get(c, f"Item {c}") for c in codes)
        title = f"{form}: {what}" if what else {"10-Q": "10-Q quarterly report", "10-K": "10-K annual report"}.get(form, form)
        tags = set()
        if form in ("10-Q", "10-K", "20-F") or "2.02" in codes:
            tags.add("results")
        items.append(
            Item(
                key=f"sec:{acc}",
                kind="filing",
                title=title,
                url=f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}",
                source="SEC EDGAR",
                published=http.parse_date(rec["filingDate"][i], "%Y-%m-%d"),
                tags=tags,
            )
        )
        if len(items) >= limit:
            break
    return items


def edgar_filings(cik: int, user_agent: str, limit: int = 15) -> list[Item]:
    resp = http.get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", headers={"User-Agent": user_agent})
    return parse_submissions(resp.json(), cik, limit)
