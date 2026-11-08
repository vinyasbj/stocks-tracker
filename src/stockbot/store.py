import json
import sqlite3
import time
from dataclasses import replace
from pathlib import Path

from .analysis.blueprint import Blueprint, Locked
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
    pages TEXT,
    conviction TEXT,
    PRIMARY KEY (symbol, market)
);
CREATE TABLE IF NOT EXISTS blueprint_versions (
    version INTEGER PRIMARY KEY,
    locked_at REAL NOT NULL,
    fingerprint TEXT NOT NULL,
    blueprint TEXT NOT NULL
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
        for column in ("pages TEXT", "conviction TEXT"):  # databases created before theses were read
            try:
                self.db.execute(f"ALTER TABLE holdings ADD COLUMN {column}")
            except sqlite3.OperationalError:
                pass  # already there

    # --- holdings (mirrors the Notion portfolio and wishlist) ---
    def replace_holdings(self, holdings: list[Holding]) -> tuple[list[Security], list[Security]]:
        """Swap in the latest Notion lists. Returns (added, removed) securities."""
        merged: dict[Security, Holding] = {}
        for h in holdings:
            old = merged.get(h.sec)
            if old is None or (old.list_type == "wishlist" and h.list_type == "portfolio"):
                merged[h.sec] = h
            elif old.list_type == h.list_type == "portfolio":
                # Several lots of the same stock: keep every row's page, in Notion order.
                pages, conviction = old.pages + h.pages, old.conviction or h.conviction
                if old.qty and h.qty:
                    # Total quantity, weighted average price.
                    qty = old.qty + h.qty
                    avg = (
                        (old.qty * old.avg_price + h.qty * h.avg_price) / qty
                        if old.avg_price is not None and h.avg_price is not None
                        else None
                    )
                    merged[h.sec] = Holding(h.sec, "portfolio", qty, avg, pages, conviction)
                else:
                    merged[h.sec] = replace(old, pages=pages, conviction=conviction)
        before = {h.sec for h in self.holdings()}
        with self.db:
            self.db.execute("BEGIN")
            self.db.execute("DELETE FROM holdings")
            self.db.executemany(
                "INSERT INTO holdings (symbol, market, name, cik, list_type, qty, avg_price, pages, conviction) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        h.sec.symbol, h.sec.market, h.sec.name, h.sec.cik, h.list_type, h.qty, h.avg_price,
                        ",".join(dict.fromkeys(p for p in h.pages if p)), h.conviction,
                    )
                    for h in merged.values()
                ],
            )
        after = set(merged)
        return [s for s in merged if s not in before], [s for s in before if s not in after]

    def holdings(self) -> list[Holding]:
        rows = self.db.execute(
            "SELECT symbol, market, name, cik, list_type, qty, avg_price, pages, conviction FROM holdings "
            "ORDER BY list_type, symbol"
        )
        return [
            Holding(Security(sym, mkt, name, cik), lt, qty, avg, tuple(p for p in (pages or "").split(",") if p), conv)
            for sym, mkt, name, cik, lt, qty, avg, pages, conv in rows
        ]

    def holding(self, sec: Security) -> Holding | None:
        """Matched on symbol and market, the table's key: a lookup may spell the company name differently."""
        return next((h for h in self.holdings() if (h.sec.symbol, h.sec.market) == (sec.symbol, sec.market)), None)

    # --- the locked conviction blueprint (every version is kept) ---
    def lock_blueprint(self, bp: Blueprint) -> int:
        """Store `bp` as the next version and return its number."""
        cur = self.db.execute(
            "INSERT INTO blueprint_versions (locked_at, fingerprint, blueprint) VALUES (?, ?, ?)",
            (time.time(), bp.fingerprint(), json.dumps(bp.to_json())),
        )
        return cur.lastrowid

    def locked_blueprint(self) -> Locked | None:
        """The latest locked version, which is the one stocks are judged by."""
        row = self.db.execute(
            "SELECT version, locked_at, blueprint FROM blueprint_versions ORDER BY version DESC LIMIT 1"
        ).fetchone()
        return Locked(row[0], row[1], Blueprint.from_json(json.loads(row[2]))) if row else None

    def blueprint_versions(self) -> list[int]:
        return [r[0] for r in self.db.execute("SELECT version FROM blueprint_versions ORDER BY version")]

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
