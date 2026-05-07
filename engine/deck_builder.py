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
from engine.mana_pip import (
    analyze_pips, calculate_basic_land_distribution,
    build_basic_land_list, format_pip_report,
)
from engine.dynamic_scoring import DynamicScorer
from engine.slot_config import get_slot_targets, describe_adjustments

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
    pip_analysis: object = None      # PipAnalysis — pip distribution của deck
    basic_distribution: dict = field(default_factory=dict)  # {color: count} basic lands
    curve_summary: dict = field(default_factory=dict)        # CMC distribution sau build
    top_chain_buffs: list = field(default_factory=list)      # top chain-buffed cards


def build_deck(
    commander_name: str,
    commander_slug: str,
    partner_name: str | None = None,
    archetype: str = "generic",
) -> BuiltDeck:
    """
    Build deck tốt nhất cho commander từ collection + ghi nhận card thiếu.

    FIX Bug 4 — Partner commanders:
      Nếu partner_name được cung cấp:
      - Color identity = union của cả hai commanders
      - EDHREC slug dùng make_partner_slug(name1, name2)
      - Commander line trong decklist sẽ có 2 dòng *CMDR*
    """
    collection_names = cache.get_collection_names()
    banned_set = cache.get_banned_list()

    # 1. Lấy commander data từ Scryfall
    names_to_fetch = [commander_name]
    if partner_name:
        names_to_fetch.append(partner_name)

    cmd_data_rows = scryfall.enrich_cards(names_to_fetch)
    cmd_data = cmd_data_rows.get(commander_name, {})
    commander_colors = json.loads(cmd_data.get("color_identity", "[]"))

    # Merge color identity nếu có partner
    if partner_name:
        partner_data = cmd_data_rows.get(partner_name, {})
        partner_colors = json.loads(partner_data.get("color_identity", "[]"))
        commander_colors = list(set(commander_colors) | set(partner_colors))
        # Dùng partner slug cho EDHREC
        commander_slug = scryfall.make_partner_slug(commander_name, partner_name)

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
            raw_synergy = edhrec_row.get("synergy", 0.0)
            # FIX: non-basic lands dùng utility score để bù staple lands
            # có EDHREC synergy âm/thấp (quá phổ biến = không "đặc biệt" với EDHREC)
            if slot == "land" and not bl.is_basic_land(name):
                effective_synergy = _land_utility_score(name, raw_synergy)
            else:
                effective_synergy = raw_synergy
            slot_pools.setdefault(slot, []).append({
                "name": name,
                "slot": slot,
                "synergy": effective_synergy,
                "is_owned": name in collection_names,
                "cmc": card_row.get("cmc", 0.0) if card_row else 0.0,
                "type_line": card_row.get("type_line", "") if card_row else "",
                "price_usd": _get_price(card_row),
            })

    # Khởi tạo DynamicScorer cho curve penalty + chain buff
    oracle_texts_for_dynamic = {
        name: (scryfall_data.get(name) or {}).get("oracle_text", "") or ""
        for name in legal_names
    }
    dynamic_scorer = DynamicScorer(archetype=archetype)
    dynamic_scorer.set_oracle_index(oracle_texts_for_dynamic)

    # Sort each pool: owned first, then by dynamic adjusted synergy desc
    # Note: dynamic_score sẽ thay đổi theo thời gian khi deck được build,
    # nên sort ban đầu chỉ dùng base synergy — re-evaluate khi pick
    for slot in slot_pools:
        slot_pools[slot].sort(
            key=lambda c: (not c["is_owned"], -c["synergy"])
        )

    # 7. Tier-based Pool Picking
    #
    # Kiến trúc mới thay thế slot-by-slot greedy fill:
    #
    # Thay vì "fill ramp trước, draw sau", gom tất cả non-land cards vào
    # một pool duy nhất. Mỗi lượt pick, chọn card có pick_score cao nhất.
    #
    # pick_score = dynamic_score × owned_bonus + slot_hunger
    #
    # slot_hunger: bonus thêm cho card của slot còn thiếu so với target.
    #   Slot càng "đói" (thiếu nhiều so với target) → hunger càng cao
    #   → card của slot đó được ưu tiên hơn. Khi slot đủ target,
    #   hunger = 0 nhưng card vẫn có thể được chọn nếu score đủ cao
    #   (cạnh tranh với synergy pool — tức là soft fallback tự nhiên).
    #
    # Lợi thế so với slot-by-slot:
    #   - Không bao giờ bỏ card mạnh vì "slot đầy"
    #   - Card yếu không được ưu tiên chỉ vì slot của nó chưa đủ
    #   - Curve penalty và chain buff được apply đồng nhất cho tất cả cards
    #   - Soft fallback là hệ quả tự nhiên, không cần code riêng
    #
    # Land vẫn xử lý riêng (non-basic EDHREC pool + basic pip-weighted fill)
    # vì land có logic đặc biệt không phù hợp với pool scoring chung.

    # V2 + V7: Dynamic slot targets theo archetype + commander CMC
    commander_cmc = cmd_data.get("cmc", 3.0) or 3.0
    partner_cmc   = 0.0
    if partner_name:
        partner_data_for_cmc = cmd_data_rows.get(partner_name, {})
        partner_cmc = partner_data_for_cmc.get("cmc", 0.0) or 0.0

    slot_targets = get_slot_targets(
        archetype=archetype,
        commander_cmc=commander_cmc,
        partner_cmc=partner_cmc,
    )
    adj_desc = describe_adjustments(archetype, commander_cmc, slot_targets)
    if adj_desc and "no adjustments" not in adj_desc:
        print(f"  Slot adjustments: {adj_desc}")

    selected: list[dict] = []
    missing:  list[dict] = []
    used_names: set[str] = set()

    LAND_TARGET    = slot_targets.get("land", 37)
    NON_LAND_TOTAL = 99 - LAND_TARGET  # = 62

    # Ref containers để capture pip analysis
    _pip_analysis_ref: list = [None]
    _basic_dist_ref:   list = [{}]

    # ── Step 7a: Land slot (xử lý riêng, không vào pool) ──────────────────
    # FIX: Non-basic lands sorted by synergy DESC + owned first.
    # EDHREC có synergy scores cho non-basic lands (Cabal Coffers ~0.15,
    # Command Tower ~0.05, etc.) nhưng trước đây chúng được add không theo thứ tự.
    # Utility lands quan trọng (Maze of Ith, Cabal Coffers) giờ được ưu tiên.
    land_pool = slot_pools.get("land", [])
    land_count = 0
    non_basics = sorted(
        [c for c in land_pool if not bl.is_basic_land(c["name"])],
        key=lambda c: (not c["is_owned"], -(c["synergy"] or 0.0))
    )
    for card in non_basics:
        if land_count >= LAND_TARGET:
            break
        if card["name"] in used_names:
            continue
        used_names.add(card["name"])
        selected.append(card)
        if not card["is_owned"]:
            missing.append(card)
        land_count += 1

    # ── Step 7b: Build unified non-land pool ──────────────────────────────
    # Gom tất cả non-land cards từ mọi slot vào 1 pool duy nhất
    unified_pool: list[dict] = []
    for slot, cards in slot_pools.items():
        if slot == "land":
            continue
        for card in cards:
            if card["name"] not in used_names and not bl.is_basic_land(card["name"]):
                unified_pool.append(card)

    # Dedup: mỗi card chỉ xuất hiện 1 lần trong pool
    seen_in_pool: set[str] = set()
    deduped_pool: list[dict] = []
    for card in unified_pool:
        if card["name"] not in seen_in_pool:
            seen_in_pool.add(card["name"])
            deduped_pool.append(card)

    # ── Step 7c: Tier-based picking — 62 picks ────────────────────────────
    # Track slot counts để tính slot_hunger real-time
    slot_counts: dict[str, int] = {s: 0 for s in slot_targets if s != "land"}

    # Hunger scaling: hunger giảm tuyến tính từ HUNGER_MAX → 0 khi slot đầy
    # FIX: tăng HUNGER_MAX từ 0.15 → 0.25
    # EDHREC synergy range: 0.02–0.30. Với 0.15, ramp card (0.05) + hunger (0.15)
    # = 0.20 bằng synergy card mạnh (0.20) → tie, slot không được ưu tiên đúng.
    # Với 0.25: ramp trống (0/10) thắng cả synergy 0.24, nhưng khi ramp đã 80%
    # (8/10) → hunger=0.05, nhường cho synergy card chất lượng cao. Đúng intent.
    HUNGER_MAX    = 0.25   # bonus tối đa khi slot hoàn toàn trống (tăng từ 0.15)
    OWNED_BONUS   = 0.05   # nhỏ — owned được ưu tiên nhưng không áp đảo score

    def slot_hunger(slot: str) -> float:
        """Bonus score cho card của slot còn thiếu. = 0 khi slot >= target."""
        target = slot_targets.get(slot, 0)
        if target <= 0:
            return 0.0
        current = slot_counts.get(slot, 0)
        deficit_ratio = max(0.0, (target - current) / target)
        return HUNGER_MAX * deficit_ratio

    picks_done = 0
    while picks_done < NON_LAND_TOTAL and deduped_pool:
        # Score tất cả cards trong pool với state hiện tại
        # O(n) per pick, n = pool size (~200-300), 62 picks → ~15,000 ops
        best_score = -1.0
        best_idx   = -1

        for i, card in enumerate(deduped_pool):
            if card["name"] in used_names:
                continue

            dyn  = dynamic_scorer.adjust_score(card)
            own  = OWNED_BONUS if card["is_owned"] else 0.0
            hung = slot_hunger(card["slot"])
            score = dyn + own + hung

            if score > best_score:
                best_score = score
                best_idx   = i

        if best_idx == -1:
            break  # pool cạn

        # Pick card tốt nhất
        card = deduped_pool.pop(best_idx)
        used_names.add(card["name"])
        selected.append(card)
        if not card["is_owned"]:
            missing.append(card)

        # Cập nhật trackers
        slot_counts[card["slot"]] = slot_counts.get(card["slot"], 0) + 1
        dynamic_scorer.register_pick(card)
        picks_done += 1

    # ── Step 7d: Basic land fill (pip-weighted) ────────────────────────────
    basic_remaining = LAND_TARGET - land_count
    if basic_remaining > 0:
        all_non_land_names = [
            c["name"] for c in selected if c["slot"] != "land"
        ]
        pip = analyze_pips(all_non_land_names, scryfall_data, commander_colors)
        distribution = calculate_basic_land_distribution(
            pip, basic_remaining, commander_colors, min_per_color=1
        )
        basic_cards = build_basic_land_list(distribution, collection_names)
        selected.extend(basic_cards)
        _pip_analysis_ref[0] = pip
        _basic_dist_ref[0]   = distribution

    # 9. Score deck
    # FIX avg_synergy: loại BASIC lands (synergy=0) ra — chúng không có EDHREC score.
    # Non-basic lands GIỮ LẠI vì có utility score thực (Command Tower, Cabal Coffers...).
    # Basic land được nhận ra qua is_basic_land() — chính xác hơn check slot="land"
    # vì non-basic lands cũng có slot="land".
    from filters.banned_list import is_basic_land as _is_basic
    scoring_cards = [c for c in selected if not _is_basic(c["name"])]
    owned_count = sum(1 for c in selected if c["is_owned"])
    avg_synergy = (
        sum(c["synergy"] for c in scoring_cards) / max(len(scoring_cards), 1)
    )
    coverage = owned_count / max(len(selected), 1)
    slot_balance = _score_slot_balance(selected, slot_targets)
    total_missing_price = sum(
        c["price_usd"] for c in missing if c["price_usd"] is not None
    )

    # FIX: dùng scorer.py để tính composite — 1 công thức duy nhất.
    # Tránh tình trạng deck_builder và scorer.py dùng 2 công thức khác nhau.
    # deck_builder tính sơ bộ để fill BuiltDeck, scorer.py sẽ override khi display.
    # composite ở đây dùng cùng weights với scorer.py (40/20/15/15/10)
    # nhưng chưa có curve/chain (chưa chạy scorer). Được bù bởi scorer.py sau.
    from engine.mana_curve import analyze_curve as _analyze_curve
    from engine.synergy_chain import analyze_synergy_chains as _analyze_chains
    _deck_cards_tmp = [
        type("_C", (), {"name": c["name"], "slot": c["slot"], "synergy": c["synergy"],
                        "is_owned": c["is_owned"], "cmc": c["cmc"] or 0.0,
                        "type_line": c["type_line"] or "", "price_usd": c.get("price_usd")})()
        for c in selected
    ]
    _oracle_tmp = {
        name: (scryfall_data.get(name) or {}).get("oracle_text", "") or ""
        for name in [c["name"] for c in selected]
    }
    _curve_tmp  = _analyze_curve(_deck_cards_tmp)
    _chains_tmp = _analyze_chains(_deck_cards_tmp, _oracle_tmp)
    synergy_norm = min(avg_synergy * 5, 1.0)
    composite = (
        0.40 * synergy_norm
        + 0.20 * coverage
        + 0.15 * _curve_tmp.curve_score
        + 0.15 * _chains_tmp.chain_score
        + 0.10 * slot_balance
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
        pip_analysis=_pip_analysis_ref[0],
        basic_distribution=_basic_dist_ref[0],
        curve_summary=dynamic_scorer.get_curve_summary(),
        top_chain_buffs=dynamic_scorer.get_top_buffed_cards(5),
    )


def _classify_card(name: str, card_row: dict, edhrec_row: dict, slots_config: dict) -> str:
    """
    Phân loại card vào slot theo thứ tự ưu tiên:
      1. Basic land check (hardcoded, không cần data)
      2. EDHREC slot_tag — nguồn đáng tin nhất vì dựa trên thống kê thực tế
      3. Type line check (land luôn là land)
      4. Known card lookup — catch các card nổi tiếng mà oracle text misleading
         Ví dụ: Dockside Extortionist (ramp), Smothering Tithe (ramp),
                Rhystic Study (draw) đã được classify bởi EDHREC tag rồi,
                nhưng các card mới ra chưa có EDHREC data thì cần fallback này
      5. Oracle text heuristic — fallback cuối cùng cho card không có EDHREC data

    FIX Bug 2: EDHREC tag được kiểm tra TRƯỚC oracle text.
    Card như Dockside Extortionist sẽ có slot_tag="ramp" từ EDHREC
    dù oracle text không chứa "add {" hay "mana artifact".
    """
    if bl.is_basic_land(name):
        return "land"

    type_line = card_row.get("type_line", "").lower() if card_row else ""
    oracle = card_row.get("oracle_text", "").lower() if card_row else ""
    edhrec_slot = edhrec_row.get("slot_tag", "") or ""

    # --- Bước 1: EDHREC tag (nguồn chính) ---
    EDHREC_SLOT_MAP = {
        "ramp": "ramp", "draw": "draw", "removal": "removal",
        "wipe": "wipe", "tutor": "tutor", "land": "land",
        "synergy": "synergy", "top": "synergy",
        "creature": "synergy", "instant": "synergy",
        "sorcery": "synergy", "enchantment": "synergy",
        "artifact": "synergy", "planeswalker": "synergy",
    }
    if edhrec_slot in EDHREC_SLOT_MAP:
        return EDHREC_SLOT_MAP[edhrec_slot]

    # --- Bước 2: Type line ---
    if "land" in type_line:
        return "land"

    # --- Bước 3: Known cards lookup ---
    # Catch cards mà oracle text không phản ánh đúng vai trò trong EDH
    # Thường là những card cực mạnh có mechanic gián tiếp
    slot = _lookup_known_card(name)
    if slot:
        return slot

    # --- Bước 4: Oracle text heuristic (fallback) ---
    slots_raw = _load_slots_raw_full()

    # Wipe trước removal để tránh "destroy target" khớp cả board wipe
    for kw in slots_raw.get("wipe_keywords", []):
        if kw.lower() in oracle:
            return "wipe"
    for kw in slots_raw.get("tutor_keywords", []):
        if kw.lower() in oracle:
            return "tutor"
    for kw in slots_raw.get("ramp_keywords", []):
        if kw.lower() in oracle:
            return "ramp"
    for kw in slots_raw.get("draw_keywords", []):
        if kw.lower() in oracle:
            return "draw"
    for kw in slots_raw.get("removal_keywords", []):
        if kw.lower() in oracle:
            return "removal"

    return "synergy"


# Known cards mà oracle text không phản ánh đúng slot EDH
# Key: card name (lowercase), Value: slot
_KNOWN_CARD_SLOTS: dict[str, str] = {
    # Ramp mạnh nhưng không có "add mana" trực tiếp
    "dockside extortionist": "ramp",
    "smothering tithe": "ramp",
    "black market connections": "ramp",
    "trouble in pairs": "draw",
    "ledger shredder": "synergy",
    "shorikai, genesis engine": "draw",
    "ephara, god of the polis": "draw",
    # Removal gián tiếp
    "bounce to hand": "removal",
    "collector ouphe": "removal",  # stax = removal-like
    "null rod": "removal",
    # Wipe mà không dùng "destroy all"
    "nevinyrral's disk": "wipe",
    "oblivion stone": "wipe",
    "deed": "wipe",
    "pernicious deed": "wipe",
    "aura shards": "removal",
    "bane of progress": "wipe",
}


def _lookup_known_card(name: str) -> str | None:
    """Tra cứu slot cho card trong known list. Trả về None nếu không có."""
    return _KNOWN_CARD_SLOTS.get(name.lower())


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




# Mapping màu WUBRG → basic land tương ứng
_COLOR_TO_BASIC = {
    'W': 'Plains',
    'U': 'Island',
    'B': 'Swamp',
    'R': 'Mountain',
    'G': 'Forest',
}
_COLORLESS_BASIC = 'Wastes'


def _pick_basic_lands(
    commander_colors: list[str],
    count: int,
    collection_names: set[str],
) -> list[dict]:
    """
    Tạo danh sách basic lands để fill phần còn lại của land slot.

    Phân phối đều các basic land theo màu của commander.
    Ưu tiên mark is_owned=True nếu user có basic đó trong collection.

    Args:
        commander_colors: list màu, vd ["W", "U", "B", "G"]
        count: số basic land cần thêm
        collection_names: để check ownership

    Returns:
        list[dict] với format DeckCard-compatible
    """
    if not commander_colors:
        # Colorless commander: dùng Wastes
        basics = [_COLORLESS_BASIC]
    else:
        basics = [_COLOR_TO_BASIC[c] for c in commander_colors if c in _COLOR_TO_BASIC]
        if not basics:
            basics = [_COLORLESS_BASIC]

    result = []
    for i in range(count):
        # Round-robin phân phối đều
        basic_name = basics[i % len(basics)]
        result.append({
            "name": basic_name,
            "slot": "land",
            "synergy": 0.0,
            "is_owned": basic_name in collection_names,
            "cmc": 0.0,
            "type_line": "Basic Land",
            "price_usd": None,
        })
    return result
# ── Known high-value utility lands ────────────────────────────────────────────
# Staple lands có synergy âm hoặc thấp trên EDHREC (quá phổ biến = không "đặc biệt")
# nhưng thực ra rất quan trọng. Bù bằng utility score riêng.
_UTILITY_LAND_SCORES: dict[str, float] = {
    # Mana fixing staples — quan trọng với mọi multicolor deck
    "command tower":                   0.18,
    "arcane sanctum":                  0.10,
    "path of ancestry":                0.10,
    "exotic orchard":                  0.10,
    "mana confluence":                 0.12,
    "city of brass":                   0.12,
    "reflecting pool":                 0.12,
    # Black utility — mana doubling
    "cabal coffers":                   0.22,
    "cabal stronghold":                0.12,
    "urborg, tomb of yawgmoth":        0.18,
    # Defensive / control utility
    "maze of ith":                     0.15,
    "glacial chasm":                   0.10,
    # Green utility
    "nykthos, shrine to nyx":          0.20,
    "cradle":                          0.25,
    "gaea's cradle":                   0.25,
    # Colorless utility
    "reliquary tower":                 0.10,
    "field of the dead":               0.15,
    "dark depths":                     0.12,
    "strip mine":                      0.10,
    "wasteland":                       0.10,
    "hall of heliod's generosity":     0.10,
    "high market":                     0.08,
    "phyrexian tower":                 0.10,
    "bojuka bog":                      0.08,
    "war room":                        0.10,
    # Fetches — fixing + graveyard synergy
    "polluted delta":                  0.12,
    "flooded strand":                  0.12,
    "bloodstained mire":               0.12,
    "wooded foothills":                0.12,
    "windswept heath":                 0.12,
    "verdant catacombs":               0.12,
    "misty rainforest":                0.12,
    "scalding tarn":                   0.12,
    "arid mesa":                       0.12,
    "marsh flats":                     0.12,
    # Shocks — dual color fixing
    "breeding pool":                   0.10,
    "hallowed fountain":               0.10,
    "watery grave":                    0.10,
    "blood crypt":                     0.10,
    "stomping ground":                 0.10,
    "temple garden":                   0.10,
    "godless shrine":                  0.10,
    "sacred foundry":                  0.10,
    "steam vents":                     0.10,
    "overgrown tomb":                  0.10,
}

def _land_utility_score(card_name: str, synergy: float) -> float:
    """
    Tính effective score cho non-basic land.

    Vấn đề: EDHREC synergy đo "phổ biến hơn baseline" — staple lands
    (Command Tower, Cabal Coffers) có synergy âm/thấp vì quá phổ biến
    nên không được coi là đặc biệt. Thực tế chúng rất quan trọng.

    Giải pháp: dùng max(edhrec_synergy, _UTILITY_LAND_SCORES.get(name, 0))
    Card không trong danh sách → dùng EDHREC synergy gốc.
    Card trong danh sách → lấy score cao hơn giữa EDHREC và utility.
    """
    utility = _UTILITY_LAND_SCORES.get(card_name.lower(), 0.0)
    return max(synergy or 0.0, utility)


def _get_price(card_row: dict | None) -> float | None:
    """
    Lấy giá USD cho card.
    FIX 2: Đọc từ scryfall_prices (TTL 7 ngày) thay vì từ scryfall_cards.
    card_row["price_usd"] được inject bởi enrich_cards() nếu có;
    fallback về cache.get_price_usd() nếu field chưa có.
    """
    if not card_row:
        return None
    # enrich_cards() inject price_usd trực tiếp vào card_row
    if "price_usd" in card_row and card_row["price_usd"] is not None:
        return card_row["price_usd"]
    # Fallback: đọc từ scryfall_prices table
    oracle_name = card_row.get("oracle_name") or card_row.get("name")
    if oracle_name:
        return cache.get_price_usd(oracle_name)
    return None


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
