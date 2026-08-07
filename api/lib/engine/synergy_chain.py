"""
engine/synergy_chain.py — Phát hiện và score synergy chains giữa các cards.

Synergy chain là khi 2-3 cards trong deck "kết hợp" tạo ra hiệu ứng mạnh
hơn tổng các card riêng lẻ. Ví dụ:
  Rhystic Study + Smothering Tithe = draw + mana từ cùng 1 opponent action
  Doubling Season + Planeswalker = ult planeswalker ngay lần play đầu
  Deadeye Navigator + ETB creature = infinite ETB triggers

Cách tính:
  1. Phát hiện pair-chains: 2 cards có keyword overlap (mechanical synergy)
  2. Phát hiện theme-chains: nhiều cards cùng theme (sacrifice, proliferate...)
  3. Tính chain_density = số meaningful chains / tổng cards có thể pair
  4. chain_score đóng góp vào composite scoring

Approach dùng keyword-based matching (không cần ML):
  - Nhanh, deterministic, dễ debug
  - Có thể thiếu một số synergy phức tạp nhưng catch được majority
  - Dễ mở rộng bằng cách thêm PAIR_RULES mới
"""

from dataclasses import dataclass, field
from itertools import combinations
from engine.deck_builder import DeckCard


# ── Pair synergy rules ────────────────────────────────────────────────────────
# Mỗi rule: (trigger_keywords, payoff_keywords, label, strength)
# strength: 1=decent, 2=strong, 3=very strong/potential infinite

PAIR_RULES: list[tuple[list[str], list[str], str, int]] = [
    # Proliferate engine
    (["proliferate"], ["counter", "+1/+1", "charge counter", "poison", "loyalty"],
     "proliferate engine", 2),

    # Doubling effects
    (["doubling season", "doubling cube", "parallel lives", "anointed procession"],
     ["token", "counter", "planeswalker"],
     "doubling synergy", 3),

    # ETB abuse
    (["blink", "flicker", "leaves the battlefield and returns",
      "deadeye navigator", "conjurer's closet", "ephemerate"],
     ["enters the battlefield", "when this creature enters", "etb"],
     "ETB loop", 2),

    # Draw + tax
    (["whenever an opponent draws", "whenever a player draws",
      "rhystic study", "mystic remora"],
     ["smothering tithe", "treasure", "whenever a player casts"],
     "draw-tax engine", 3),

    # Sacrifice engine
    (["sacrifice a creature", "sacrifice another creature",
      "whenever a creature you control dies",
      "whenever another creature dies"],
     ["create a", "token", "draw a card", "you gain life", "deals damage"],
     "sacrifice engine", 2),

    # Reanimator
    (["return target creature card from your graveyard",
      "return a creature card from a graveyard",
      "animate dead", "reanimate", "entomb", "buried alive"],
     ["creature card in a graveyard", "from your graveyard",
      "mill", "discard", "dredge"],
     "reanimator package", 2),

    # Infinite mana setup
    (["add {c}{c}", "add mana equal", "untap target land", "gaea's cradle",
      "nykthos", "cabal coffers", "crypt of agadeem"],
     ["x spells", "spend this mana", "fireball", "torment of hailfire",
      "walking ballista", "exsanguinate", "aetherflux reservoir"],
     "infinite mana outlet", 3),

    # Wheel synergy
    (["wheel of fortune", "windfall", "timetwister", "draw seven",
      "discard your hand", "each player draws"],
     ["underworld breach", "library of leng", "teferi's puzzle box",
      "waste not", "psychic corrosion"],
     "wheel package", 2),

    # Storm setup
    (["storm", "whenever you cast a spell this turn",
      "past in flames", "yawgmoth's will"],
     ["ritual", "dark ritual", "cabal ritual", "pyretic ritual",
      "desperate ritual", "manamorphose"],
     "storm package", 3),

    # Graveyard hate protection (meta awareness)
    (["from your graveyard", "graveyard", "dredge", "flashback"],
     ["rest in peace", "leyline of the void", "grafdigger's cage",
      "ground seal", "tormod's crypt"],
     "GY synergy/hate interaction", 1),

    # Landfall
    (["landfall", "whenever a land enters the battlefield under your control"],
     ["fetch land", "fetchland", "harrow", "crucible of worlds",
      "ramunap excavator", "life from the loam"],
     "landfall engine", 2),

    # Voltron
    (["aura", "equipment", "attach", "equip"],
     ["commander damage", "double strike", "trample",
      "shroud", "hexproof", "totem armor"],
     "voltron package", 2),

    # Aristocrats
    (["whenever a creature you control dies", "whenever another creature dies",
      "blood artist", "zulaport cutthroat", "falkenrath noble"],
     ["sacrifice outlet", "sacrifice a creature", "free sacrifice",
      "ashnod's altar", "phyrexian altar", "altar of dementia"],
     "aristocrats engine", 3),

    # Counter abuse
    (["+1/+1 counter", "proliferate", "doubling season"],
     ["hardened scales", "branching evolution", "vorinclex",
      "corpsejack menace", "cytoplast manipulator"],
     "counter doubling", 2),

    # Token spam
    (["create a", "token", "anointed procession", "parallel lives"],
     ["overrun", "pathbreaker ibex", "craterhoof behemoth",
      "overwhelming stampede", "beastmaster ascension"],
     "token pump finisher", 2),

    # Stax + land denial
    (["winter orb", "static orb", "stasis", "tangle wire"],
     ["untap", "doesn't untap", "you untap", "teferi, mage of zhalfir"],
     "asymmetric stax", 3),

    # Combo win conditions
    (["thassa's oracle", "laboratory maniac", "jace, wielder of mysteries"],
     ["demonic consultation", "tainted pact", "doomsday",
      "draw from empty library", "milling yourself"],
     "labman combo", 3),

    # Food chain
    (["food chain", "misthollow griffin", "eternal scourge",
      "squee the immortal", "brook"],
     ["outlet", "enter the battlefield", "commander", "infinite mana"],
     "food chain engine", 3),
]


# ── Theme detection ───────────────────────────────────────────────────────────
# Theme: nhiều cards cùng thể hiện một mechanic → density score

THEME_KEYWORDS: dict[str, list[str]] = {
    "proliferate":   ["proliferate"],
    "sacrifice":     ["sacrifice a creature", "sacrifice another", "whenever a creature dies"],
    "token":         ["create a", "token", "creates tokens"],
    "graveyard":     ["from your graveyard", "from a graveyard", "dredge", "flashback", "unearth"],
    "counter":       ["+1/+1 counter", "add a counter", "remove a counter"],
    "landfall":      ["landfall", "whenever a land enters"],
    "blink":         ["exile", "returns to the battlefield", "flicker", "blink"],
    "wheels":        ["draw seven", "discard your hand and draw", "wheel"],
    "tribal":        ["other", "creatures you control get", "lord"],
    "spellslinger":  ["whenever you cast an instant or sorcery",
                      "whenever you cast a noncreature spell",
                      "magecraft"],
    "stax":          ["players can't", "spells cost", "opponents can't"],
    "reanimator":    ["return target creature card from your graveyard",
                      "return a creature card from a graveyard"],
}

THEME_DENSITY_THRESHOLDS = {
    "strong":  0.25,   # ≥25% card có theme keyword → theme rõ ràng
    "medium":  0.12,   # 12-25% → theme phụ
    "weak":    0.05,   # 5-12% → theme hint
}


@dataclass
class SynergyPair:
    card1: str
    card2: str
    rule_label: str
    strength: int   # 1, 2, 3


@dataclass
class ThemeResult:
    theme: str
    card_count: int
    density: float  # % deck có theme keyword
    level: str      # "strong" / "medium" / "weak"


@dataclass
class SynergyChainResult:
    pairs: list[SynergyPair]
    themes: list[ThemeResult]
    chain_density: float         # pairs / possible pairs (capped)
    chain_score: float           # 0-1, contribution to composite
    top_pairs: list[str]         # human-readable top synergy pairs
    dominant_theme: str | None   # theme mạnh nhất nếu có


def analyze_synergy_chains(
    cards: list[DeckCard],
    oracle_texts: dict[str, str] = None,
) -> SynergyChainResult:
    """
    Phát hiện và score synergy chains trong deck.

    Args:
        cards: 99 DeckCard
        oracle_texts: {card_name: oracle_text} từ Scryfall cache

    Returns:
        SynergyChainResult với pairs, themes, và scores
    """
    oracle_texts = oracle_texts or {}
    non_lands = [c for c in cards if c.slot != "land"]

    # Tạo lookup text cho mỗi card
    card_texts: dict[str, str] = {}
    for card in non_lands:
        oracle = oracle_texts.get(card.name, "").lower()
        card_texts[card.name] = f"{card.name.lower()} {oracle}"

    # 1. Detect pairs
    pairs = _detect_pairs(non_lands, card_texts)

    # 2. Detect themes
    themes = _detect_themes(non_lands, card_texts)

    # 3. Score
    n = len(non_lands)
    max_possible_pairs = n * (n - 1) / 2 if n > 1 else 1

    # Weight pairs by strength
    weighted_pairs = sum(p.strength for p in pairs)
    # Normalize: 30 weighted pairs từ 62 non-land cards = score 1.0
    chain_density = min(1.0, weighted_pairs / max(n * 0.5, 1))
    chain_score = _calc_chain_score(pairs, themes, n)

    # Top pairs for display
    top_pairs = _format_top_pairs(pairs[:5])

    # Dominant theme
    strong_themes = [t for t in themes if t.level == "strong"]
    dominant_theme = strong_themes[0].theme if strong_themes else None

    return SynergyChainResult(
        pairs=pairs,
        themes=themes,
        chain_density=round(chain_density, 3),
        chain_score=round(chain_score, 3),
        top_pairs=top_pairs,
        dominant_theme=dominant_theme,
    )


def _detect_pairs(
    cards: list[DeckCard],
    card_texts: dict[str, str],
) -> list[SynergyPair]:
    """Tìm tất cả meaningful pairs dựa trên PAIR_RULES."""
    pairs: list[SynergyPair] = []
    card_names = [c.name for c in cards]

    for rule_triggers, rule_payoffs, label, strength in PAIR_RULES:
        # Tìm cards khớp trigger
        trigger_cards = [
            name for name in card_names
            if any(kw.lower() in card_texts.get(name, "") for kw in rule_triggers)
        ]
        # Tìm cards khớp payoff
        payoff_cards = [
            name for name in card_names
            if any(kw.lower() in card_texts.get(name, "") for kw in rule_payoffs)
        ]

        # Tạo pairs: một bên trigger, một bên payoff, không trùng nhau
        for t in trigger_cards:
            for p in payoff_cards:
                if t != p:
                    pairs.append(SynergyPair(
                        card1=t, card2=p,
                        rule_label=label,
                        strength=strength,
                    ))

    # Deduplicate (same pair, different rule order)
    seen: set[frozenset] = set()
    unique_pairs: list[SynergyPair] = []
    for pair in sorted(pairs, key=lambda p: -p.strength):
        key = frozenset([pair.card1, pair.card2])
        if key not in seen:
            seen.add(key)
            unique_pairs.append(pair)

    return unique_pairs


def _detect_themes(
    cards: list[DeckCard],
    card_texts: dict[str, str],
) -> list[ThemeResult]:
    """Đo mật độ theme trong deck."""
    total = len(cards)
    if total == 0:
        return []

    results: list[ThemeResult] = []
    for theme, keywords in THEME_KEYWORDS.items():
        matching = [
            name for name, text in card_texts.items()
            if any(kw.lower() in text for kw in keywords)
        ]
        density = len(matching) / total

        if density < THEME_DENSITY_THRESHOLDS["weak"]:
            continue

        level = (
            "strong" if density >= THEME_DENSITY_THRESHOLDS["strong"] else
            "medium" if density >= THEME_DENSITY_THRESHOLDS["medium"] else
            "weak"
        )
        results.append(ThemeResult(
            theme=theme,
            card_count=len(matching),
            density=round(density, 3),
            level=level,
        ))

    return sorted(results, key=lambda t: -t.density)


def _calc_chain_score(
    pairs: list[SynergyPair],
    themes: list[ThemeResult],
    n_cards: int,
) -> float:
    """
    Tính chain_score 0-1.

    Công thức:
      pair_component  = min(1, weighted_pairs / target_pairs)
      theme_component = bonus từ strong themes
      chain_score     = 0.7 * pair_component + 0.3 * theme_component
    """
    target_pairs = n_cards * 0.4  # target: 40% cards tham gia ít nhất 1 meaningful pair
    weighted_pairs = sum(p.strength for p in pairs)
    pair_component = min(1.0, weighted_pairs / max(target_pairs, 1))

    strong = sum(1 for t in themes if t.level == "strong")
    medium = sum(1 for t in themes if t.level == "medium")
    theme_component = min(1.0, strong * 0.3 + medium * 0.1)

    return 0.7 * pair_component + 0.3 * theme_component


def _format_top_pairs(pairs: list[SynergyPair]) -> list[str]:
    """Format top pairs thành human-readable strings."""
    result = []
    for pair in pairs:
        stars = "★" * pair.strength
        result.append(f"{pair.card1} + {pair.card2} [{pair.rule_label}] {stars}")
    return result
