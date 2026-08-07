"""
engine/archetype.py — Tự động phát hiện archetype của deck từ card pool.

5 archetype chính trong EDH:
  combo   : win bằng infinite loop hoặc instant-win combo
  control : counter + removal heavy, win bằng card advantage
  stax    : prison/tax effects làm chậm đối thủ
  aggro   : creature beat-down, go-wide hoặc go-tall
  midrange: value-oriented, flexible threats + answers

Phương pháp:
  1. Signal scoring: mỗi card trong deck được check qua keyword signals
     cho từng archetype, tích lũy điểm signal
  2. Slot weighting: signal từ slot "synergy" có weight cao hơn
     vì phản ánh intent của deck (không phải staple utility)
  3. Confidence threshold: nếu không archetype nào đủ rõ,
     trả về "midrange" làm default
  4. Hybrid detection: nếu 2 archetype gần nhau về điểm,
     label là hybrid (vd "combo-control")
"""

from dataclasses import dataclass
from engine.deck_builder import DeckCard


# ── Signal definitions ────────────────────────────────────────────────────────
# Format: {signal_text: weight}
# weight > 0: positive signal cho archetype
# Tất cả check case-insensitive trên oracle_text + card name

COMBO_SIGNALS: dict[str, float] = {
    "infinite": 2.0,
    "win the game": 2.0,
    "you win": 2.0,
    "untap all": 1.5,
    "take an extra turn": 1.5,
    "copy target": 1.0,
    "storm": 1.5,
    "ritual": 1.0,
    "hermit druid": 2.0,
    "thassa's oracle": 2.0,
    "demonic consultation": 2.0,
    "doomsday": 2.0,
    "flash hulk": 1.5,
    "ad nauseam": 1.5,
    "underworld breach": 1.5,
    "wheels": 0.8,
    "wheel of fortune": 1.2,
    "necropotence": 1.2,
    "timetwister": 1.5,
    "walking ballista": 1.0,
    "ballista": 0.8,
    "triskelion": 1.0,
    "heliod": 0.8,
    "painter's servant": 1.2,
    "grindstone": 1.2,
}

CONTROL_SIGNALS: dict[str, float] = {
    "counter target spell": 1.5,
    "counter target": 1.0,
    "counter spell": 0.8,
    "draw a card": 0.5,
    "draw two cards": 0.8,
    "draw three cards": 1.0,
    "exile target": 0.8,
    "destroy target": 0.5,
    "return target": 0.5,
    "fog": 0.5,
    "propaganda": 1.2,
    "ghostly prison": 1.2,
    "rhystic study": 1.5,
    "mystic remora": 1.5,
    "phyrexian arena": 1.0,
    "cyclonic rift": 1.5,
    "force of will": 1.5,
    "mana drain": 1.5,
    "counterspell": 1.2,
    "swan song": 0.8,
    "arcane denial": 0.8,
    "at the beginning of your end step": 0.3,
}

STAX_SIGNALS: dict[str, float] = {
    "players can't": 2.0,
    "opponent can't": 1.5,
    "spells cost": 1.2,
    "each player can": 1.0,
    "whenever a player": 0.8,
    "tax": 0.5,
    "as an additional cost": 0.8,
    "lock": 0.8,
    "null rod": 2.0,
    "collector ouphe": 1.5,
    "winter orb": 2.0,
    "static orb": 2.0,
    "stasis": 2.0,
    "smokestack": 2.0,
    "tangle wire": 1.5,
    "blood moon": 1.5,
    "back to basics": 1.5,
    "rule of law": 1.5,
    "eidolon of rhetoric": 1.2,
    "sphere of resistance": 1.5,
    "trinisphere": 1.5,
    "torpor orb": 1.2,
    "grafdigger's cage": 1.0,
}

AGGRO_SIGNALS: dict[str, float] = {
    "haste": 0.8,
    "trample": 0.5,
    "double strike": 1.0,
    "first strike": 0.5,
    "whenever this creature deals combat damage": 1.2,
    "whenever a creature you control deals": 0.8,
    "attack with": 0.8,
    "combat damage": 0.5,
    "token": 0.3,
    "create a": 0.2,
    "+1/+1 counter": 0.3,
    "whenever a creature enters": 0.5,
    "anthem": 0.8,
    "battle cry": 1.0,
    "goad": 0.8,
    "menace": 0.5,
    "attack each combat": 0.8,
    "must attack": 0.5,
    "eminence": 1.0,
    "go wide": 0.5,
    "equipment": 0.3,
    "voltron": 0.5,
    "commander damage": 0.8,
    "aura": 0.3,
}

MIDRANGE_SIGNALS: dict[str, float] = {
    "enters the battlefield": 0.5,
    "when this creature": 0.3,
    "at the beginning of your upkeep": 0.5,
    "sacrifice": 0.3,
    "graveyard": 0.3,
    "return from your graveyard": 0.5,
    "value": 0.3,
    "landfall": 0.8,
    "aristocrats": 0.8,
    "whenever another creature dies": 1.0,
    "whenever a creature dies": 1.0,
    "death trigger": 0.8,
    "reanimator": 0.8,
    "reanimate": 0.8,
    "persist": 0.8,
    "undying": 0.8,
    "cascade": 1.0,
    "blink": 0.8,
    "flicker": 0.8,
    "etb": 0.5,
}

ARCHETYPE_SIGNALS = {
    "combo":    COMBO_SIGNALS,
    "control":  CONTROL_SIGNALS,
    "stax":     STAX_SIGNALS,
    "aggro":    AGGRO_SIGNALS,
    "midrange": MIDRANGE_SIGNALS,
}

# Weight multiplier theo slot (synergy = intent, utility = incidental)
SLOT_WEIGHTS = {
    "synergy":  1.5,
    "creature": 1.2,
    "instant":  1.0,
    "sorcery":  1.0,
    "enchantment": 1.0,
    "artifact": 0.8,
    "ramp":     0.4,   # ramp là utility, ít reflect intent
    "draw":     0.4,
    "removal":  0.5,
    "wipe":     0.6,
    "tutor":    0.8,
    "land":     0.0,
}


@dataclass
class ArchetypeResult:
    primary: str               # archetype chính
    secondary: str | None      # archetype phụ nếu hybrid
    label: str                 # "combo", "combo-control", etc.
    scores: dict[str, float]   # raw scores cho từng archetype
    confidence: float          # 0-1, mức độ rõ ràng
    key_cards: dict[str, list[str]]  # {archetype: [top signal cards]}
    description: str           # mô tả ngắn


def detect_archetype(cards: list[DeckCard], oracle_texts: dict[str, str] = None) -> ArchetypeResult:
    """
    Phát hiện archetype từ card pool.

    Args:
        cards: 99 DeckCard của deck
        oracle_texts: {card_name: oracle_text} từ Scryfall cache
                     (nếu None, chỉ dùng card name để detect)

    Returns:
        ArchetypeResult với primary archetype, scores, và key cards
    """
    oracle_texts = oracle_texts or {}
    raw_scores: dict[str, float] = {a: 0.0 for a in ARCHETYPE_SIGNALS}
    signal_cards: dict[str, list[tuple[str, float]]] = {a: [] for a in ARCHETYPE_SIGNALS}

    for card in cards:
        if card.slot == "land":
            continue

        slot_weight = SLOT_WEIGHTS.get(card.slot, 0.8)
        text = oracle_texts.get(card.name, "").lower()
        name_lower = card.name.lower()
        combined = f"{name_lower} {text}"

        for archetype, signals in ARCHETYPE_SIGNALS.items():
            card_score = 0.0
            for signal, weight in signals.items():
                if signal.lower() in combined:
                    card_score += weight

            if card_score > 0:
                weighted = card_score * slot_weight
                raw_scores[archetype] += weighted
                signal_cards[archetype].append((card.name, weighted))

    # Normalize scores thành 0-1
    max_score = max(raw_scores.values()) if any(raw_scores.values()) else 1.0
    norm_scores = {
        a: round(s / max_score, 3) if max_score > 0 else 0.0
        for a, s in raw_scores.items()
    }

    # Sort archetypes theo score
    sorted_archetypes = sorted(norm_scores.items(), key=lambda x: -x[1])
    primary_name, primary_score = sorted_archetypes[0]
    second_name, second_score   = sorted_archetypes[1]

    # Confidence: khoảng cách giữa primary và secondary
    gap = primary_score - second_score
    confidence = min(1.0, gap * 2 + 0.3)  # gap lớn → confident hơn

    # Hybrid detection: nếu secondary >= 70% của primary
    is_hybrid = second_score >= primary_score * 0.70 and primary_score > 0.2
    secondary = second_name if is_hybrid else None
    label = f"{primary_name}-{second_name}" if is_hybrid else primary_name

    # Top signal cards cho mỗi archetype
    key_cards = {
        a: [name for name, _ in sorted(cards_list, key=lambda x: -x[1])[:5]]
        for a, cards_list in signal_cards.items()
        if cards_list
    }

    description = _build_description(primary_name, secondary, norm_scores, confidence)

    return ArchetypeResult(
        primary=primary_name,
        secondary=secondary,
        label=label,
        scores=norm_scores,
        confidence=round(confidence, 3),
        key_cards=key_cards,
        description=description,
    )


def _build_description(primary: str, secondary: str | None, scores: dict, confidence: float) -> str:
    desc_map = {
        "combo":    "win bằng infinite combo hoặc instant-win condition",
        "control":  "kiểm soát bàn chơi bằng counterspell và removal",
        "stax":     "làm chậm đối thủ bằng prison/tax effects",
        "aggro":    "tấn công nhanh bằng creatures hoặc commander damage",
        "midrange": "value-oriented, linh hoạt giữa threats và answers",
    }

    conf_str = "rõ ràng" if confidence >= 0.7 else ("trung bình" if confidence >= 0.4 else "mơ hồ")
    primary_desc = desc_map.get(primary, primary)

    if secondary:
        secondary_desc = desc_map.get(secondary, secondary)
        return f"Hybrid {primary}/{secondary}: {primary_desc}, có yếu tố {secondary_desc} (confidence: {conf_str})"

    return f"{primary.title()}: {primary_desc} (confidence: {conf_str})"
