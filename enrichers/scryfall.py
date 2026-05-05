"""
enrichers/scryfall.py — Lấy card data từ Scryfall API.

Scryfall API docs: https://scryfall.com/docs/api
Rate limit: 50–100ms delay, max 10 req/sec.
Dùng /cards/collection endpoint để batch 75 cards/request.
"""

import json
import time
import requests
from db import cache

SCRYFALL_COLLECTION_URL = "https://api.scryfall.com/cards/collection"
SCRYFALL_BULK_COMMANDERS_URL = "https://api.scryfall.com/cards/search"
BATCH_SIZE = 75
SLEEP_BETWEEN_BATCHES = 0.1


def enrich_cards(card_names: list[str]) -> dict[str, dict]:
    """
    Lấy Scryfall data cho danh sách card, ưu tiên cache.

    Returns:
        dict: {card_name: scryfall_data}
    """
    missing = cache.get_missing_scryfall_cards(card_names)

    if missing:
        print(f"  Fetching {len(missing)} cards từ Scryfall...")
        fetched = _batch_fetch(missing)
        for card_data in fetched.values():
            cache.upsert_scryfall_card(card_data)

    result = {}
    for name in card_names:
        row = cache.get_scryfall_card(name)
        if row:
            result[name] = dict(row)
    return result


def _batch_fetch(names: list[str]) -> dict[str, dict]:
    """Batch fetch qua /cards/collection, 75 card mỗi lần."""
    result = {}

    for i in range(0, len(names), BATCH_SIZE):
        batch = names[i : i + BATCH_SIZE]
        identifiers = [{"name": n} for n in batch]

        resp = requests.post(
            SCRYFALL_COLLECTION_URL,
            json={"identifiers": identifiers},
            headers={"Accept": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        for card in data.get("data", []):
            name = card.get("name", "")
            if not name:
                continue
            result[name] = _normalize_card(card)

        not_found = data.get("not_found", [])
        if not_found:
            print(f"  [!] Không tìm thấy trên Scryfall: {[n['name'] for n in not_found]}")

        time.sleep(SLEEP_BETWEEN_BATCHES)

    return result


def _normalize_card(card: dict) -> dict:
    """Chuẩn hóa Scryfall card object thành schema nội bộ."""
    prices = card.get("prices", {})
    legalities = card.get("legalities", {})

    return {
        "name": card.get("name", ""),
        "oracle_id": card.get("oracle_id", ""),
        "mana_cost": card.get("mana_cost", ""),
        "cmc": card.get("cmc", 0.0),
        "type_line": card.get("type_line", ""),
        "oracle_text": card.get("oracle_text", ""),
        "color_identity": json.dumps(card.get("color_identity", [])),
        "keywords": json.dumps(card.get("keywords", [])),
        "legalities": json.dumps(legalities),
        "prices": json.dumps({
            "usd": prices.get("usd"),
            "usd_foil": prices.get("usd_foil"),
            "eur": prices.get("eur"),
        }),
        "scryfall_id": card.get("id", ""),
    }


def fetch_all_commanders() -> list[dict]:
    """
    Lấy tất cả commander hợp lệ từ Scryfall.
    Query: is:commander format:commander legal:commander
    """
    print("Fetching danh sách commanders từ Scryfall...")
    commanders = []
    url = SCRYFALL_BULK_COMMANDERS_URL
    params = {
        "q": "is:commander format:commander",
        "order": "name",
        "unique": "cards",
    }

    while url:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        for card in data.get("data", []):
            commanders.append({
                "name": card["name"],
                "slug": _to_slug(card["name"]),
                "color_identity": json.dumps(card.get("color_identity", [])),
            })

        url = data.get("next_page")
        params = {}  # next_page URL đã có params
        time.sleep(SLEEP_BETWEEN_BATCHES)

    print(f"  Tìm thấy {len(commanders)} commanders.")
    cache.upsert_commanders(commanders)
    return commanders


def fetch_banned_list() -> list[str]:
    """
    Lấy banned list EDH Commander từ Scryfall.
    Query: banned:commander
    """
    print("Fetching banned list từ Scryfall...")
    banned = []
    url = SCRYFALL_BULK_COMMANDERS_URL
    params = {"q": "banned:commander", "unique": "cards", "order": "name"}

    while url:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        banned.extend(c["name"] for c in data.get("data", []))
        url = data.get("next_page")
        params = {}
        time.sleep(SLEEP_BETWEEN_BATCHES)

    cache.update_banned_list(banned)
    print(f"  {len(banned)} cards bị banned trong EDH.")
    return banned


def is_commander_legal(card_data: dict) -> bool:
    """Kiểm tra card có legal trong Commander format không."""
    legalities = card_data.get("legalities", "{}")
    if isinstance(legalities, str):
        legalities = json.loads(legalities)
    return legalities.get("commander", "not_legal") == "legal"


def _to_slug(name: str) -> str:
    """Chuyển card name thành EDHREC slug format."""
    import re
    slug = name.lower()
    slug = re.sub(r"[',\.]", "", slug)
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug
