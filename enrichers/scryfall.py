"""
enrichers/scryfall.py — Lấy card data từ Scryfall API.

FIX 1 — Reprint dedup:
  _normalize_card() giờ extract thêm oracle_name từ Scryfall response.
  oracle_name = card["name"] cho single-face cards.
  oracle_name = card["card_faces"][0]["name"] cho double-faced cards.
  Dùng để normalize ownership check cross-reprint.

FIX 2 — Price TTL tách riêng:
  enrich_cards() tách thành 2 pass:
    Pass 1: fetch oracle data (CMC, type, CI...) nếu missing/stale (30 ngày)
    Pass 2: refresh prices nếu stale (7 ngày) dù oracle data còn fresh
  Cho phép buylist luôn hiển thị giá gần đúng nhất mà không force
  re-fetch toàn bộ oracle data mỗi tuần.
"""

import json
import re
import time
import requests
from db import cache

SCRYFALL_COLLECTION_URL  = "https://api.scryfall.com/cards/collection"
SCRYFALL_BULK_SEARCH_URL = "https://api.scryfall.com/cards/search"
BATCH_SIZE               = 75
SLEEP_BETWEEN_BATCHES    = 0.1


def enrich_cards(card_names: list[str]) -> dict[str, dict]:
    """
    Lấy Scryfall data cho danh sách card, ưu tiên cache.

    FIX 2: Hai pass riêng biệt:
      - Pass 1: oracle data (type, CI, oracle text...) — cache 30 ngày
      - Pass 2: price refresh — cache 7 ngày, chạy độc lập với pass 1

    Returns:
        dict: {card_name: merged_data_with_price}
    """
    # Pass 1: Oracle data
    missing_oracle = cache.get_missing_scryfall_cards(card_names)
    if missing_oracle:
        print(f"  Fetching oracle data: {len(missing_oracle)} cards từ Scryfall...")
        fetched = _batch_fetch_oracle(missing_oracle)
        for data in fetched.values():
            cache.upsert_scryfall_card(data)
            # Lưu price từ cùng response luôn (tận dụng API call)
            oracle_name = data.get("oracle_name") or data["name"]
            prices = data.pop("_prices", {})
            cache.upsert_price(oracle_name, prices.get("usd"), prices.get("usd_foil"), prices.get("eur"))
        # Sau khi có oracle_name mới, cập nhật collection oracle mapping
        cache.refresh_collection_oracle_names()

    # Pass 2: Price refresh cho cards đã có oracle nhưng giá stale
    all_oracle_names = []
    for name in card_names:
        row = cache.get_scryfall_card(name)
        if row:
            all_oracle_names.append(row["oracle_name"] or name)

    stale_prices = cache.get_stale_price_cards(list(set(all_oracle_names)))
    if stale_prices:
        # Lấy printing names tương ứng để fetch
        stale_printing_names = []
        for name in card_names:
            row = cache.get_scryfall_card(name)
            if row and (row["oracle_name"] or name) in stale_prices:
                stale_printing_names.append(name)

        if stale_printing_names:
            print(f"  Refreshing prices: {len(stale_printing_names)} cards...")
            price_data = _batch_fetch_prices_only(stale_printing_names)
            for oracle_name, prices in price_data.items():
                cache.upsert_price(oracle_name, prices.get("usd"), prices.get("usd_foil"), prices.get("eur"))

    # Assemble kết quả từ cache
    result = {}
    for name in card_names:
        row = cache.get_scryfall_card(name)
        if row:
            d = dict(row)
            oracle_name = d.get("oracle_name") or name
            d["price_usd"] = cache.get_price_usd(oracle_name)
            result[name] = d
    return result


def _batch_fetch_oracle(names: list[str]) -> dict[str, dict]:
    """Batch fetch oracle data qua /cards/collection (75 cards/request)."""
    result = {}
    for i in range(0, len(names), BATCH_SIZE):
        batch = names[i:i + BATCH_SIZE]
        resp = requests.post(
            SCRYFALL_COLLECTION_URL,
            json={"identifiers": [{"name": n} for n in batch]},
            headers={"Accept": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        for card in data.get("data", []):
            name = card.get("name", "")
            if name:
                result[name] = _normalize_card(card)
        not_found = data.get("not_found", [])
        if not_found:
            print(f"  [!] Không tìm thấy trên Scryfall: {[n['name'] for n in not_found]}")
        time.sleep(SLEEP_BETWEEN_BATCHES)
    return result


def _batch_fetch_prices_only(names: list[str]) -> dict[str, dict]:
    """
    Fetch chỉ price data, trả về {oracle_name: {usd, usd_foil, eur}}.
    FIX 2: Dùng cùng endpoint nhưng chỉ extract prices, không update oracle cache.
    """
    result = {}
    for i in range(0, len(names), BATCH_SIZE):
        batch = names[i:i + BATCH_SIZE]
        resp = requests.post(
            SCRYFALL_COLLECTION_URL,
            json={"identifiers": [{"name": n} for n in batch]},
            headers={"Accept": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        for card in data.get("data", []):
            prices = card.get("prices", {})
            oracle_name = _extract_oracle_name(card)
            result[oracle_name] = {
                "usd":      prices.get("usd"),
                "usd_foil": prices.get("usd_foil"),
                "eur":      prices.get("eur"),
            }
        time.sleep(SLEEP_BETWEEN_BATCHES)
    return result


def _normalize_card(card: dict) -> dict:
    """
    Chuẩn hóa Scryfall response thành schema nội bộ.

    FIX 1: Extract oracle_name riêng biệt với printing name.
    FIX 2: Prices lưu tạm trong _prices key, caller tách ra upsert riêng.
    """
    prices = card.get("prices", {})
    legalities = card.get("legalities", {})
    oracle_name = _extract_oracle_name(card)

    return {
        "name":           card.get("name", ""),
        "oracle_name":    oracle_name,
        "oracle_id":      card.get("oracle_id", ""),
        "mana_cost":      card.get("mana_cost", ""),
        "cmc":            card.get("cmc", 0.0),
        "type_line":      card.get("type_line", ""),
        "oracle_text":    card.get("oracle_text", ""),
        "color_identity": json.dumps(card.get("color_identity", [])),
        "keywords":       json.dumps(card.get("keywords", [])),
        "legalities":     json.dumps(legalities),
        "scryfall_id":    card.get("id", ""),
        # Price tách riêng — caller sẽ pop và upsert vào scryfall_prices
        "_prices": {
            "usd":      prices.get("usd"),
            "usd_foil": prices.get("usd_foil"),
            "eur":      prices.get("eur"),
        },
    }


def _extract_oracle_name(card: dict) -> str:
    """
    Lấy oracle_name (tên canonical) từ Scryfall card object.

    FIX 1 — Reprint dedup rules:
      - Single-face card: oracle_name = card["name"]
      - Double-faced card (layout: transform/modal_dfc/...):
          oracle_name = front face name chỉ
          Ví dụ: "Delina, Wild Mage // Draconic Destiny" → "Delina, Wild Mage"
      - Split card (layout: split/adventure):
          oracle_name = full name giữ cả hai mặt vì là cùng physical card
          Ví dụ: "Fire // Ice" → "Fire // Ice"

    Dùng oracle_id để group reprints về cùng oracle_name.
    """
    layout = card.get("layout", "")
    name = card.get("name", "")

    # Double-faced: chỉ lấy front face
    if layout in ("transform", "modal_dfc", "meld", "reversible_card"):
        faces = card.get("card_faces", [])
        if faces:
            return faces[0].get("name", name)

    # Split/adventure: giữ full name (Fire // Ice là 1 card)
    return name


def fetch_all_commanders() -> list[dict]:
    """
    Lấy tất cả commander hợp lệ từ Scryfall.
    Detect partner ability từ keywords và oracle text.
    """
    print("Fetching danh sách commanders từ Scryfall...")
    commanders = []
    url = SCRYFALL_BULK_SEARCH_URL
    params = {"q": "is:commander format:commander", "order": "name", "unique": "cards"}

    while url:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        for card in data.get("data", []):
            keywords = card.get("keywords", [])
            oracle = card.get("oracle_text", "")
            is_partner = (
                "Partner" in keywords
                or "Partner with" in keywords
                or "partner" in oracle.lower()
            )
            commanders.append({
                "name":           card["name"],
                "slug":           _to_slug(card["name"]),
                "color_identity": json.dumps(card.get("color_identity", [])),
                "is_partner":     1 if is_partner else 0,
                "partner_name":   None,
            })

        url = data.get("next_page")
        params = {}
        time.sleep(SLEEP_BETWEEN_BATCHES)

    partner_count = sum(1 for c in commanders if c["is_partner"])
    print(f"  Tìm thấy {len(commanders)} commanders ({partner_count} có partner ability).")
    cache.upsert_commanders(commanders)
    return commanders


def make_partner_slug(name1: str, name2: str) -> str:
    """
    Tạo EDHREC slug cho partner pair (sorted alphabetically).
    Ví dụ: Thrasios + Tymna → thrasios-triton-hero-tymna-the-weaver
    """
    pair = sorted([_to_slug(name1), _to_slug(name2)])
    return f"{pair[0]}-{pair[1]}"


def fetch_banned_list() -> list[str]:
    """Lấy banned list EDH từ Scryfall."""
    print("Fetching banned list từ Scryfall...")
    banned = []
    url = SCRYFALL_BULK_SEARCH_URL
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
    legalities = card_data.get("legalities", "{}")
    if isinstance(legalities, str):
        legalities = json.loads(legalities)
    return legalities.get("commander", "not_legal") == "legal"


def _to_slug(name: str) -> str:
    """
    Chuyển card name thành EDHREC slug format.
    Handle double-faced, smart quotes, và ký tự đặc biệt MTG.
    """
    if " // " in name:
        name = name.split(" // ")[0].strip()
    slug = name.lower()
    slug = re.sub(r"[',\\.!?:;]", "", slug)
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug
