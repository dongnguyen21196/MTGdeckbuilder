"""
index.py — FastAPI entrypoint cho MTG Deck Builder.

Vercel nạp biến `app` ở file này (xem `[tool.vercel] entrypoint` trong pyproject.toml).
Toàn bộ logic nghiệp vụ nằm trong lib/ và được import nguyên trạng từ bản CLI cũ —
file này chỉ là lớp HTTP mỏng.
"""

import os
import sys
from pathlib import Path

# lib/ chứa các package engine, db, enrichers... với import tuyệt đối
# (`from engine import ...`). Đưa lib/ lên sys.path để giữ nguyên các import đó.
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from routes import router  # noqa: E402

app = FastAPI(
    title="MTG Deck Builder API",
    version="1.0.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

# Trong production, web/ và api/ nằm sau cùng một domain nên không cần CORS.
# Khi chạy local, Next.js ở :3000 gọi sang API ở :8000.
if os.getenv("VERCEL") is None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Vercel Services rewrite `/api/(.*)` sang service này. Tài liệu không đảm bảo
# prefix `/api` có được giữ lại trong path mà function nhận được hay không, nên
# đăng ký router ở cả hai prefix — request nào tới cũng khớp đúng một route.
app.include_router(router, prefix="/api")
app.include_router(router, include_in_schema=False)
