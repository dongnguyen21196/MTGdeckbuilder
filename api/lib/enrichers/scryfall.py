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
import os
import re
import time
import requests
from db import cache

SCRYFALL_COLLECTION_URL  = "https://api.scryfall.com/cards/collection"
SCRYFALL_BULK_SEARCH_URL = "https://api.scryfall.com/cards/search"
BATCH_SIZE               = 75
SLEEP_BETWEEN_BATCHES    = 0.1

# Scryfall trả 400 cho request không có User-Agent định danh — kể cả UA mặc
# định của thư viện requests. Xem https://scryfall.com/docs/api
# SCRYFALL_CONTACT nên đặt thành email liên hệ để Scryfall biết ai đang gọi
# nếu cần báo vấn đề.
HEADERS = {
    "User-Agent": "MTGDeckBuilder/1.0 ({})".format(
        os.getenv("SCRYFALL_CONTACT", "https://github.com/dongnguyen21196/MTGdeckbuilder")
    ),
    "Accept": "application/json",
}

MAX_RETRIES  = 5
RETRY_STATUS = {429, 500, 502, 503, 504}


def _request(method: str, url: str, **kwargs) -> requests.Response:
    """Gọi Scryfall với backoff khi bị rate-limit.

    Seed đầy đủ bắn vài trăm request; runner GitHub lại dùng IP dùng chung nên
    dính 429 là chuyện bình thường, không phải lỗi lập trình. Không có backoff
    thì cả job chết giữa chừng vì một lần 429 duy nhất."""
    delay = 1.0
    resp = None
    for attempt in range(1, MAX_RETRIES + 1):
        resp = requests.request(method, url, headers=HEADERS, timeout=30, **kwargs)
        if resp.status_code not in RETRY_STATUS:
            resp.raise_for_status()
            return resp
        wait = float(resp.headers.get("Retry-After") or delay)
        print(f"  [!] Scryfall {resp.status_code}, chờ {wait:.0f}s "
              f"(lần {attempt}/{MAX_RETRIES})", flush=True)
        time.sleep(wait)
        delay = min(delay * 2, 30.0)
    resp.raise_for_status()
    return resp


def enrich_cards(card_names: list[str]) -> dict[str, dict]:
    """
    Lấy Scryfall data cho danh sách card, ưu tiên cache.

    FIX 2: Hai pass riêng biệt:
      - Pass 1: oracle data (type, CI, oracle text...) — cache 30 ngày
      - Pass 2: price refresh — cache 7 ngày, chạy độc lập với pass 1

    Returns:
        dict: {card_name: merged_data_with_price}
    """
    # Mọi truy cập cache ở đây đều theo lô. Bản SQLite query từng card trong
    # ba vòng lặp riêng — với file local thì không sao, nhưng qua mạng tới
    # Postgres thì một lần build deck 700 card thành ~2.800 round trip.
    # Kết quả không đổi, chỉ đổi số lượt đi lại.

    # Pass 1: Oracle data
    missing_oracle = cache.get_missing_scryfall_cards(card_names)
    if missing_oracle:
        print(f"  Fetching oracle data: {len(missing_oracle)} cards từ Scryfall...")
        fetched = _batch_fetch_oracle(missing_oracle)
        new_prices = []
        for data in fetched.values():
            # Lưu price từ cùng response luôn (tận dụng API call)
            oracle_name = data.get("oracle_name") or data["name"]
            prices = data.pop("_prices", {})
            new_prices.append({
                "oracle_name": oracle_name,
                "usd":         prices.get("usd"),
                "usd_foil":    prices.get("usd_foil"),
                "eur":         prices.get("eur"),
            })
        cache.upsert_scryfall_cards(list(fetched.values()))
        cache.upsert_prices(new_prices)
        # Sau khi có oracle_name mới, cập nhật collection oracle mapping
        cache.refresh_collection_oracle_names()

    rows = cache.get_scryfall_cards(card_names)
    oracle_of = {name: (row["oracle_name"] or name) for name, row in rows.items()}

    # Pass 2: Price refresh cho cards đã có oracle nhưng giá stale
    stale_prices = set(cache.get_stale_price_cards(list(set(oracle_of.values()))))
    if stale_prices:
        stale_printing_names = [
            name for name in card_names if oracle_of.get(name) in stale_prices
        ]
        if stale_printing_names:
            print(f"  Refreshing prices: {len(stale_printing_names)} cards...")
            price_data = _batch_fetch_prices_only(stale_printing_names)
            cache.upsert_prices([
                {"oracle_name": oracle_name, **prices}
                for oracle_name, prices in price_data.items()
            ])

    # Assemble kết quả từ cache
    price_of = cache.get_prices_usd(list(set(oracle_of.values())))
    result = {}
    for name in card_names:
        row = rows.get(name)
        if row:
            d = dict(row)
            d["price_usd"] = price_of.get(oracle_of[name])
            result[name] = d
    return result


def _batch_fetch_oracle(names: list[str]) -> dict[str, dict]:
    """Batch fetch oracle data qua /cards/collection (75 cards/request)."""
    result = {}
    for i in range(0, len(names), BATCH_SIZE):
        batch = names[i:i + BATCH_SIZE]
        resp = _request(
            "POST",
            SCRYFALL_COLLECTION_URL,
            json={"identifiers": [{"name": n} for n in batch]},
        )
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
        resp = _request(
            "POST",
            SCRYFALL_COLLECTION_URL,
            json={"identifiers": [{"name": n} for n in batch]},
        )
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
    FIX 3: oracle_text cho multi-face cards (split/adventure/modal_dfc):
      Scryfall KHÔNG có top-level oracle_text cho các card này.
      Thay vào đó, oracle_text nằm trong card_faces[].
      Ghép cả hai mặt để archetype detection và synergy chain
      có đủ text để phân tích.
    FIX 4: Showcase/Promo cards (Godzilla series, Buy-a-Box...):
      Scryfall dùng oracle_id để link promo name → real card.
      Dùng oracle_id để lookup tên thật nếu card là promo.
      oracle_name sẽ là front-face name của oracle card.
    """
    prices = card.get("prices", {})
    legalities = card.get("legalities", {})
    oracle_name = _extract_oracle_name(card)
    layout = card.get("layout", "")
    faces = card.get("card_faces", [])

    # FIX 3: Ghép oracle_text từ tất cả card faces
    # Ưu tiên top-level oracle_text nếu có (normal cards).
    # Với split/adventure/modal_dfc: top-level oracle_text = None.
    top_oracle = card.get("oracle_text")
    if top_oracle:
        oracle_text = top_oracle
    elif faces:
        # Ghép oracle text các mặt, ngăn cách bằng "\n---\n"
        # để archetype/chain detection biết đây là 2 phần khác nhau
        face_texts = [
            f.get("oracle_text", "") for f in faces
            if f.get("oracle_text")
        ]
        oracle_text = "\n---\n".join(face_texts)
    else:
        oracle_text = ""

    # Mana cost: top-level hoặc từ front face (split cards)
    mana_cost = card.get("mana_cost")
    if not mana_cost and faces:
        mana_cost = faces[0].get("mana_cost", "")

    # Type line: ghép tất cả faces nếu top-level trống
    type_line = card.get("type_line", "")
    if not type_line and faces:
        type_line = " // ".join(f.get("type_line", "") for f in faces if f.get("type_line"))

    return {
        "name":           card.get("name", ""),
        "oracle_name":    oracle_name,
        "oracle_id":      card.get("oracle_id", ""),
        "mana_cost":      mana_cost or "",
        "cmc":            card.get("cmc", 0.0),
        "type_line":      type_line,
        "oracle_text":    oracle_text,
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

    Rules theo layout:
      normal/flip:     oracle_name = card["name"]
      transform/modal_dfc/meld/reversible:
                       oracle_name = front face name only
                       "Birgi, God of Storytelling // Harnfel" → "Birgi, God of Storytelling"
      split (Fire//Ice, Dusk//Dawn, Wear//Tear):
                       oracle_name = full combined name (là 1 physical card)
                       "Fire // Ice" → "Fire // Ice"
      adventure (Murderous Rider // Swift End):
                       oracle_name = creature face name only
                       Lý do: card được nhận diện bởi creature name trong gameplay,
                       adventure spell là optional ability — không phải separate card.
                       "Murderous Rider // Swift End" → "Murderous Rider"

    FIX 4 — Showcase/Promo cards:
      Cards như "Godzilla, King of the Monsters" là promo print của
      "Zilortha, Strength Incarnate". Scryfall response có field
      "flavor_name" chứa promo name — tên thật nằm trong "name".
      Với promo cards: oracle_name = card["name"] (tên thật từ Scryfall)
      Không cần xử lý đặc biệt vì Scryfall /cards/named?fuzzy= tự resolve
      promo name → real card và trả về real name trong "name" field.
    """
    layout = card.get("layout", "")
    name = card.get("name", "")
    faces = card.get("card_faces", [])

    # Transform/modal_dfc/meld: lấy front face
    if layout in ("transform", "modal_dfc", "meld", "reversible_card"):
        if faces:
            return faces[0].get("name", name)
        # Fallback: nếu không có faces, lấy phần trước //
        if " // " in name:
            return name.split(" // ")[0].strip()

    # Adventure: lấy creature face (face[0]) — không phải full name
    if layout == "adventure":
        if faces:
            return faces[0].get("name", name)
        if " // " in name:
            return name.split(" // ")[0].strip()

    # Split, aftermath, fuse: giữ full name (là 1 physical card duy nhất)
    # "Fire // Ice", "Dusk // Dawn", "Wear // Tear" → giữ nguyên
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
        resp = _request("GET", url, params=params)
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
        resp = _request("GET", url, params=params)
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


# DFC/adventure layouts: slug dùng front face
# Split/aftermath/fuse layouts: slug giữ cả hai phần (fire-ice, dusk-dawn)
# Nhận diện qua heuristic: split cards thường có 2 tên đều ngắn (1-2 từ)
# DFC/modal_dfc thường có tên dài, chứa dấu phẩy hoặc danh hiệu
_KNOWN_SPLIT_CARDS: frozenset = frozenset([
    # Thêm vào khi cần — heuristic xử lý được phần lớn
])


def _to_slug(name: str, layout: str = "") -> str:
    """
    Chuyển card name thành EDHREC slug format.

    Rules theo layout:
      transform/modal_dfc/adventure (DFC): front face only
        "Murderous Rider // Swift End" → "murderous-rider"
        "Birgi, God of Storytelling // Harnfel" → "birgi-god-of-storytelling"

      split/aftermath/fuse: giữ cả hai phần với dấu gạch ngang
        "Fire // Ice" → "fire-ice"
        "Dusk // Dawn" → "dusk-dawn"
        "Wear // Tear" → "wear-tear"

    Heuristic khi không có layout info:
      Nếu cả hai nửa đều ngắn (≤ 3 từ), coi là split → giữ cả hai.
      Nếu một nửa dài hoặc có dấu phẩy, coi là DFC → front face only.
    """
    if " // " in name:
        parts = name.split(" // ")
        front, back = parts[0].strip(), parts[1].strip()

        # Layout rõ ràng
        if layout in ("transform", "modal_dfc", "meld", "adventure", "flip"):
            name = front
        elif layout in ("split", "aftermath"):
            # Giữ cả hai: "Fire // Ice" → "fire-ice"
            slug_front = re.sub(r"[',\.!?:;]", "", front.lower())
            slug_front = re.sub(r"[^a-z0-9]+", "-", slug_front).strip("-")
            slug_back  = re.sub(r"[',\.!?:;]", "", back.lower())
            slug_back  = re.sub(r"[^a-z0-9]+", "-", slug_back).strip("-")
            return f"{slug_front}-{slug_back}"
        else:
            # Heuristic: đếm từ trong mỗi nửa
            # Split: cả hai ngắn (Fire, Ice, Wear, Tear, Dusk, Dawn)
            # DFC: ít nhất một nửa dài hoặc có dấu phẩy
            front_words = len(front.split())
            back_words  = len(back.split())
            has_comma   = "," in front or "," in back
            if front_words == 1 and back_words == 1:
                # Split card: giữ cả hai
                slug_f = re.sub(r"[',\.!?:;]", "", front.lower())
                slug_f = re.sub(r"[^a-z0-9]+", "-", slug_f).strip("-")
                slug_b = re.sub(r"[',\.!?:;]", "", back.lower())
                slug_b = re.sub(r"[^a-z0-9]+", "-", slug_b).strip("-")
                return f"{slug_f}-{slug_b}"
            else:
                # DFC/adventure: front face only
                name = front

    slug = name.lower()
    slug = re.sub(r"[',\.!?:;]", "", slug)
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug
