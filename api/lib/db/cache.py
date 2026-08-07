"""
db/cache.py — Postgres (Neon) wrapper cho toàn bộ dữ liệu.

Port từ bản SQLite. Chữ ký của mọi hàm public được giữ nguyên nên các module
engine/, enrichers/, filters/ không phải sửa gì.

Khác biệt so với bản SQLite:

  1. Postgres thay cho file SQLite local — Vercel có filesystem read-only,
     `/tmp` bị xoá sau mỗi cold start nên không thể giữ state ở đó.

  2. Bảng `collection` có thêm cột `session_id`. App chạy public multi-user,
     mỗi khách có một session riêng. Session hiện tại được truyền qua
     ContextVar (`set_session`) thay vì luồn tham số qua 8 tầng hàm của engine.

  3. `_resolve_oracle_name` gộp thành một query duy nhất. Bản SQLite query
     từng card một — chấp nhận được với file local, nhưng với DB qua mạng thì
     collection 5.000 card sẽ thành 5.000 round trip.

  4. TTL so sánh bằng `now() - interval` trong SQL thay vì so chuỗi ISO trong
     Python, tránh lệ thuộc timezone của process.

Giữ nguyên từ bản SQLite:

  FIX 1 — Reprint deduplication:
    Collection lưu thêm cột oracle_name (= tên canonical từ Scryfall).
    Khi check ownership dùng oracle_name thay vì printing name.
    Demonic Tutor bản STA và bản EMA đều map về oracle_name="Demonic Tutor".

  FIX 2 — Price TTL tách riêng:
    Prices nằm ở bảng scryfall_prices với TTL 7 ngày.
    scryfall_cards (oracle data) vẫn cache 30 ngày.
"""

import contextvars
import os
import re
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

ORACLE_TTL_DAYS = int(os.getenv("SCRYFALL_CACHE_TTL_DAYS", "30"))
PRICE_TTL_DAYS = int(os.getenv("SCRYFALL_PRICE_TTL_DAYS", "7"))
EDHREC_TTL_DAYS = int(os.getenv("EDHREC_CACHE_TTL_DAYS", "7"))

_TABLES = [
    "collection",
    "scryfall_cards",
    "scryfall_prices",
    "edhrec_data",
    "banned_list",
    "commanders",
]

# Session của request đang chạy. Chỉ các hàm collection dùng tới.
# Seeder không đụng bảng collection nên không cần set.
_session = contextvars.ContextVar("session_id", default=None)

_pool: ConnectionPool | None = None


# ── Kết nối ───────────────────────────────────────────────────────────────────

def set_session(session_id: str) -> None:
    """Gán session cho request hiện tại. API gọi ở đầu mỗi request."""
    _session.set(session_id)


def current_session() -> str:
    sid = _session.get()
    if not sid:
        raise RuntimeError(
            "Chưa có session. Gọi cache.set_session(<id>) trước khi đụng collection."
        )
    return sid


_ENV_PREFIXES = ("DATABASE_URL=", "POSTGRES_URL=")


def _dsn() -> str:
    raw = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
    if not raw:
        raise RuntimeError(
            "Thiếu DATABASE_URL. Tạo Neon Postgres trong Vercel Marketplace "
            "hoặc set biến môi trường khi chạy local."
        )

    dsn = raw.strip().strip("\"'")
    # Copy nguyên dòng từ tab .env của Vercel rồi dán vào secret là lỗi rất dễ
    # gặp; psycopg sẽ hiểu chuỗi đó là connection string dạng key=value và báo
    # `invalid connection option "DATABASE_URL"`, chẳng gợi ý gì về nguyên nhân.
    for prefix in _ENV_PREFIXES:
        if dsn.startswith(prefix):
            dsn = dsn[len(prefix):].strip().strip("\"'")
            break

    if dsn.startswith("postgres://"):
        # psycopg chỉ nhận scheme postgresql://
        dsn = "postgresql://" + dsn[len("postgres://"):]

    if not dsn.startswith("postgresql://"):
        raise RuntimeError(
            "DATABASE_URL không phải connection string hợp lệ — phải bắt đầu "
            f"bằng postgresql:// (nhận được: {dsn[:20]!r}…)"
        )
    return dsn


def get_pool() -> ConnectionPool:
    """Pool dùng chung cho cả process. Fluid Compute cho phép nhiều request
    chạy song song trên một instance nên pool thật sự có tác dụng."""
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            _dsn(),
            min_size=0,
            max_size=int(os.getenv("DB_POOL_MAX", "5")),
            # Neon suspend sau vài phút idle — check trước khi giao connection
            # để request không chết vì một kết nối đã bị server đóng.
            check=ConnectionPool.check_connection,
            kwargs={"row_factory": dict_row},
            # Mặc định của pool là chờ 30s mới báo lỗi. Trong function có trần
            # 60s, hỏng kết nối mà treo 30s thì coi như mất luôn request.
            timeout=float(os.getenv("DB_CONNECT_TIMEOUT", "8")),
            open=True,
        )
    return _pool


@contextmanager
def cursor():
    """Cursor có commit/rollback tự động. Đơn vị giao dịch cơ bản của module."""
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            yield cur


# Giữ tên cũ cho code ngoài module vẫn gọi được.
get_conn = cursor


# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS collection (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id   text NOT NULL,
    name         text NOT NULL,
    oracle_name  text,
    quantity     integer NOT NULL DEFAULT 1,
    set_code     text,
    foil         boolean NOT NULL DEFAULT false,
    condition    text,
    imported_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_collection_session ON collection(session_id);
CREATE INDEX IF NOT EXISTS idx_collection_session_name ON collection(session_id, name);
CREATE INDEX IF NOT EXISTS idx_collection_session_oracle ON collection(session_id, oracle_name);

CREATE TABLE IF NOT EXISTS scryfall_cards (
    name            text PRIMARY KEY,
    oracle_id       text,
    oracle_name     text,
    mana_cost       text,
    cmc             real,
    type_line       text,
    oracle_text     text,
    color_identity  text,
    keywords        text,
    legalities      text,
    scryfall_id     text,
    fetched_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_scryfall_oracle_id ON scryfall_cards(oracle_id);
CREATE INDEX IF NOT EXISTS idx_scryfall_oracle_name ON scryfall_cards(oracle_name);

CREATE TABLE IF NOT EXISTS scryfall_prices (
    oracle_name  text PRIMARY KEY,
    usd          text,
    usd_foil     text,
    eur          text,
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS edhrec_data (
    commander_slug  text NOT NULL,
    card_name       text NOT NULL,
    synergy         real,
    inclusion       integer,
    num_decks       integer,
    potential_decks integer,
    slot_tag        text,
    fetched_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (commander_slug, card_name)
);
CREATE INDEX IF NOT EXISTS idx_edhrec_commander ON edhrec_data(commander_slug);

CREATE TABLE IF NOT EXISTS banned_list (
    name       text PRIMARY KEY,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS commanders (
    name           text PRIMARY KEY,
    slug           text UNIQUE,
    color_identity text,
    is_legal       boolean NOT NULL DEFAULT true,
    is_partner     boolean NOT NULL DEFAULT false,
    partner_name   text,
    fetched_at     timestamptz NOT NULL DEFAULT now()
);
"""


def init_db():
    """Tạo schema. Idempotent. Seeder gọi lúc khởi động; API không gọi mỗi
    request để khỏi tốn round trip thừa."""
    with cursor() as cur:
        cur.execute(_SCHEMA)


def drop_stale_sessions(older_than_days: int = 30) -> int:
    """Dọn collection của session cũ. Seeder gọi hàng đêm."""
    with cursor() as cur:
        cur.execute(
            "DELETE FROM collection WHERE imported_at < now() - make_interval(days => %s)",
            (older_than_days,),
        )
        return cur.rowcount


# ── Collection ────────────────────────────────────────────────────────────────

def upsert_collection(cards: list[dict]):
    """Thay toàn bộ collection của session hiện tại."""
    sid = current_session()
    enriched = _resolve_oracle_names(cards)
    with cursor() as cur:
        cur.execute("DELETE FROM collection WHERE session_id = %s", (sid,))
        cur.executemany(
            """INSERT INTO collection
               (session_id, name, oracle_name, quantity, set_code, foil, condition)
               VALUES (%(session_id)s, %(name)s, %(oracle_name)s, %(quantity)s,
                       %(set_code)s, %(foil)s, %(condition)s)""",
            # foil được importer sinh ra dạng int 0/1 (di sản SQLite). Postgres
            # không tự ép integer sang boolean nên phải coerce ở đây — sửa ở lớp
            # db thay vì đụng vào importer.
            [{"session_id": sid, **c, "foil": bool(c.get("foil"))} for c in enriched],
        )


def _resolve_oracle_names(cards: list[dict]) -> list[dict]:
    """Map printing name → oracle_name bằng một query duy nhất.
    Card chưa có trong cache Scryfall thì fallback về chính printing name."""
    if not cards:
        return []
    names = list({c["name"] for c in cards})
    with cursor() as cur:
        cur.execute(
            """SELECT name, oracle_name FROM scryfall_cards
               WHERE name = ANY(%s) AND oracle_name IS NOT NULL""",
            (names,),
        )
        mapping = {r["name"]: r["oracle_name"] for r in cur.fetchall()}
    return [{**c, "oracle_name": mapping.get(c["name"], c["name"])} for c in cards]


def refresh_collection_oracle_names():
    """Cập nhật oracle_name cho collection dựa trên cache Scryfall.
    Gọi sau import + enrich để dedup reprint chính xác."""
    sid = current_session()
    with cursor() as cur:
        cur.execute(
            """UPDATE collection c
               SET oracle_name = COALESCE(sc.oracle_name, c.name)
               FROM scryfall_cards sc
               WHERE sc.name = c.name AND c.session_id = %s""",
            (sid,),
        )


def get_collection() -> list[dict]:
    """Collection gộp theo oracle_name (đã dedup reprint)."""
    with cursor() as cur:
        cur.execute(
            """SELECT COALESCE(oracle_name, name) AS name, SUM(quantity)::int AS quantity
               FROM collection WHERE session_id = %s
               GROUP BY COALESCE(oracle_name, name)
               ORDER BY 1""",
            (current_session(),),
        )
        return cur.fetchall()


def get_collection_names() -> set[str]:
    """Set oracle_name để check ownership.
    FIX 1: dùng oracle_name → mọi reprint đều tính là owned."""
    with cursor() as cur:
        cur.execute(
            """SELECT DISTINCT COALESCE(oracle_name, name) AS canon
               FROM collection WHERE session_id = %s""",
            (current_session(),),
        )
        return {r["canon"] for r in cur.fetchall()}


def get_collection_raw_names() -> set[str]:
    """Printing name gốc — dùng khi cần refetch giá theo đúng bản in."""
    with cursor() as cur:
        cur.execute(
            "SELECT DISTINCT name FROM collection WHERE session_id = %s",
            (current_session(),),
        )
        return {r["name"] for r in cur.fetchall()}


def clear_collection():
    with cursor() as cur:
        cur.execute("DELETE FROM collection WHERE session_id = %s", (current_session(),))


# ── Scryfall oracle data ──────────────────────────────────────────────────────

def get_scryfall_card(name: str) -> dict | None:
    with cursor() as cur:
        cur.execute("SELECT * FROM scryfall_cards WHERE name = %s", (name,))
        return cur.fetchone()


def get_scryfall_cards(names: list[str]) -> dict[str, dict]:
    """Batch lookup. Engine gọi get_scryfall_card() từng card trong vòng lặp —
    với DB qua mạng thì cần bản batch này để warm cache trước."""
    if not names:
        return {}
    with cursor() as cur:
        cur.execute("SELECT * FROM scryfall_cards WHERE name = ANY(%s)", (list(names),))
        return {r["name"]: r for r in cur.fetchall()}


def upsert_scryfall_card(data: dict):
    """Upsert oracle data. Prices lưu riêng qua upsert_price()."""
    with cursor() as cur:
        cur.execute(
            """INSERT INTO scryfall_cards
               (name, oracle_id, oracle_name, mana_cost, cmc, type_line, oracle_text,
                color_identity, keywords, legalities, scryfall_id, fetched_at)
               VALUES (%(name)s, %(oracle_id)s, %(oracle_name)s, %(mana_cost)s, %(cmc)s,
                       %(type_line)s, %(oracle_text)s, %(color_identity)s, %(keywords)s,
                       %(legalities)s, %(scryfall_id)s, now())
               ON CONFLICT (name) DO UPDATE SET
                 oracle_id=EXCLUDED.oracle_id,
                 oracle_name=EXCLUDED.oracle_name,
                 mana_cost=EXCLUDED.mana_cost,
                 cmc=EXCLUDED.cmc,
                 type_line=EXCLUDED.type_line,
                 oracle_text=EXCLUDED.oracle_text,
                 color_identity=EXCLUDED.color_identity,
                 keywords=EXCLUDED.keywords,
                 legalities=EXCLUDED.legalities,
                 fetched_at=EXCLUDED.fetched_at""",
            data,
        )


def get_missing_scryfall_cards(names: list[str]) -> list[str]:
    """Card chưa có oracle data hoặc đã stale (> ORACLE_TTL_DAYS)."""
    if not names:
        return []
    with cursor() as cur:
        cur.execute(
            """SELECT name FROM scryfall_cards
               WHERE name = ANY(%s) AND fetched_at > now() - make_interval(days => %s)""",
            (list(names), ORACLE_TTL_DAYS),
        )
        cached = {r["name"] for r in cur.fetchall()}
    return [n for n in names if n not in cached]


def get_collection_color_rows(names: list[str]) -> list[dict]:
    """color_identity + type_line của các card cho trước.

    commander_picker dùng để suy ra dải màu của collection. Trước đây picker
    tự viết SQL và gọi get_conn() trực tiếp — query đã chuyển về đây để lớp
    engine không còn phụ thuộc phương ngữ SQL."""
    if not names:
        return []
    with cursor() as cur:
        cur.execute(
            """SELECT color_identity, type_line FROM scryfall_cards
               WHERE name = ANY(%s)""",
            (list(names),),
        )
        return cur.fetchall()


# ── Scryfall prices (TTL ngắn hơn oracle data) ────────────────────────────────

def upsert_price(oracle_name: str, usd: str | None, usd_foil: str | None, eur: str | None):
    with cursor() as cur:
        cur.execute(
            """INSERT INTO scryfall_prices (oracle_name, usd, usd_foil, eur, updated_at)
               VALUES (%s, %s, %s, %s, now())
               ON CONFLICT (oracle_name) DO UPDATE SET
                 usd=EXCLUDED.usd, usd_foil=EXCLUDED.usd_foil,
                 eur=EXCLUDED.eur, updated_at=EXCLUDED.updated_at""",
            (oracle_name, usd, usd_foil, eur),
        )


def get_price_usd(oracle_name: str) -> float | None:
    """Giá USD dạng float. None nếu chưa có hoặc đã stale."""
    with cursor() as cur:
        cur.execute(
            """SELECT usd FROM scryfall_prices
               WHERE oracle_name = %s AND updated_at > now() - make_interval(days => %s)""",
            (oracle_name, PRICE_TTL_DAYS),
        )
        row = cur.fetchone()
    if not row or not row["usd"]:
        return None
    try:
        return float(row["usd"])
    except (ValueError, TypeError):
        return None


def get_prices_usd(oracle_names: list[str]) -> dict[str, float]:
    """Batch của get_price_usd. Buylist cần giá cho cả trăm card một lúc."""
    if not oracle_names:
        return {}
    with cursor() as cur:
        cur.execute(
            """SELECT oracle_name, usd FROM scryfall_prices
               WHERE oracle_name = ANY(%s)
                 AND updated_at > now() - make_interval(days => %s)""",
            (list(oracle_names), PRICE_TTL_DAYS),
        )
        rows = cur.fetchall()
    out = {}
    for r in rows:
        try:
            out[r["oracle_name"]] = float(r["usd"])
        except (ValueError, TypeError):
            continue
    return out


def get_stale_price_cards(oracle_names: list[str]) -> list[str]:
    """oracle_names cần refresh giá (chưa có hoặc > PRICE_TTL_DAYS).
    FIX 2: TTL riêng 7 ngày, không dùng chung 30 ngày với oracle data."""
    if not oracle_names:
        return []
    with cursor() as cur:
        cur.execute(
            """SELECT oracle_name FROM scryfall_prices
               WHERE oracle_name = ANY(%s)
                 AND updated_at > now() - make_interval(days => %s)""",
            (list(oracle_names), PRICE_TTL_DAYS),
        )
        fresh = {r["oracle_name"] for r in cur.fetchall()}
    return [n for n in oracle_names if n not in fresh]


# ── EDHREC data ───────────────────────────────────────────────────────────────

def get_edhrec_cards(commander_slug: str, max_age_days: int = EDHREC_TTL_DAYS) -> list[dict]:
    with cursor() as cur:
        cur.execute(
            """SELECT * FROM edhrec_data
               WHERE commander_slug = %s
                 AND fetched_at > now() - make_interval(days => %s)
               ORDER BY synergy DESC""",
            (commander_slug, max_age_days),
        )
        return cur.fetchall()


def get_edhrec_deck_counts(slugs: list[str], max_age_days: int = EDHREC_TTL_DAYS) -> dict[str, int]:
    """Số card đã cache cho mỗi slug. Dùng để biết commander nào đã seed
    mà không phải kéo hết vài trăm nghìn dòng về."""
    if not slugs:
        return {}
    with cursor() as cur:
        cur.execute(
            """SELECT commander_slug, COUNT(*)::int AS n FROM edhrec_data
               WHERE commander_slug = ANY(%s)
                 AND fetched_at > now() - make_interval(days => %s)
               GROUP BY commander_slug""",
            (list(slugs), max_age_days),
        )
        return {r["commander_slug"]: r["n"] for r in cur.fetchall()}


def upsert_edhrec_cards(commander_slug: str, cards: list[dict]):
    with cursor() as cur:
        cur.execute("DELETE FROM edhrec_data WHERE commander_slug = %s", (commander_slug,))
        if not cards:
            return
        cur.executemany(
            """INSERT INTO edhrec_data
               (commander_slug, card_name, synergy, inclusion, num_decks,
                potential_decks, slot_tag, fetched_at)
               VALUES (%(commander_slug)s, %(card_name)s, %(synergy)s, %(inclusion)s,
                       %(num_decks)s, %(potential_decks)s, %(slot_tag)s, now())
               ON CONFLICT (commander_slug, card_name) DO NOTHING""",
            [{"commander_slug": commander_slug, **c} for c in cards],
        )


# ── Banned list ───────────────────────────────────────────────────────────────

def get_banned_list() -> set[str]:
    with cursor() as cur:
        cur.execute("SELECT name FROM banned_list")
        return {r["name"] for r in cur.fetchall()}


def update_banned_list(names: list[str]):
    with cursor() as cur:
        cur.execute("DELETE FROM banned_list")
        if names:
            cur.executemany(
                "INSERT INTO banned_list (name) VALUES (%s) ON CONFLICT DO NOTHING",
                [(n,) for n in names],
            )


# ── Commanders ────────────────────────────────────────────────────────────────

def upsert_commanders(commanders: list[dict]):
    if not commanders:
        return
    with cursor() as cur:
        cur.executemany(
            """INSERT INTO commanders (name, slug, color_identity, is_legal,
                                       is_partner, partner_name, fetched_at)
               VALUES (%(name)s, %(slug)s, %(color_identity)s, true,
                       %(is_partner)s, %(partner_name)s, now())
               ON CONFLICT (name) DO UPDATE SET
                 slug=EXCLUDED.slug,
                 color_identity=EXCLUDED.color_identity,
                 is_partner=EXCLUDED.is_partner,
                 partner_name=EXCLUDED.partner_name,
                 fetched_at=EXCLUDED.fetched_at""",
            # scryfall.fetch_all_commanders trả is_partner dạng int 0/1.
            [{**c, "is_partner": bool(c.get("is_partner"))} for c in commanders],
        )


def get_all_commanders() -> list[dict]:
    with cursor() as cur:
        cur.execute("SELECT * FROM commanders WHERE is_legal ORDER BY name")
        return cur.fetchall()


# ── Stats ─────────────────────────────────────────────────────────────────────

def get_db_stats() -> dict:
    """Đếm dòng mỗi bảng + độ tươi của seed. `/api/health` và seeder cùng dùng."""
    counts = ", ".join(f"(SELECT COUNT(*) FROM {t})::bigint AS {t}" for t in _TABLES)
    with cursor() as cur:
        cur.execute(f"SELECT {counts}")
        stats = dict(cur.fetchone())

        cur.execute(
            """SELECT COUNT(*)::bigint AS n FROM scryfall_prices
               WHERE updated_at <= now() - make_interval(days => %s)""",
            (PRICE_TTL_DAYS,),
        )
        stats["stale_prices"] = cur.fetchone()["n"]

        cur.execute("SELECT MAX(fetched_at) AS t FROM edhrec_data")
        last = cur.fetchone()["t"]
        stats["edhrec_last_seeded"] = last.isoformat() if last else None

        cur.execute(
            "SELECT COUNT(DISTINCT commander_slug)::bigint AS n FROM edhrec_data"
        )
        stats["commanders_seeded"] = cur.fetchone()["n"]
    return stats


def ping() -> bool:
    """Kiểm tra DB sống. Không tạo schema, không ghi gì."""
    return diagnose()["connected"]


_DSN_PATTERN = re.compile(r"postgres(?:ql)?://[^\s\"']+", re.I)


def diagnose() -> dict:
    """Vì sao không kết nối được. `/api/health` trả về khi DB chết —
    trên Vercel không đọc được log nên thông tin này phải nằm trong response.

    Message của psycopg có thể chứa nguyên DSN (kèm password), nên phải scrub
    trước khi trả ra ngoài."""
    dsn_var = next(
        (v for v in ("DATABASE_URL", "POSTGRES_URL") if os.getenv(v)), None
    )
    result = {"connected": False, "dsnVar": dsn_var, "error": None}
    if not dsn_var:
        result["error"] = "Không thấy DATABASE_URL hoặc POSTGRES_URL trong môi trường"
        return result

    try:
        with cursor() as cur:
            cur.execute("SELECT 1")
            result["connected"] = cur.fetchone() is not None
    except Exception as e:  # noqa: BLE001 — health check phải báo được mọi lỗi
        scrubbed = _DSN_PATTERN.sub("<dsn>", str(e))
        result["error"] = f"{type(e).__name__}: {scrubbed[:300]}"
    return result
