from typing import Literal

from pydantic import BaseModel, Field

GROUNDING = (
    "Use ONLY the numbers and facts in DATA. Never invent or estimate figures. "
    "If DATA does not contain the answer, say so plainly. "
    "Do not tell the user to buy or sell. Plain text only: no markdown, no tables, no headings."
)

SUMMARY_SYSTEM = (
    "You are an equity research assistant writing for a retail investor. "
    "Given DATA about one stock, write a balanced summary in 4-6 short sentences: "
    "the business momentum in the latest quarter, the biggest strength, the biggest risk, "
    "and what to watch next (from the recent news or filings, if any). " + GROUNDING
)

RESULTS_SYSTEM = (
    "You are an equity research assistant. Given DATA about one stock's latest quarterly results, "
    "explain them in 3-5 short sentences: revenue and profit versus the previous quarter and the same "
    "quarter last year, margins, and anything notable in the result filings or news. " + GROUNDING
)

QA_SYSTEM = (
    "You are a stock research assistant in a Telegram chat. Answer the user's question about the "
    "stock in DATA in under 180 words. Quote the relevant numbers and dates from DATA. " + GROUNDING
)

INTENT_SYSTEM = (
    "You read a message sent to a stock research bot and extract:\n"
    "- company: the company name or ticker the user asks about, exactly as they wrote it "
    "(e.g. 'hdfc bank', 'AAPL', 'tata motors'); null if they name none.\n"
    "- intent: 'results' for quarterly/annual results or earnings, 'news' for latest news, updates, "
    "filings or regulator actions, 'analyze' for strengths, weaknesses, fundamentals or whether it is a "
    "good company, 'other' for anything else about the stock.\n"
    "- market: 'IN' if the user says NSE, BSE or India; 'US' if they say NYSE, NASDAQ or US; else null."
)

IMPACT_SYSTEM = (
    "You assess news and filings for an investor who holds {stock}. For each numbered item return "
    "its index, impact on the stock price (positive, negative or neutral), importance (high: results, "
    "guidance, big orders, M&A, regulatory action, management change, fraud, rating change; "
    "low: generic market wrap-ups, price-movement recaps, listicles, routine compliance), and a reason "
    "of at most 15 words. Judge only from the item text."
)

REGULATOR_SYSTEM = (
    "You decide whether a regulator release affects specific listed companies. Given the RELEASE and "
    "a list of STOCKS (symbol, name, sector, industry), return only the stocks it materially affects: "
    "it names the company, or it changes rules for that company's industry (e.g. bank capital, lending, "
    "NBFC rules for banks/NBFCs; broker, AMC or exchange rules for capital-market firms; pricing or "
    "disclosure rules for that industry). General notices, routine auctions, orders against unrelated "
    "entities and investor-awareness items affect no stock. For each stock give impact and a reason "
    "of at most 15 words. Return an empty list when unsure."
)


class Intent(BaseModel):
    company: str | None
    intent: Literal["analyze", "results", "news", "other"]
    market: Literal["IN", "US"] | None = None


class ItemImpact(BaseModel):
    index: int
    impact: Literal["positive", "negative", "neutral"]
    importance: Literal["high", "medium", "low"]
    reason: str = Field(max_length=200)


class Impacts(BaseModel):
    items: list[ItemImpact]


class StockImpact(BaseModel):
    symbol: str
    impact: Literal["positive", "negative", "neutral"]
    reason: str = Field(max_length=200)


class RegulatorImpact(BaseModel):
    affected: list[StockImpact]
