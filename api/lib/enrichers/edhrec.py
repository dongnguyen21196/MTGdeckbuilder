"""
enrichers/edhrec.py — Lấy synergy data từ EDHREC JSON API.

Endpoint: https://json.edhrec.com/pages/commanders/{slug}.json
Không có official API — đây là undocumented JSON endpoint mà website dùng.
Courtesy: delay 1 giây giữa các request, cache aggressively (7 ngày mặc định).

Response structure:
  container.json_dict.cardlists[].cardviews[]:
    name, synergy, inclusion, num_decks, potential_decks
  container.json_dict.cardlists[].header:
    "High Synergy Cards", "Top Cards", "Creatures", "Instants", etc.
"""

import json
import time
import os
import requests
from db import cache

EDHREC_BASE = "https://json.edhrec.com/pages/commanders"
SLEEP_BETWEEN_REQUESTS = 1.0  # courtesy delay
CACHE_TTL_DAYS = int(os.getenv("EDHREC_CACHE_TTL_DAYS", "7"))

# Map EDHREC cardlist header → slot tag nội bộ
HEADER_TO_SLOT = {
    "High Synergy Cards": "synergy",
    "Top Cards": "top",
    "Creatures": "creature",
    "Instants": "instant",
    "Sorceries": "sorcery",
    "Enchantments": "enchantment",
    "Artifacts": "artifact",
    "Planeswalkers": "planeswalker",
    "Lands": "land",
    "Mana Artifacts": "ramp",
    "Card Draw": "draw",
    "Removal": "removal",
    "Board Wipes": "wipe",
    "Tutors": "tutor",
}


def get_commander_cards(commander_slug: str) -> list[dict]:
    """
    Lấy cards cho commander từ EDHREC, ưu tiên cache.

    Args:
        commander_slug: ví dụ "atraxa-praetors-voice"

    Returns:
        list of dicts: [{card_name, synergy, inclusion, num_decks,
                         potential_decks, slot_tag}, ...]
    """
    cached = cache.get_edhrec_cards(commander_slug, max_age_days=CACHE_TTL_DAYS)
    if cached:
        return [dict(r) for r in cached]

    data = _fetch_edhrec(commander_slug)
    if not data:
        return []

    cards = _parse_edhrec_response(data)
    cache.upsert_edhrec_cards(commander_slug, cards)
    return cards


def _fetch_edhrec(slug: str) -> dict | None:
    url = f"{EDHREC_BASE}/{slug}.json"
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "EDH-Deck-Builder/1.0 (personal use)"},
            timeout=15,
        )
        if resp.status_code == 404:
            print(f"  [!] Commander không tìm thấy trên EDHREC: {slug}")
            return None
        resp.raise_for_status()
        time.sleep(SLEEP_BETWEEN_REQUESTS)
        return resp.json()
    except requests.RequestException as e:
        print(f"  [!] EDHREC fetch thất bại cho {slug}: {e}")
        return None


def _parse_edhrec_response(data: dict) -> list[dict]:
    """Extract cards từ EDHREC JSON response."""
    cards_by_name: dict[str, dict] = {}

    try:
        cardlists = (
            data["container"]["json_dict"]["cardlists"]
        )
    except (KeyError, TypeError):
        return []

    for cardlist in cardlists:
        header = cardlist.get("header", "")
        slot_tag = HEADER_TO_SLOT.get(header, "other")

        for cv in cardlist.get("cardviews", []):
            name = cv.get("name", "").strip()
            if not name:
                continue

            # Ưu tiên entry có synergy score cao nhất nếu card xuất hiện nhiều list
            existing = cards_by_name.get(name)
            synergy = cv.get("synergy", 0.0) or 0.0

            if existing is None or synergy > existing["synergy"]:
                cards_by_name[name] = {
                    "card_name": name,
                    "synergy": synergy,
                    "inclusion": cv.get("inclusion", 0) or 0,
                    "num_decks": cv.get("num_decks", 0) or 0,
                    "potential_decks": cv.get("potential_decks", 0) or 0,
                    "slot_tag": slot_tag if existing is None else existing["slot_tag"],
                }

    return list(cards_by_name.values())


def get_num_decks_for_commander(commander_slug: str) -> int:
    """Trả về số deck đã build với commander này trên EDHREC."""
    rows = cache.get_edhrec_cards(commander_slug, max_age_days=CACHE_TTL_DAYS)
    if not rows:
        return 0
    return rows[0]["potential_decks"] if rows else 0
