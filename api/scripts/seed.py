#!/usr/bin/env python3
"""
scripts/seed.py — Nạp dữ liệu toàn cục vào Postgres.

Chạy trên GitHub Actions, KHÔNG chạy trên Vercel. Lý do:
  - Seed EDHREC cho ~1.800 commander với delay 1s/request mất ~30 phút,
    trong khi Vercel Function trần 60s.
  - Vercel Hobby chỉ cho cron 1 lần/ngày và vẫn bị giới hạn thời gian đó.
  - Chạy từ IP GitHub thay vì IP Vercel giúp giảm rủi ro edge bị EDHREC chặn.

Sau khi seed xong, mọi request của app chỉ còn đọc DB + tính toán thuần CPU,
nên vừa khít giới hạn serverless.

Script idempotent và resumable: dữ liệu còn trong TTL sẽ bị bỏ qua, nên chạy
lại giữa chừng không tốn công làm lại.

  python scripts/seed.py --all
  python scripts/seed.py --commanders --banned
  python scripts/seed.py --edhrec --limit 200
  python scripts/seed.py --stats
"""

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

from db import cache  # noqa: E402
from enrichers import edhrec, scryfall  # noqa: E402

SCRYFALL_CHUNK = 500  # số card gửi cho enrich_cards mỗi lượt


def log(msg: str) -> None:
    print(msg, flush=True)


def seed_banned() -> int:
    log("→ Banned list…")
    names = scryfall.fetch_banned_list()
    log(f"  {len(names)} card bị ban")
    return len(names)


def seed_commanders() -> int:
    log("→ Danh sách commander…")
    commanders = scryfall.fetch_all_commanders()
    log(f"  {len(commanders)} commander hợp lệ")
    return len(commanders)


def seed_edhrec(limit: int | None = None) -> tuple[int, int]:
    """Fetch EDHREC cho từng commander chưa có dữ liệu tươi.

    Trả về (số commander đã fetch, số bỏ qua vì còn tươi)."""
    commanders = cache.get_all_commanders()
    if not commanders:
        log("  [!] Chưa có commander nào. Chạy --commanders trước.")
        return 0, 0

    slugs = [c["slug"] for c in commanders if c["slug"]]
    fresh = cache.get_edhrec_deck_counts(slugs)
    todo = [c for c in commanders if c["slug"] and c["slug"] not in fresh]

    if limit:
        todo = todo[:limit]

    log(f"→ EDHREC: {len(todo)} cần fetch, {len(fresh)} còn tươi (bỏ qua)")
    if not todo:
        return 0, len(fresh)

    started = time.monotonic()
    fetched = 0
    for i, c in enumerate(todo, 1):
        cards = edhrec.get_commander_cards(c["slug"])
        if cards:
            fetched += 1
        if i % 25 == 0 or i == len(todo):
            rate = i / max(time.monotonic() - started, 0.001)
            eta = (len(todo) - i) / max(rate, 0.001)
            log(f"  {i}/{len(todo)}  ({fetched} có data)  ETA {eta / 60:.1f} phút")

    return fetched, len(fresh)


def seed_scryfall() -> int:
    """Nạp oracle data cho mọi card từng xuất hiện trong dữ liệu EDHREC.

    Đây là thứ engine cần để chấm điểm — thiếu nó thì request của user phải tự
    gọi Scryfall và sẽ chạm trần thời gian."""
    with cache.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT e.card_name AS name FROM edhrec_data e
               LEFT JOIN scryfall_cards s ON s.name = e.card_name
               WHERE s.name IS NULL"""
        )
        names = [r["name"] for r in cur.fetchall()]

    log(f"→ Scryfall: {len(names)} card chưa có oracle data")
    if not names:
        return 0

    for i in range(0, len(names), SCRYFALL_CHUNK):
        chunk = names[i:i + SCRYFALL_CHUNK]
        scryfall.enrich_cards(chunk)
        log(f"  {min(i + SCRYFALL_CHUNK, len(names))}/{len(names)}")

    return len(names)


def cleanup() -> int:
    days = int(os.getenv("SESSION_RETENTION_DAYS", "30"))
    removed = cache.drop_stale_sessions(days)
    log(f"→ Dọn {removed} dòng collection cũ hơn {days} ngày")
    return removed


def print_stats() -> None:
    stats = cache.get_db_stats()
    log("\nThống kê database:")
    for k, v in stats.items():
        log(f"  {k:<22} {v}")


def main() -> int:
    p = argparse.ArgumentParser(description="Seed dữ liệu toàn cục cho MTG Deck Builder")
    p.add_argument("--all", action="store_true", help="Chạy toàn bộ các bước")
    p.add_argument("--banned", action="store_true")
    p.add_argument("--commanders", action="store_true")
    p.add_argument("--edhrec", action="store_true")
    p.add_argument("--scryfall", action="store_true")
    p.add_argument("--cleanup", action="store_true")
    p.add_argument("--stats", action="store_true")
    p.add_argument("--limit", type=int, default=None,
                   help="Giới hạn số commander fetch EDHREC trong lần chạy này")
    args = p.parse_args()

    steps = [args.banned, args.commanders, args.edhrec, args.scryfall,
             args.cleanup, args.stats]
    if not any(steps) and not args.all:
        p.error("Cần ít nhất một bước. Dùng --all để chạy hết.")

    started = time.monotonic()
    cache.init_db()
    log("Schema sẵn sàng.\n")

    if args.all or args.banned:
        seed_banned()
    if args.all or args.commanders:
        seed_commanders()
    if args.all or args.edhrec:
        seed_edhrec(limit=args.limit)
    if args.all or args.scryfall:
        seed_scryfall()
    if args.all or args.cleanup:
        cleanup()
    if args.all or args.stats:
        print_stats()

    log(f"\nXong sau {(time.monotonic() - started) / 60:.1f} phút.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
