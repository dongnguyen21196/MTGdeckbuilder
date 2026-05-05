"""
db/cache.py — SQLite wrapper cho toàn bộ local data.

FIX 1 — Reprint deduplication:
  Collection lưu thêm cột oracle_name (= tên canonical từ Scryfall).
  Khi check ownership, dùng oracle_name thay vì printing name.
  Demonic Tutor bản STA và bản EMA đều map về oracle_name="Demonic Tutor".

FIX 2 — Price TTL tách riêng:
  Tách prices ra bảng scryfall_prices với TTL 7 ngày.
  scryfall_cards (oracle data) vẫn cache 30 ngày.
"""

import sqlite3
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "edh_builder.db"

ORACLE_TTL_DAYS = int(os.getenv("SCRYFALL_CACHE_TTL_DAYS", "30"))
PRICE_TTL_DAYS  = int(os.getenv("SCRYFALL_PRICE_TTL_DAYS", "7"))
EDHREC_TTL_DAYS = int(os.getenv("EDHREC_CACHE_TTL_DAYS", "7"))


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Tạo tất cả tables và chạy migrations nếu cần."""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS collection (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT NOT NULL,
                oracle_name  TEXT,
                quantity     INTEGER NOT NULL DEFAULT 1,
                set_code     TEXT,
                foil         INTEGER NOT NULL DEFAULT 0,
                condition    TEXT,
                imported_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_collection_name
                ON collection(name);
            CREATE INDEX IF NOT EXISTS idx_collection_oracle
                ON collection(oracle_name);

            CREATE TABLE IF NOT EXISTS scryfall_cards (
                name            TEXT PRIMARY KEY,
                oracle_id       TEXT,
                oracle_name     TEXT,
                mana_cost       TEXT,
                cmc             REAL,
                type_line       TEXT,
                oracle_text     TEXT,
                color_identity  TEXT,
                keywords        TEXT,
                legalities      TEXT,
                scryfall_id     TEXT,
                fetched_at      TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_scryfall_oracle_id
                ON scryfall_cards(oracle_id);
            CREATE INDEX IF NOT EXISTS idx_scryfall_oracle_name
                ON scryfall_cards(oracle_name);

            CREATE TABLE IF NOT EXISTS scryfall_prices (
                oracle_name  TEXT PRIMARY KEY,
                usd          TEXT,
                usd_foil     TEXT,
                eur          TEXT,
                updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS edhrec_data (
                commander_slug  TEXT NOT NULL,
                card_name       TEXT NOT NULL,
                synergy         REAL,
                inclusion       INTEGER,
                num_decks       INTEGER,
                potential_decks INTEGER,
                slot_tag        TEXT,
                fetched_at      TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (commander_slug, card_name)
            );
            CREATE INDEX IF NOT EXISTS idx_edhrec_commander
                ON edhrec_data(commander_slug);

            CREATE TABLE IF NOT EXISTS banned_list (
                name       TEXT PRIMARY KEY,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS commanders (
                name           TEXT PRIMARY KEY,
                slug           TEXT UNIQUE,
                color_identity TEXT,
                is_legal       INTEGER NOT NULL DEFAULT 1,
                is_partner     INTEGER NOT NULL DEFAULT 0,
                partner_name   TEXT,
                fetched_at     TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS db_migrations (
                version    INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (datetime('now')),
                description TEXT
            );
        """)
    _run_migrations()


def _run_migrations():
    """Chạy schema migrations cho DB đã tồn tại từ version cũ."""
    with get_conn() as conn:
        applied = {
            r["version"]
            for r in conn.execute("SELECT version FROM db_migrations").fetchall()
        }

    migrations = [
        (1, "Add oracle_name to collection",
         ["ALTER TABLE collection ADD COLUMN oracle_name TEXT"]),
        (2, "Add oracle_name + oracle_id index to scryfall_cards",
         ["ALTER TABLE scryfall_cards ADD COLUMN oracle_name TEXT"]),
        (3, "Migrate prices to scryfall_prices table", [
            """INSERT OR IGNORE INTO scryfall_prices (oracle_name, usd, usd_foil, eur, updated_at)
               SELECT COALESCE(oracle_name, name),
                      json_extract(prices, '$.usd'),
                      json_extract(prices, '$.usd_foil'),
                      json_extract(prices, '$.eur'),
                      fetched_at
               FROM scryfall_cards
               WHERE prices IS NOT NULL AND prices NOT IN ('{}', 'null', '')"""
        ]),
        (4, "Add partner columns to commanders", [
            "ALTER TABLE commanders ADD COLUMN is_partner INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE commanders ADD COLUMN partner_name TEXT",
        ]),
    ]

    for version, description, stmts in migrations:
        if version in applied:
            continue
        try:
            with get_conn() as conn:
                for stmt in stmts:
                    try:
                        conn.execute(stmt)
                    except sqlite3.OperationalError as e:
                        if "already exists" in str(e) or "duplicate column" in str(e).lower():
                            pass  # Idempotent — bỏ qua nếu đã có
                        else:
                            raise
                conn.execute(
                    "INSERT INTO db_migrations (version, description) VALUES (?, ?)",
                    (version, description),
                )
        except Exception as e:
            print(f"  [!] Migration v{version} skipped: {e}")


# ── Collection ────────────────────────────────────────────────────────────────

def upsert_collection(cards: list[dict]):
    """
    Xóa collection cũ, insert mới.
    oracle_name được lookup từ cache nếu có; fallback về printing name.
    """
    enriched = [_resolve_oracle_name(c) for c in cards]
    with get_conn() as conn:
        conn.execute("DELETE FROM collection")
        conn.executemany(
            """INSERT INTO collection
               (name, oracle_name, quantity, set_code, foil, condition)
               VALUES (:name, :oracle_name, :quantity, :set_code, :foil, :condition)""",
            enriched,
        )


def _resolve_oracle_name(card: dict) -> dict:
    """Lookup oracle_name từ scryfall_cards cache."""
    name = card["name"]
    with get_conn() as conn:
        row = conn.execute(
            "SELECT oracle_name FROM scryfall_cards WHERE name = ?", (name,)
        ).fetchone()
    oracle_name = (row["oracle_name"] if row and row["oracle_name"] else name)
    return {**card, "oracle_name": oracle_name}


def refresh_collection_oracle_names():
    """
    Cập nhật oracle_name cho toàn bộ collection dựa trên scryfall_cards cache.
    Gọi sau import + enrich để đảm bảo dedup chính xác.
    """
    with get_conn() as conn:
        conn.execute("""
            UPDATE collection
            SET oracle_name = COALESCE(
                (SELECT sc.oracle_name FROM scryfall_cards sc
                 WHERE sc.name = collection.name AND sc.oracle_name IS NOT NULL),
                collection.name
            )
        """)


def get_collection() -> list[sqlite3.Row]:
    """Trả về collection grouped theo oracle_name (deduped across reprints)."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT COALESCE(oracle_name, name) as name, SUM(quantity) as quantity
               FROM collection GROUP BY COALESCE(oracle_name, name)"""
        ).fetchall()


def get_collection_names() -> set[str]:
    """
    Trả về set oracle_name để check ownership.
    FIX 1: Dùng oracle_name → mọi reprint của cùng card được tính là owned.
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT COALESCE(oracle_name, name) as canon FROM collection"
        ).fetchall()
        return {r["canon"] for r in rows}


# ── Scryfall oracle data ──────────────────────────────────────────────────────

def get_scryfall_card(name: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM scryfall_cards WHERE name = ?", (name,)
        ).fetchone()


def upsert_scryfall_card(data: dict):
    """Upsert oracle data. Prices lưu riêng qua upsert_price()."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO scryfall_cards
               (name, oracle_id, oracle_name, mana_cost, cmc, type_line, oracle_text,
                color_identity, keywords, legalities, scryfall_id, fetched_at)
               VALUES (:name, :oracle_id, :oracle_name, :mana_cost, :cmc, :type_line,
                       :oracle_text, :color_identity, :keywords, :legalities,
                       :scryfall_id, datetime('now'))
               ON CONFLICT(name) DO UPDATE SET
                 oracle_id=excluded.oracle_id,
                 oracle_name=excluded.oracle_name,
                 mana_cost=excluded.mana_cost,
                 cmc=excluded.cmc,
                 type_line=excluded.type_line,
                 oracle_text=excluded.oracle_text,
                 color_identity=excluded.color_identity,
                 keywords=excluded.keywords,
                 legalities=excluded.legalities,
                 fetched_at=excluded.fetched_at""",
            data,
        )


def get_missing_scryfall_cards(names: list[str]) -> list[str]:
    """Card chưa có oracle data hoặc đã stale (> ORACLE_TTL_DAYS)."""
    if not names:
        return []
    cutoff = (datetime.utcnow() - timedelta(days=ORACLE_TTL_DAYS)).isoformat()
    placeholders = ",".join("?" * len(names))
    with get_conn() as conn:
        cached = conn.execute(
            f"SELECT name FROM scryfall_cards WHERE name IN ({placeholders}) AND fetched_at > ?",
            names + [cutoff],
        ).fetchall()
    cached_names = {r["name"] for r in cached}
    return [n for n in names if n not in cached_names]


# ── Scryfall prices (TTL ngắn hơn oracle data) ────────────────────────────────

def upsert_price(oracle_name: str, usd: str | None, usd_foil: str | None, eur: str | None):
    """Lưu/cập nhật giá cho card theo oracle_name."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO scryfall_prices (oracle_name, usd, usd_foil, eur, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'))
               ON CONFLICT(oracle_name) DO UPDATE SET
                 usd=excluded.usd, usd_foil=excluded.usd_foil,
                 eur=excluded.eur, updated_at=excluded.updated_at""",
            (oracle_name, usd, usd_foil, eur),
        )


def get_price_usd(oracle_name: str) -> float | None:
    """Lấy giá USD float. None nếu không có hoặc stale."""
    cutoff = (datetime.utcnow() - timedelta(days=PRICE_TTL_DAYS)).isoformat()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT usd FROM scryfall_prices WHERE oracle_name = ? AND updated_at > ?",
            (oracle_name, cutoff),
        ).fetchone()
    if not row or not row["usd"]:
        return None
    try:
        return float(row["usd"])
    except (ValueError, TypeError):
        return None


def get_stale_price_cards(oracle_names: list[str]) -> list[str]:
    """
    Trả về oracle_names cần refresh giá (chưa có hoặc > PRICE_TTL_DAYS).
    FIX 2: TTL riêng 7 ngày cho prices, không dùng chung 30 ngày với oracle.
    """
    if not oracle_names:
        return []
    cutoff = (datetime.utcnow() - timedelta(days=PRICE_TTL_DAYS)).isoformat()
    placeholders = ",".join("?" * len(oracle_names))
    with get_conn() as conn:
        fresh = conn.execute(
            f"""SELECT oracle_name FROM scryfall_prices
                WHERE oracle_name IN ({placeholders}) AND updated_at > ?""",
            oracle_names + [cutoff],
        ).fetchall()
    fresh_names = {r["oracle_name"] for r in fresh}
    return [n for n in oracle_names if n not in fresh_names]


# ── EDHREC data ───────────────────────────────────────────────────────────────

def get_edhrec_cards(commander_slug: str, max_age_days: int = EDHREC_TTL_DAYS) -> list[sqlite3.Row]:
    cutoff = (datetime.utcnow() - timedelta(days=max_age_days)).isoformat()
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM edhrec_data
               WHERE commander_slug = ? AND fetched_at > ?
               ORDER BY synergy DESC""",
            (commander_slug, cutoff),
        ).fetchall()


def upsert_edhrec_cards(commander_slug: str, cards: list[dict]):
    with get_conn() as conn:
        conn.execute("DELETE FROM edhrec_data WHERE commander_slug = ?", (commander_slug,))
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
        return {r["name"] for r in conn.execute("SELECT name FROM banned_list").fetchall()}


def update_banned_list(names: list[str]):
    with get_conn() as conn:
        conn.execute("DELETE FROM banned_list")
        conn.executemany("INSERT INTO banned_list (name) VALUES (?)", [(n,) for n in names])


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


# ── DB stats ──────────────────────────────────────────────────────────────────

def get_db_stats() -> dict:
    """Thống kê DB. Dùng để debug và monitor."""
    with get_conn() as conn:
        stats = {}
        for t in ["collection", "scryfall_cards", "scryfall_prices",
                  "edhrec_data", "banned_list", "commanders"]:
            stats[t] = conn.execute(f"SELECT COUNT(*) as n FROM {t}").fetchone()["n"]

        cutoff = (datetime.utcnow() - timedelta(days=PRICE_TTL_DAYS)).isoformat()
        stats["stale_prices"] = conn.execute(
            "SELECT COUNT(*) as n FROM scryfall_prices WHERE updated_at <= ?", (cutoff,)
        ).fetchone()["n"]

        v = conn.execute("SELECT MAX(version) as v FROM db_migrations").fetchone()
        stats["schema_version"] = v["v"] or 0
    return stats
