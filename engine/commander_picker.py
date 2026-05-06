"""
engine/commander_picker.py — Score và rank commanders dựa trên collection.

Hai mode:
  owned_only=True  → chỉ commander có trong collection
  owned_only=False → tất cả commander hợp lệ, ưu tiên commander
                     giúp tái dùng nhiều card nhất từ collection

FIX Bug 1 — O(n×API) pre-filter:
  Trước khi gọi EDHREC, loại bỏ tất cả commanders có color identity
  không phải subset của màu trong collection. Giảm 1847 → ~50-200 candidates
  tùy collection, tránh hàng nghìn EDHREC calls lãng phí.

  Ví dụ: collection chỉ có card W/U/B → loại hết commander cần G hoặc R.
  Nếu collection rỗng màu (chỉ toàn colorless) → không lọc để tránh loại hết.
"""

import json
import math
from dataclasses import dataclass, field
from enrichers import scryfall, edhrec
from db import cache


@dataclass
class CommanderScore:
    name: str
    slug: str
    color_identity: list[str]
    is_owned: bool
    collection_overlap: int        # số card trong collection có synergy
    collection_overlap_pct: float  # % deck có thể fill từ collection
    edhrec_deck_count: int         # popularity trên EDHREC
    composite_score: float
    top_owned_cards: list[str] = field(default_factory=list)


def pick_commanders(
    top_n: int = 5,
    owned_only: bool = False,
) -> list[CommanderScore]:
    """
    Score commanders và trả về top N.

    Luồng xử lý:
      1. Lấy màu trong collection từ Scryfall cache
      2. Pre-filter: loại commanders có màu ngoài collection colors
      3. Với owned_only: chỉ giữ commanders có trong collection
      4. Gọi EDHREC chỉ cho candidates còn lại
      5. Score + sort + trả về top N

    Args:
        top_n: số commander muốn trả về
        owned_only: chỉ xét commander đang có trong collection
    """
    collection_names = cache.get_collection_names()
    if not collection_names:
        raise RuntimeError("Collection trống. Chạy `python cli.py import` trước.")

    all_commanders = cache.get_all_commanders()
    if not all_commanders:
        print("  Commander list chưa có cache. Fetching từ Scryfall...")
        scryfall.fetch_all_commanders()
        all_commanders = cache.get_all_commanders()

    # --- Pre-filter bước 1: theo ownership ---
    candidates = []
    for cmd in all_commanders:
        is_owned = cmd["name"] in collection_names
        if owned_only and not is_owned:
            continue
        candidates.append(cmd)

    # --- Pre-filter bước 2: theo color identity của collection ---
    # Chỉ áp dụng khi không phải owned_only (đã filter rồi)
    # và khi collection đủ lớn để có ý nghĩa
    if not owned_only and len(candidates) > 200:
        collection_colors = _infer_collection_colors(collection_names)
        if collection_colors:  # bỏ qua nếu colorless hoàn toàn
            before = len(candidates)
            candidates = _filter_by_color_identity(candidates, collection_colors)
            print(f"  Color pre-filter: {before} → {len(candidates)} commanders "
                  f"(collection màu: {sorted(collection_colors)})")

    print(f"  Đang score {len(candidates)} commanders...")

    scored = []
    for cmd in candidates:
        score = _score_commander(cmd, collection_names)
        scored.append(score)

    scored.sort(key=lambda s: s.composite_score, reverse=True)
    return scored[:top_n]


# Ngưỡng tối thiểu để coi 1 màu là "có trong collection"
# Mục đích: loại bỏ màu "rác" từ 1-2 card lẻ làm loãng pre-filter
_COLOR_MIN_CARDS   = 20    # phải có ít nhất 20 card màu đó
_COLOR_MIN_RATIO   = 0.03  # hoặc ít nhất 3% tổng non-land cards


def _infer_collection_colors(collection_names: set[str]) -> set[str]:
    """
    Suy ra tập màu chủ đạo của collection từ Scryfall cache.

    FIX điểm 3 — Color threshold:
    Không lấy union toàn bộ màu (dễ bị nhiễu bởi 1-2 card lẻ).
    Thay vào đó đếm số card theo từng màu, chỉ coi màu là hợp lệ
    khi đạt ĐỒNG THỜI ít nhất một trong hai ngưỡng:
      - Absolute: >= _COLOR_MIN_CARDS card có màu đó trong CI
      - Relative: >= _COLOR_MIN_RATIO % tổng non-land cards

    Ví dụ: collection 3.000 card chủ yếu WU với 2 card R lẻ:
      W: 1.200 card → 40% → valid
      U: 1.400 card → 47% → valid
      R: 2 card   →  0.07% → LOẠI (< 3% và < 20 cards)

    Edge cases:
      - Collection trống / chưa enrich: trả về {} (không filter)
      - Collection toàn colorless: trả về {} (không filter)
      - Collection < 50 cards: hạ ngưỡng xuống 2 cards tuyệt đối
    """
    if not collection_names:
        return set()

    color_counts: dict[str, int] = {c: 0 for c in "WUBRG"}
    total_non_land = 0

    with cache.get_conn() as conn:
        placeholders = ",".join("?" * len(collection_names))
        rows = conn.execute(
            f"""SELECT color_identity, type_line
                FROM scryfall_cards
                WHERE name IN ({placeholders})""",
            list(collection_names),
        ).fetchall()

    for row in rows:
        type_line = row["type_line"] or ""
        if "Land" in type_line:
            continue  # bỏ qua lands khi đếm
        total_non_land += 1
        ci = json.loads(row["color_identity"] or "[]")
        for color in ci:
            if color in color_counts:
                color_counts[color] += 1

    if total_non_land == 0:
        return set()

    # Điều chỉnh ngưỡng absolute cho collection nhỏ
    min_cards = max(2, min(_COLOR_MIN_CARDS, total_non_land // 20))

    valid_colors: set[str] = set()
    for color, count in color_counts.items():
        ratio = count / total_non_land
        if count >= min_cards or ratio >= _COLOR_MIN_RATIO:
            valid_colors.add(color)

    return valid_colors


def _filter_by_color_identity(
    commanders: list, collection_colors: set[str]
) -> list:
    """
    Giữ lại commanders có color identity là subset của collection_colors.
    Commander colorless (CI=[]) luôn được giữ lại.
    Commander 5-color luôn được giữ lại nếu collection có đủ 5 màu.
    """
    filtered = []
    for cmd in commanders:
        ci = set(json.loads(cmd["color_identity"] or "[]"))
        if not ci or ci.issubset(collection_colors):
            filtered.append(cmd)
    return filtered


def _score_commander(cmd, collection_names: set[str]) -> CommanderScore:
    slug = cmd["slug"]
    color_identity = json.loads(cmd["color_identity"] or "[]")
    is_owned = cmd["name"] in collection_names

    # Lấy EDHREC cards cho commander này (từ cache nếu có)
    edhrec_cards = edhrec.get_commander_cards(slug)
    edhrec_card_names = {c["card_name"] for c in edhrec_cards}
    edhrec_num_decks = edhrec_cards[0]["potential_decks"] if edhrec_cards else 0

    # Collection overlap
    owned_and_synergy = edhrec_card_names & collection_names
    overlap_count = len(owned_and_synergy)
    overlap_pct = overlap_count / 99 if edhrec_cards else 0.0

    top_owned = sorted(
        [c for c in edhrec_cards if c["card_name"] in collection_names],
        key=lambda c: c["synergy"],
        reverse=True,
    )[:5]

    # Composite score:
    #   40% collection overlap %  (cao = ít phải mua)
    #   30% avg synergy của owned cards
    #   20% EDHREC popularity (log-normalized)
    #   10% bonus nếu commander đang sở hữu
    avg_synergy = (
        sum(c["synergy"] for c in edhrec_cards if c["card_name"] in collection_names)
        / max(overlap_count, 1)
    )

    popularity_score = math.log10(max(edhrec_num_decks, 1)) / 6  # normalize to ~0-1
    owned_bonus = 0.10 if is_owned else 0.0

    composite = (
        0.40 * overlap_pct
        + 0.30 * min(avg_synergy * 5, 1.0)
        + 0.20 * popularity_score
        + owned_bonus
    )

    return CommanderScore(
        name=cmd["name"],
        slug=slug,
        color_identity=color_identity,
        is_owned=is_owned,
        collection_overlap=overlap_count,
        collection_overlap_pct=overlap_pct,
        edhrec_deck_count=edhrec_num_decks,
        composite_score=composite,
        top_owned_cards=[c["card_name"] for c in top_owned],
    )
