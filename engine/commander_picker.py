"""
engine/commander_picker.py — Score và rank commanders dựa trên collection.

Hai mode:
  owned_only=True  → chỉ commander có trong collection
  owned_only=False → tất cả commander hợp lệ, ưu tiên commander
                     giúp tái dùng nhiều card nhất từ collection
"""

import json
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
    Score tất cả commanders và trả về top N.

    Args:
        top_n: số commander muốn trả về
        owned_only: chỉ xét commander đang có trong collection

    Returns:
        list[CommanderScore] sorted by composite_score desc
    """
    collection_names = cache.get_collection_names()
    if not collection_names:
        raise RuntimeError("Collection trống. Chạy `python cli.py import` trước.")

    all_commanders = cache.get_all_commanders()
    if not all_commanders:
        print("  Commander list chưa có cache. Fetching từ Scryfall...")
        scryfall.fetch_all_commanders()
        all_commanders = cache.get_all_commanders()

    candidates = []
    for cmd in all_commanders:
        is_owned = cmd["name"] in collection_names
        if owned_only and not is_owned:
            continue
        candidates.append(cmd)

    print(f"  Đang score {len(candidates)} commanders...")

    scored = []
    for cmd in candidates:
        score = _score_commander(cmd, collection_names)
        scored.append(score)

    scored.sort(key=lambda s: s.composite_score, reverse=True)
    return scored[:top_n]


def _score_commander(cmd, collection_names: set[str]) -> CommanderScore:
    slug = cmd["slug"]
    color_identity = json.loads(cmd["color_identity"] or "[]")
    is_owned = cmd["name"] in collection_names

    # Lấy EDHREC cards cho commander này
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

    import math
    popularity_score = math.log10(max(edhrec_num_decks, 1)) / 6  # normalize to ~0-1

    owned_bonus = 0.10 if is_owned else 0.0

    composite = (
        0.40 * overlap_pct
        + 0.30 * min(avg_synergy * 5, 1.0)  # synergy thường 0-0.5, scale lên
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
