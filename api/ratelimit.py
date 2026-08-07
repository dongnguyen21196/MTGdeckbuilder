"""
ratelimit.py — Giới hạn tần suất theo IP cho các endpoint tốn tài nguyên.

App là public, không đăng nhập, nên không có gì ngăn một script gọi lặp vô
hạn. Ba endpoint nặng (import, build, rank) đều chiếm 1769MB RAM và có thể
chạy tới 60s, đủ để đốt hết quota Function của Vercel Hobby và làm phình
bảng collection bằng session rác.

Bộ đếm nằm trong Postgres chứ không phải biến in-memory: Function serverless
không giữ state giữa các lần gọi, hai request liên tiếp có thể rơi vào hai
instance khác nhau.
"""

import os

from fastapi import HTTPException, Request

from db import cache


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


# Cửa sổ chung cho mọi bucket. 1 giờ.
WINDOW_SECONDS = _env_int("RATE_LIMIT_WINDOW_SECONDS", 3600)

# Ngưỡng mỗi IP trong một cửa sổ. Đặt 0 để tắt (dùng khi chạy local).
# Rank đắt nhất (~8s, có thể tới 60s) nên chặt tay nhất; build rẻ hơn
# (~1.3s) nên rộng hơn để người dùng thật xem được nhiều deck.
LIMITS = {
    "import": _env_int("RATE_LIMIT_IMPORT", 10),
    "build": _env_int("RATE_LIMIT_BUILD", 40),
    "rank": _env_int("RATE_LIMIT_RANK", 12),
}


def client_ip(request: Request) -> str:
    """IP thật của client.

    Ưu tiên header do chính Vercel đặt — `x-forwarded-for` client tự gửi được
    nên không tin được khi đứng một mình."""
    for header in ("x-vercel-forwarded-for", "x-real-ip"):
        value = request.headers.get(header)
        if value:
            return value.strip()

    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()

    return request.client.host if request.client else "unknown"


def enforce(request: Request, action: str) -> None:
    """Tăng bộ đếm và ném 429 nếu vượt ngưỡng.

    Request vượt ngưỡng vẫn được đếm, nhưng `window_start` không dịch — nên
    kẻ gọi dồn dập không tự kéo dài thời gian bị chặn của chính mình."""
    limit = LIMITS[action]
    if limit <= 0:
        return

    hits, reset_in = cache.hit_rate_limit(
        f"{action}:{client_ip(request)}", WINDOW_SECONDS
    )
    if hits > limit:
        raise HTTPException(
            429,
            f"Quá nhiều yêu cầu. Giới hạn {limit} lần mỗi "
            f"{WINDOW_SECONDS // 60} phút. Thử lại sau {reset_in} giây.",
            headers={"Retry-After": str(reset_in)},
        )
