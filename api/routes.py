"""
routes.py — Lớp HTTP mỏng phủ lên engine.

Mọi tính toán đều nằm trong lib/. Các handler ở đây chỉ làm ba việc: gắn
session, gọi hàm engine, và serialize kết quả.
"""

import io
import os
import secrets
import time

import psycopg
from fastapi import APIRouter, File, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field

from db import cache
from engine.commander_picker import pick_commanders
from engine.deck_builder import build_deck
from engine.scorer import score_deck
from enrichers import scryfall

import serializers

router = APIRouter()

SESSION_COOKIE = "mtg_sid"
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 ngày
MAX_UPLOAD_BYTES = 5 * 1024 * 1024   # CSV collection lớn nhất ~2MB

# Cookie Secure không gửi được qua http:// nên local dev sẽ mất session.
# Trên Vercel luôn là https.
COOKIE_SECURE = os.getenv("VERCEL") is not None


# ── Session ───────────────────────────────────────────────────────────────────

def _bind_session(request: Request, response: Response) -> str:
    """Lấy session từ cookie, tạo mới nếu chưa có, rồi gắn vào ContextVar để
    lớp db biết collection nào thuộc về ai."""
    sid = request.cookies.get(SESSION_COOKIE)
    if not sid or len(sid) < 16:
        sid = secrets.token_urlsafe(24)
        response.set_cookie(
            SESSION_COOKIE,
            sid,
            max_age=SESSION_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=COOKIE_SECURE,
        )
    cache.set_session(sid)
    return sid


# ── Health ────────────────────────────────────────────────────────────────────

@router.get("/health")
def health(response: Response):
    """Trạng thái DB + độ tươi của dữ liệu seed. Không cần session.

    Trả 200 kèm lý do khi DB hỏng thay vì 503 trần — trên Vercel không đọc
    được log function, nên chẩn đoán phải nằm trong chính response."""
    diag = cache.diagnose()
    if not diag["connected"]:
        response.status_code = 503
        return {"status": "error", "database": "disconnected", "ready": False, **diag}

    try:
        stats = cache.get_db_stats()
    except psycopg.errors.UndefinedTable:
        # Database mới toanh, seeder chưa chạy lần nào. Schema là DDL idempotent
        # nên tạo luôn ở đây, khỏi bắt người dùng làm thêm một bước thủ công.
        cache.init_db()
        stats = cache.get_db_stats()

    return {
        "status": "ok",
        "database": "connected",
        "seed": {
            "commanders": stats["commanders"],
            "commandersSeeded": stats["commanders_seeded"],
            "bannedCards": stats["banned_list"],
            "scryfallCards": stats["scryfall_cards"],
            "edhrecRows": stats["edhrec_data"],
            "lastSeeded": stats["edhrec_last_seeded"],
        },
        "ready": stats["commanders"] > 0 and stats["commanders_seeded"] > 0,
    }


# ── Collection ────────────────────────────────────────────────────────────────

@router.post("/collection/import")
async def import_collection(
    request: Request,
    response: Response,
    file: UploadFile = File(..., description="CSV export từ Archidekt"),
):
    """Nhận CSV export của Archidekt, lưu vào collection của session hiện tại,
    rồi enrich oracle data cho những card chưa có trong cache."""
    _bind_session(request, response)

    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File quá lớn (tối đa {MAX_UPLOAD_BYTES // 1024 // 1024}MB)")
    if not raw:
        raise HTTPException(400, "File rỗng")

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(400, "File không phải UTF-8. Export lại từ Archidekt.")

    from importers.archidekt_csv import parse_csv

    try:
        cards = parse_csv(io.StringIO(text))
    except ValueError as e:
        raise HTTPException(400, str(e))

    if not cards:
        raise HTTPException(400, "Không đọc được card nào từ file.")

    cache.upsert_collection(cards)

    names = list({c["name"] for c in cards})
    missing = cache.get_missing_scryfall_cards(names)
    if missing:
        scryfall.enrich_cards(missing)
    cache.refresh_collection_oracle_names()

    return {
        "imported": len(cards),
        "uniqueCards": len(names),
        "totalCopies": sum(c["quantity"] for c in cards),
        "enrichedFromScryfall": len(missing),
    }


@router.get("/collection")
def get_collection(request: Request, response: Response):
    _bind_session(request, response)
    rows = cache.get_collection()
    return {
        "cards": [{"name": r["name"], "quantity": r["quantity"]} for r in rows],
        "uniqueCards": len(rows),
        "totalCopies": sum(r["quantity"] for r in rows),
    }


@router.delete("/collection")
def delete_collection(request: Request, response: Response):
    _bind_session(request, response)
    cache.clear_collection()
    return {"cleared": True}


# ── Commanders ────────────────────────────────────────────────────────────────

@router.get("/commanders/rank")
def rank_commanders(
    request: Request,
    response: Response,
    top: int = Query(5, ge=1, le=20),
    ownedOnly: bool = Query(True),
):
    """Chấm điểm commander theo collection rồi build deck cho top N.

    Đây là endpoint nặng nhất. Nó chỉ chạy được trong giới hạn 60s khi dữ liệu
    EDHREC đã seed sẵn — commander nào chưa seed sẽ phải gọi mạng ngay trong
    request, mỗi lần ~1s."""
    _bind_session(request, response)

    if not cache.get_collection_names():
        raise HTTPException(409, "Collection trống. Upload CSV trước.")

    started = time.monotonic()
    scores = pick_commanders(top_n=top, owned_only=ownedOnly, cached_only=True)
    if not scores:
        raise HTTPException(
            404,
            "Chưa chấm được commander nào. Hoặc collection không có commander "
            "phù hợp, hoặc dữ liệu EDHREC cho những commander đó chưa được seed "
            "xong. Thử bỏ chọn 'chỉ commander đang sở hữu'.",
        )

    decks = []
    for cs in scores:
        deck = build_deck(cs.name, cs.slug)
        if not deck.cards:
            continue  # chưa có EDHREC data cho commander này
        decks.append(serializers.deck_summary_json(deck, score_deck(deck)))

    decks.sort(key=lambda d: -d["score"]["composite"])
    return {
        "decks": decks,
        "candidatesScored": len(scores),
        "elapsedMs": int((time.monotonic() - started) * 1000),
    }


# ── Decks ─────────────────────────────────────────────────────────────────────

class BuildRequest(BaseModel):
    commander: str = Field(min_length=1, max_length=200)
    partner: str | None = Field(default=None, max_length=200)


@router.post("/decks/build")
def build(request: Request, response: Response, body: BuildRequest):
    """Build một deck cụ thể và trả về mọi góc nhìn cùng lúc — chi tiết,
    decklist text, gợi ý swap, buylist.

    Gộp trong một response vì mỗi lần build tốn vài giây; tách thành 4 endpoint
    sẽ khiến UI phải build lại deck 4 lần cho cùng một commander."""
    _bind_session(request, response)

    if not cache.get_collection_names():
        raise HTTPException(409, "Collection trống. Upload CSV trước.")

    slug = scryfall._to_slug(body.commander)
    deck = build_deck(body.commander, slug, partner_name=body.partner)

    if not deck.cards:
        raise HTTPException(
            404,
            f"Không có dữ liệu EDHREC cho '{body.commander}'. "
            "Kiểm tra lại tên hoặc đợi lần seed kế tiếp.",
        )

    return {
        "deck": serializers.deck_detail_json(deck),
        "decklist": serializers.decklist_text(deck),
        "swaps": serializers.swaps_json(deck),
        "buylist": serializers.buylist_json(deck),
    }
