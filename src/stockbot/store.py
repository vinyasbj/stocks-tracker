import json
import sqlite3
import time
from pathlib import Path

from .models import Holding, Security

SCHEMA = """
CREATE TABLE IF NOT EXISTS holdings (
    symbol TEXT NOT NULL,
    market TEXT NOT NULL,
    name TEXT NOT NULL,
    cik INTEGER,
    list_type TEXT NOT NULL,
    qty REAL,
    avg_price REAL,
    PRIMARY KEY (symbol, market)
);
CREATE TABLE IF NOT EXISTS seen_items (
    scope TEXT NOT NULL,
    key TEXT NOT NULL,
    seen_at REAL NOT NULL,
    PRIMARY KEY (scope, key)
);
CREATE TABLE IF NOT EXISTS chat_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    expires_at REAL NOT NULL
);
"""


class Store:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # The bot calls the store from the event loop and from worker threads.
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.executescript(SCHEMA)

    # --- holdings (mirrors the Notion portfolio and wishlist) ---
    def replace_holdings(self, holdings: list[Holding]) -> tuple[list[Security], list[Security]]:
        """Swap in the latest Notion lists. Returns (added, removed) securities."""
        merged: dict[Security, Holding] = {}
        for h in holdings:
            old = merged.get(h.sec)
            if old is None or (old.list_type == "wishlist" and h.list_type == "portfolio"):
                merged[h.sec] = h
            elif old.list_type == h.list_type == "portfolio" and old.qty and h.qty:
                # Several lots of the same stock: total quantity, weighted average price.
                qty = old.qty + h.qty
                avg = (
                    (old.qty * old.avg_price + h.qty * h.avg_price) / qty
                    if old.avg_price is not None and h.avg_price is not None
                    else None
                )
                merged[h.sec] = Holding(h.sec, "portfolio", qty, avg)
        before = {h.sec for h in self.holdings()}
        with self.db:
            self.db.execute("BEGIN")
            self.db.execute("DELETE FROM holdings")
            self.db.executemany(
                "INSERT INTO holdings VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (h.sec.symbol, h.sec.market, h.sec.name, h.sec.cik, h.list_type, h.qty, h.avg_price)
                    for h in merged.values()
                ],
            )
        after = set(merged)
        return [s for s in merged if s not in before], [s for s in before if s not in after]

    def holdings(self) -> list[Holding]:
        rows = self.db.execute(
            "SELECT symbol, market, name, cik, list_type, qty, avg_price FROM holdings "
            "ORDER BY list_type, symbol"
        )
        return [Holding(Security(sym, mkt, name, cik), lt, qty, avg) for sym, mkt, name, cik, lt, qty, avg in rows]

    # --- dedupe ---
    def has_scope(self, scope: str) -> bool:
        return self.db.execute("SELECT 1 FROM seen_items WHERE scope = ? LIMIT 1", (scope,)).fetchone() is not None

    def unseen(self, scope: str, keys: list[str]) -> set[str]:
        if not keys:
            return set()
        marks = ",".join("?" * len(keys))
        seen = {
            r[0]
            for r in self.db.execute(
                f"SELECT key FROM seen_items WHERE scope = ? AND key IN ({marks})", (scope, *keys)
            )
        }
        return set(keys) - seen

    def mark_seen(self, scope: str, keys: list[str]) -> None:
        now = time.time()
        self.db.executemany(
            "INSERT OR IGNORE INTO seen_items VALUES (?, ?, ?)", [(scope, k, now) for k in keys]
        )

    # --- conversation ---
    def add_turn(self, chat_id: int, role: str, content: str) -> None:
        self.db.execute(
            "INSERT INTO chat_history (chat_id, role, content, ts) VALUES (?, ?, ?, ?)",
            (chat_id, role, content, time.time()),
        )

    def history(self, chat_id: int, turns: int = 6) -> list[dict]:
        rows = self.db.execute(
            "SELECT role, content FROM chat_history WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
            (chat_id, turns),
        ).fetchall()
        return [{"role": r, "content": c} for r, c in reversed(rows)]

    # --- cache ---
    def cache_get(self, key: str):
        row = self.db.execute("SELECT value, expires_at FROM cache WHERE key = ?", (key,)).fetchone()
        if row is None or row[1] < time.time():
            return None
        return json.loads(row[0])

    def cache_set(self, key: str, value, ttl_seconds: float) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO cache VALUES (?, ?, ?)",
            (key, json.dumps(value), time.time() + ttl_seconds),
        )
