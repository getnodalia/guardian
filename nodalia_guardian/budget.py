"""Conservative, local reservation ledger for Claude requests.

A local ledger is NOT an account-wide hard spending limit. Configure a hard workspace
limit in Claude Console, especially before automating GitHub Actions.
"""

import datetime as dt
import sqlite3
from decimal import Decimal, ROUND_CEILING
from contextlib import contextmanager, closing
from pathlib import Path

# Prices are US dollars per million tokens, as of 2026-11-17.
# Models with variable pricing are intentionally excluded until supported.
MODEL_PRICES = {
    "claude-sonnet-5-5": (2, 10),
    # Haiku 5.5 small prompts (<100K tokens); never use with long-context tier.
    "claude-haiku-5-5": ("0.10", "0.50"),
    "claude-opus-5-5": (4, 20),
}


def price_micro_usd(model: str, input_tokens: int, output_tokens: int) -> int:
    if model not in MODEL_PRICES:
        raise ValueError("Unsupported model for cost accounting")
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("Token counts cannot be negative")
    input_per_million, output_per_million = MODEL_PRICES[model]
    # Micro-dollar accounting: Haiku's fractional microdollars per token
    # must be rounded *up* to avoid an undercount when making many calls.
    amount = (Decimal(input_tokens) * Decimal(str(input_per_million))
              + Decimal(output_tokens) * Decimal(str(output_per_million)))
    return int(amount.to_integral_value(rounding=ROUND_CEILING))


def default_db() -> Path:
    import os
    root = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))
    return root / "nodalia-guardian" / "usage.sqlite3"


class BudgetExceeded(RuntimeError):
    pass


class BudgetLedger:
    def __init__(self, path: Path, monthly_budget_usd: float = 20):
        if not 0 < monthly_budget_usd <= 100:
            raise ValueError("Monthly budget must be between 0 and 100 USD")
        self.path = Path(path)
        self.limit_micro = int(round(monthly_budget_usd * 1_000_000))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                period TEXT NOT NULL,
                model TEXT NOT NULL,
                status TEXT NOT NULL,
                reserved_micro INTEGER NOT NULL,
                actual_micro INTEGER,
                created_at TEXT NOT NULL
            )""")

    @contextmanager
    def _connect(self):
        # sqlite3 connections remain open after a plain `with conn:` block.
        with closing(sqlite3.connect(self.path, timeout=20, isolation_level=None)) as db:
            yield db

    def reserve(self, model: str, payload_chars: int, max_output_tokens: int) -> tuple[int, int]:
        if payload_chars < 0 or not 1 <= max_output_tokens <= 4096:
            raise ValueError("Invalid input size or output limit")
        # This is deliberately conservative, but not a provider-authoritative bound.
        worst_input_tokens = payload_chars // 2 + 1000
        reservation = price_micro_usd(model, worst_input_tokens, max_output_tokens) * 2
        now = dt.datetime.now(dt.timezone.utc)
        period = now.strftime("%Y-%m")
        with self._connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                used = db.execute("""SELECT COALESCE(SUM(CASE
                    WHEN status = 'settled' THEN actual_micro
                    ELSE reserved_micro END),0)
                    FROM requests WHERE period = ?""", (period,)).fetchone()[0]
                if used + reservation > self.limit_micro:
                    raise BudgetExceeded(f"Local Guardian budget would be exceeded; used/reserved ${used / 1e6:.4f}, limit ${self.limit_micro / 1e6:.2f}")
                cursor = db.execute("""INSERT INTO requests
                    (period,model,status,reserved_micro,actual_micro,created_at)
                    VALUES (?,?,?,?,NULL,?)""", (period, model, "reserved", reservation, now.isoformat()))
                db.execute("COMMIT")
                return cursor.lastrowid, reservation
            except Exception:
                db.execute("ROLLBACK")
                raise

    def settle(self, request_id: int, model: str, input_tokens: int, output_tokens: int) -> int:
        cost = price_micro_usd(model, input_tokens, output_tokens)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT model,status FROM requests WHERE id=?", (request_id,)).fetchone()
            if not row or row[0] != model or row[1] != "reserved":
                db.execute("ROLLBACK")
                raise ValueError("Invalid or already settled reservation")
            db.execute("UPDATE requests SET actual_micro=?, status='settled' WHERE id=?", (cost, request_id))
            db.execute("COMMIT")
        return cost

    def total_micro(self) -> int:
        period = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m")
        with self._connect() as db:
            return db.execute("""SELECT COALESCE(SUM(CASE
                WHEN status = 'settled' THEN actual_micro
                ELSE reserved_micro END),0) FROM requests WHERE period=?""", (period,)).fetchone()[0]
