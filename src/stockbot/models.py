from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Security:
    symbol: str  # exchange symbol: RELIANCE, AAPL
    market: str  # "IN" or "US"
    name: str
    cik: int | None = None  # SEC id, US only

    @property
    def yf_symbol(self) -> str:
        if self.market == "IN":
            return f"{self.symbol}.NS"
        return self.symbol.replace(".", "-")  # BRK.B -> BRK-B

    @property
    def label(self) -> str:
        return f"{self.name} ({self.symbol} · {'NSE' if self.market == 'IN' else 'US'})"


@dataclass
class Item:
    """A news story, exchange filing or regulator release."""

    key: str  # stable id used for dedupe
    kind: str  # "news" | "filing" | "regulator"
    title: str
    url: str
    source: str
    published: datetime | None = None
    summary: str = ""
    tags: set[str] = field(default_factory=set)  # e.g. {"results"}


@dataclass(frozen=True)
class Holding:
    """A stock from the Notion portfolio or wishlist."""

    sec: Security
    list_type: str  # "portfolio" | "wishlist"
    qty: float | None = None
    avg_price: float | None = None

    @property
    def icon(self) -> str:
        return "💼" if self.list_type == "portfolio" else "⭐"
