"""
db/cache.py — SQLite wrapper cho toàn bộ local data.

Tables:
  collection      — cards user sở hữu
  scryfall_cards  — oracle text, types, color identity, legality, price
  edhrec_data     — synergy scores theo commander
  banned_list     — EDH banned cards
"""

import sqlite3
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "edh_builder.db"


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Tạo tất cả tables nếu chưa có."""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS collection (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                quantity    INTEGER NOT NULL DEFAULT 1,
                set_code    TEXT,
                foil        INTEGER NOT NULL DEFAULT 0,
                condition   TEXT,
                imported_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_collection_name ON collection(name);

            CREATE TABLE IF NOT EXISTS scryfall_cards (
                name            TEXT PRIMARY KEY,
                oracle_id       TEXT,
                mana_cost       TEXT,
                cmc             REAL,
                type_line       TEXT,
                oracle_text     TEXT,
                color_identity  TEXT,   -- JSON array, e.g. '["W","U"]'
                keywords        TEXT,   -- JSON array
                legalities      TEXT,   -- JSON object
                prices          TEXT,   -- JSON object {usd, usd_foil, eur}
                scryfall_id     TEXT,
                fetched_at      TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS edhrec_data (
                commander_slug  TEXT NOT NULL,
                card_name       TEXT NOT NULL,
                synergy         REAL,
                inclusion       INTEGER,
                num_decks       INTEGER,
                potential_decks INTEGER,
                slot_tag        TEXT,   -- ramp, draw, removal, wipe, land, etc.
                fetched_at      TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (commander_slug, card_name)
            );

            CREATE INDEX IF NOT EXISTS idx_edhrec_commander
                ON edhrec_data(commander_slug);

            CREATE TABLE IF NOT EXISTS banned_list (
                name        TEXT PRIMARY KEY,
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS commanders (
                name            TEXT PRIMARY KEY,
                slug            TEXT UNIQUE,
                color_identity  TEXT,   -- JSON array
                is_legal        INTEGER NOT NULL DEFAULT 1,
                is_partner      INTEGER NOT NULL DEFAULT 0,
                partner_name    TEXT,   -- tên commander partner (nếu có)
                fetched_at      TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)


# ── Collection ────────────────────────────────────────────────────────────────

def upsert_collection(cards: list[dict]):
    """Xóa collection cũ, insert mới hoàn toàn."""
    with get_conn() as conn:
        conn.execute("DELETE FROM collection")
        conn.executemany(
            """INSERT INTO collection (name, quantity, set_code, foil, condition)
               VALUES (:name, :quantity, :set_code, :foil, :condition)""",
            cards,
        )


def get_collection() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT name, SUM(quantity) as quantity FROM collection GROUP BY name"
        ).fetchall()


def get_collection_names() -> set[str]:
    with get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT name FROM collection").fetchall()
        return {r["name"] for r in rows}


# ── Scryfall cards ────────────────────────────────────────────────────────────

def get_scryfall_card(name: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM scryfall_cards WHERE name = ?", (name,)
        ).fetchone()


def upsert_scryfall_card(data: dict):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO scryfall_cards
               (name, oracle_id, mana_cost, cmc, type_line, oracle_text,
                color_identity, keywords, legalities, prices, scryfall_id, fetched_at)
               VALUES (:name, :oracle_id, :mana_cost, :cmc, :type_line, :oracle_text,
                       :color_identity, :keywords, :legalities, :prices, :scryfall_id,
                       datetime('now'))
               ON CONFLICT(name) DO UPDATE SET
                 mana_cost=excluded.mana_cost, cmc=excluded.cmc,
                 type_line=excluded.type_line, oracle_text=excluded.oracle_text,
                 color_identity=excluded.color_identity, keywords=excluded.keywords,
                 legalities=excluded.legalities, prices=excluded.prices,
                 fetched_at=excluded.fetched_at""",
            data,
        )


def get_missing_scryfall_cards(names: list[str]) -> list[str]:
    """Trả về tên card chưa có trong cache."""
    if not names:
        return []
    placeholders = ",".join("?" * len(names))
    with get_conn() as conn:
        cached = conn.execute(
            f"SELECT name FROM scryfall_cards WHERE name IN ({placeholders})",
            names,
        ).fetchall()
    cached_names = {r["name"] for r in cached}
    return [n for n in names if n not in cached_names]


# ── EDHREC data ───────────────────────────────────────────────────────────────

def get_edhrec_cards(commander_slug: str, max_age_days: int = 7) -> list[sqlite3.Row]:
    """Lấy card EDHREC theo commander, None nếu cache hết hạn."""
    cutoff = (datetime.utcnow() - timedelta(days=max_age_days)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM edhrec_data
               WHERE commander_slug = ? AND fetched_at > ?
               ORDER BY synergy DESC""",
            (commander_slug, cutoff),
        ).fetchall()
    return rows


def upsert_edhrec_cards(commander_slug: str, cards: list[dict]):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM edhrec_data WHERE commander_slug = ?", (commander_slug,)
        )
        conn.executemany(
            """INSERT INTO edhrec_data
               (commander_slug, card_name, synergy, inclusion, num_decks,
                potential_decks, slot_tag, fetched_at)
               VALUES (:commander_slug, :card_name, :synergy, :inclusion,
                       :num_decks, :potential_decks, :slot_tag, datetime('now'))""",
            [{"commander_slug": commander_slug, **c} for c in cards],
        )


# ── Banned list ───────────────────────────────────────────────────────────────

def get_banned_list() -> set[str]:
    with get_conn() as conn:
        rows = conn.execute("SELECT name FROM banned_list").fetchall()
        return {r["name"] for r in rows}


def update_banned_list(names: list[str]):
    with get_conn() as conn:
        conn.execute("DELETE FROM banned_list")
        conn.executemany(
            "INSERT INTO banned_list (name) VALUES (?)", [(n,) for n in names]
        )


# ── Commanders ────────────────────────────────────────────────────────────────

def upsert_commanders(commanders: list[dict]):
    with get_conn() as conn:
        conn.executemany(
            """INSERT INTO commanders (name, slug, color_identity, is_legal,
                                         is_partner, partner_name, fetched_at)
               VALUES (:name, :slug, :color_identity, 1,
                       :is_partner, :partner_name, datetime('now'))
               ON CONFLICT(name) DO UPDATE SET
                 slug=excluded.slug, color_identity=excluded.color_identity,
                 is_partner=excluded.is_partner, partner_name=excluded.partner_name,
                 fetched_at=excluded.fetched_at""",
            commanders,
        )


def get_all_commanders() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM commanders WHERE is_legal = 1 ORDER BY name"
        ).fetchall()
