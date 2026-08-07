"""
filters/banned_list.py — Lọc card vi phạm luật EDH Commander.

Rules:
  1. Banned list chính thức (sync từ Scryfall)
  2. Color identity: card phải nằm trong color identity của commander
  3. Singleton: chỉ 1 bản mỗi card (trừ basic lands)
"""

import json
from db import cache


BASIC_LANDS = {
    "Plains", "Island", "Swamp", "Mountain", "Forest",
    "Wastes", "Snow-Covered Plains", "Snow-Covered Island",
    "Snow-Covered Swamp", "Snow-Covered Mountain", "Snow-Covered Forest",
}


def filter_banned(card_names: list[str]) -> tuple[list[str], list[str]]:
    """
    Tách danh sách card thành legal và banned.

    Returns:
        (legal_names, banned_names)
    """
    banned_set = cache.get_banned_list()
    if not banned_set:
        print("  [!] Banned list chưa có trong cache. Chạy: python cli.py update --banned-list")
        return card_names, []

    legal = [n for n in card_names if n not in banned_set]
    banned = [n for n in card_names if n in banned_set]
    return legal, banned


def filter_color_identity(
    card_names: list[str],
    commander_colors: list[str],
    scryfall_data: dict[str, dict],
) -> tuple[list[str], list[str]]:
    """
    Lọc card không nằm trong color identity của commander.

    Args:
        card_names: danh sách card cần check
        commander_colors: color identity của commander, vd ["W", "U", "B", "G"]
        scryfall_data: dict {name: scryfall_row}

    Returns:
        (legal_names, off_color_names)
    """
    commander_color_set = set(commander_colors)
    legal = []
    off_color = []

    for name in card_names:
        if name in BASIC_LANDS:
            legal.append(name)
            continue

        card = scryfall_data.get(name)
        if not card:
            # Nếu không có data, cho qua (safe default)
            legal.append(name)
            continue

        ci_raw = card.get("color_identity", "[]")
        if isinstance(ci_raw, str):
            card_colors = set(json.loads(ci_raw))
        else:
            card_colors = set(ci_raw)

        if card_colors.issubset(commander_color_set):
            legal.append(name)
        else:
            off_color.append(name)

    return legal, off_color


def apply_singleton(cards: list[dict]) -> list[dict]:
    """
    Enforce singleton rule: mỗi card tối đa 1 bản, basic land không giới hạn.
    cards: list of {name, quantity, ...}
    """
    seen = set()
    result = []
    for card in cards:
        name = card["name"]
        if name in BASIC_LANDS:
            result.append(card)
        elif name not in seen:
            seen.add(name)
            result.append({**card, "quantity": 1})
    return result


def is_basic_land(name: str) -> bool:
    return name in BASIC_LANDS
