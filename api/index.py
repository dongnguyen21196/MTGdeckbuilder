"""
index.py — FastAPI entrypoint cho MTG Deck Builder.

Vercel nạp biến `app` ở file này (`entrypoint: "index:app"` trong vercel.json).
Toàn bộ logic nghiệp vụ nằm trong lib/ và được import nguyên trạng từ bản CLI cũ —
file này chỉ là lớp HTTP mỏng.
"""

import os
import sys
from pathlib import Path

# Không dựa vào cwd của runtime: Vercel chạy function với working directory là
# gốc project, không phải thư mục chứa file này.
#   api/      → routes.py, serializers.py
#   api/lib/  → engine, db, enrichers... dùng import tuyệt đối (`from engine ...`),
#               nên chính lib/ phải nằm trên sys.path chứ không phải api/lib.
_HERE = Path(__file__).resolve().parent
for _path in (_HERE, _HERE / "lib"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

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
