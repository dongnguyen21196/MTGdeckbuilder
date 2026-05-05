"""
engine/deck_builder.py — Greedy slot-based EDH deck builder.

Thuật toán:
  1. Lấy EDHREC cards cho commander (sorted by synergy)
  2. Filter: banned list + color identity
  3. Phân loại mỗi card vào slot (land, ramp, draw, removal, wipe, tutor, synergy)
  4. Fill từng slot từ card có synergy cao nhất trong collection trước,
     sau đó nếu chưa đủ target thì dùng card chưa có (for buylist)
  5. Trả về deck 99 cards + metadata

Slot targets (từ data/slots.json):
  land:    37    ramp:    10    draw:    10
  removal: 8     wipe:    3     tutor:   3
  synergy: 28    (tổng = 99)
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from enrichers import scryfall, edhrec
from filters import banned_list as bl
from db import cache

SLOTS_FILE = Path(__file__).parent.parent / "data" / "slots.json"


@dataclass
class DeckCard:
    name: str
    slot: str
    synergy: float
    is_owned: bool
    cmc: float = 0.0
    type_line: str = ""
    price_usd: float | None = None


@dataclass
class BuiltDeck:
    commander_name: str
    commander_slug: str
    cards: list[DeckCard] = field(default_factory=list)
    missing_cards: list[DeckCard] = field(default_factory=list)
    synergy_score: float = 0.0       # avg synergy của 99 cards
    collection_coverage: float = 0.0  # % owned
    slot_balance_score: float = 0.0
    composite_score: float = 0.0
    total_price_missing: float = 0.0


def build_deck(commander_name: str, commander_slug: str) -> BuiltDeck:
    """
    Build deck tốt nhất cho commander từ collection + ghi nhận card thiếu.
    """
    collection_names = cache.get_collection_names()
    banned_set = cache.get_banned_list()

    # 1. Lấy commander data từ Scryfall
    cmd_data_rows = scryfall.enrich_cards([commander_name])
    cmd_data = cmd_data_rows.get(commander_name, {})
    commander_colors = json.loads(cmd_data.get("color_identity", "[]"))

    # 2. Lấy EDHREC cards
    edhrec_cards = edhrec.get_commander_cards(commander_slug)
    if not edhrec_cards:
        print(f"  [!] Không có EDHREC data cho {commander_name}")
        return BuiltDeck(commander_name=commander_name, commander_slug=commander_slug)

    # 3. Enrich với Scryfall data
    all_card_names = [c["card_name"] for c in edhrec_cards]
    scryfall_data = scryfall.enrich_cards(all_card_names)

    # 4. Filter banned + color identity
    legal_names, banned = bl.filter_banned(all_card_names)
    legal_names, off_color = bl.filter_color_identity(
        legal_names, commander_colors, scryfall_data
    )

    if banned:
        print(f"  Loại {len(banned)} banned cards")
    if off_color:
        print(f"  Loại {len(off_color)} off-color cards")

    # 5. Build lookup: name → edhrec card
    edhrec_lookup = {c["card_name"]: c for c in edhrec_cards}

    # 6. Classify cards vào slots
    slots_config = _load_slots_config()
    slot_pools: dict[str, list[dict]] = {slot: [] for slot in slots_config}

    for name in legal_names:
        card_row = scryfall_data.get(name, {})
        edhrec_row = edhrec_lookup.get(name, {})
        slot = _classify_card(name, card_row, edhrec_row, slots_config)
        if slot:
            slots_config_slot = slots_config.get(slot, slots_config.get("synergy"))
            slots_config[slot]  # ensure slot exists
            slot_pools.setdefault(slot, []).append({
                "name": name,
                "slot": slot,
                "synergy": edhrec_row.get("synergy", 0.0),
                "is_owned": name in collection_names,
                "cmc": card_row.get("cmc", 0.0) if card_row else 0.0,
                "type_line": card_row.get("type_line", "") if card_row else "",
                "price_usd": _get_price(card_row),
            })

    # Sort each pool: owned first, then by synergy desc
    for slot in slot_pools:
        slot_pools[slot].sort(
            key=lambda c: (not c["is_owned"], -c["synergy"])
        )

    # 7. Greedy fill theo slot targets
    slot_targets = {s: d["target"] for s, d in _load_slots_raw().items()}
    selected: list[dict] = []
    missing: list[dict] = []
    used_names: set[str] = set()

    for slot, target in slot_targets.items():
        pool = slot_pools.get(slot, [])
        count = 0
        for card in pool:
            if count >= target:
                break
            if card["name"] in used_names:
                continue
            if bl.is_basic_land(card["name"]):
                # Basic lands: add thẳng, không check used_names
                selected.append(card)
                count += 1
                continue
            used_names.add(card["name"])
            selected.append(card)
            if not card["is_owned"]:
                missing.append(card)
            count += 1

    # 8. Fill thiếu vào slot synergy nếu < 99
    remaining_slots = 99 - len(selected)
    if remaining_slots > 0:
        synergy_pool = slot_pools.get("synergy", [])
        for card in synergy_pool:
            if remaining_slots <= 0:
                break
            if card["name"] in used_names:
                continue
            used_names.add(card["name"])
            selected.append(card)
            if not card["is_owned"]:
                missing.append(card)
            remaining_slots -= 1

    # 9. Score deck
    owned_count = sum(1 for c in selected if c["is_owned"])
    avg_synergy = sum(c["synergy"] for c in selected) / max(len(selected), 1)
    coverage = owned_count / max(len(selected), 1)
    slot_balance = _score_slot_balance(selected, slot_targets)
    total_missing_price = sum(
        c["price_usd"] for c in missing if c["price_usd"] is not None
    )

    composite = (
        0.50 * min(avg_synergy * 5, 1.0)
        + 0.30 * coverage
        + 0.20 * slot_balance
    )

    deck_cards = [DeckCard(**{k: c[k] for k in DeckCard.__dataclass_fields__}) for c in selected]
    missing_cards = [DeckCard(**{k: c[k] for k in DeckCard.__dataclass_fields__}) for c in missing]

    return BuiltDeck(
        commander_name=commander_name,
        commander_slug=commander_slug,
        cards=deck_cards,
        missing_cards=missing_cards,
        synergy_score=avg_synergy,
        collection_coverage=coverage,
        slot_balance_score=slot_balance,
        composite_score=composite,
        total_price_missing=total_missing_price,
    )


def _classify_card(name: str, card_row: dict, edhrec_row: dict, slots_config: dict) -> str:
    """Phân loại card vào slot dựa trên EDHREC tag hoặc oracle text."""
    if bl.is_basic_land(name):
        return "land"

    type_line = card_row.get("type_line", "").lower() if card_row else ""
    oracle = card_row.get("oracle_text", "").lower() if card_row else ""
    edhrec_slot = edhrec_row.get("slot_tag", "") or ""

    # Từ EDHREC tag
    slot_map = {
        "ramp": "ramp", "draw": "draw", "removal": "removal",
        "wipe": "wipe", "tutor": "tutor", "land": "land",
        "synergy": "synergy", "top": "synergy",
    }
    if edhrec_slot in slot_map:
        return slot_map[edhrec_slot]

    # Từ type line
    if "land" in type_line:
        return "land"

    # Từ oracle text heuristics
    slots_raw = _load_slots_raw_full()
    for kw in slots_raw.get("ramp_keywords", []):
        if kw.lower() in oracle:
            return "ramp"
    for kw in slots_raw.get("draw_keywords", []):
        if kw.lower() in oracle:
            return "draw"
    for kw in slots_raw.get("wipe_keywords", []):
        if kw.lower() in oracle:
            return "wipe"
    for kw in slots_raw.get("removal_keywords", []):
        if kw.lower() in oracle:
            return "removal"
    for kw in slots_raw.get("tutor_keywords", []):
        if kw.lower() in oracle:
            return "tutor"

    return "synergy"


def _score_slot_balance(selected: list[dict], targets: dict[str, int]) -> float:
    """Score 0-1 dựa trên mức độ fill đúng target từng slot."""
    counts = {}
    for c in selected:
        counts[c["slot"]] = counts.get(c["slot"], 0) + 1

    scores = []
    for slot, target in targets.items():
        actual = counts.get(slot, 0)
        deviation = abs(actual - target) / max(target, 1)
        scores.append(max(0.0, 1.0 - deviation))

    return sum(scores) / len(scores) if scores else 0.0


def _get_price(card_row: dict | None) -> float | None:
    if not card_row:
        return None
    prices_raw = card_row.get("prices", "{}")
    if isinstance(prices_raw, str):
        prices = json.loads(prices_raw)
    else:
        prices = prices_raw or {}
    usd = prices.get("usd")
    return float(usd) if usd else None


_slots_cache = None
_slots_raw_cache = None


def _load_slots_config() -> dict:
    global _slots_cache
    if _slots_cache is None:
        with open(SLOTS_FILE) as f:
            data = json.load(f)
        _slots_cache = {slot: [] for slot in data["slots"]}
    return {k: list(v) for k, v in _slots_cache.items()}


def _load_slots_raw() -> dict:
    with open(SLOTS_FILE) as f:
        data = json.load(f)
    return {slot: cfg for slot, cfg in data["slots"].items()}


def _load_slots_raw_full() -> dict:
    with open(SLOTS_FILE) as f:
        return json.load(f)
