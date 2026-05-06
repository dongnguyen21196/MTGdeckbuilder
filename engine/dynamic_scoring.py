"""
engine/dynamic_scoring.py — Dynamic card scoring trong quá trình build deck.

Thay vì chỉ dùng EDHREC synergy tĩnh, module này điều chỉnh điểm pick
của mỗi card real-time dựa trên trạng thái deck hiện tại.

Hai cơ chế:

1. CURVE PENALTY
   Theo dõi CMC distribution của deck đang được build.
   Khi một CMC bracket đã đạt ngưỡng "đầy" theo chuẩn archetype,
   trừ điểm các card CMC cao hơn trong queue và cộng buff cho card CMC thấp.

   Tại sao cần: builder hiện tại "mù" về curve — nếu 10 card CMC5+
   đều có synergy cao thì deck sẽ có 10 card CMC5+, không cast được gì
   trước turn 5 dù collection có đủ ramp.

2. CHAIN BUFF
   Khi một card được chọn vào deck, scan pool còn lại tìm các card
   có synergy pair với card vừa chọn (theo PAIR_RULES).
   Nhân đôi điểm synergy của các card đó trong queue.

   Tại sao cần: EDHREC synergy base chỉ đo "card này phổ biến với commander".
   Nếu Doubling Season vừa được pick, các card planeswalker/token trong pool
   đột nhiên trở nên cực kỳ valuable — nhưng base synergy của chúng không đổi.
   Chain Buff capture được giá trị "contextual" này.

   Optimization: Chain Buff dùng pre-built index (trigger_map) thay vì
   scan O(n²) từng lần — chỉ tốn O(k) per pick, k = số rules matched.

Design decision — KHÔNG dùng ILP:
   ILP (Integer Linear Programming) cho kết quả tối ưu toán học nhưng:
   - EDHREC synergy score là proxy không chính xác → tối ưu trên dữ liệu sai
   - Dependency nặng (PuLP/CBC solver), slow cold start
   - Gap giữa Greedy+Dynamic và ILP < noise của input data
   Greedy với dynamic adjustment đủ tốt cho bài toán này.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from collections import defaultdict
# Import PAIR_RULES inline để tránh circular import
# (synergy_chain.py import DeckCard từ deck_builder.py)
# Chỉ cần trigger/payoff keywords, không cần full SynergyChain objects
_PAIR_RULES_RAW: list[tuple[list[str], list[str], str, int]] = [
    (["proliferate"], ["counter", "+1/+1", "charge counter", "poison", "loyalty"], "proliferate engine", 2),
    (["doubling season", "doubling cube", "parallel lives", "anointed procession"],
     ["token", "counter", "planeswalker"], "doubling synergy", 3),
    (["blink", "flicker", "leaves the battlefield and returns",
      "deadeye navigator", "conjurer's closet", "ephemerate"],
     ["enters the battlefield", "when this creature enters", "etb"], "ETB loop", 2),
    (["whenever an opponent draws", "whenever a player draws",
      "rhystic study", "mystic remora"],
     ["smothering tithe", "treasure", "whenever a player casts"], "draw-tax engine", 3),
    (["sacrifice a creature", "sacrifice another creature",
      "whenever a creature you control dies", "whenever another creature dies"],
     ["create a", "token", "draw a card", "you gain life", "deals damage"], "sacrifice engine", 2),
    (["return target creature card from your graveyard",
      "return a creature card from a graveyard",
      "animate dead", "reanimate", "entomb", "buried alive"],
     ["creature card in a graveyard", "from your graveyard", "mill", "discard", "dredge"], "reanimator package", 2),
    (["add {c}{c}", "add mana equal", "untap target land", "gaea's cradle",
      "nykthos", "cabal coffers"],
     ["x spells", "fireball", "torment of hailfire", "walking ballista", "exsanguinate"], "infinite mana outlet", 3),
    (["wheel of fortune", "windfall", "timetwister", "draw seven",
      "discard your hand", "each player draws"],
     ["underworld breach", "library of leng", "waste not", "psychic corrosion"], "wheel package", 2),
    (["storm", "whenever you cast a spell this turn", "past in flames", "yawgmoth's will"],
     ["ritual", "dark ritual", "cabal ritual", "pyretic ritual", "manamorphose"], "storm package", 3),
    (["landfall", "whenever a land enters the battlefield under your control"],
     ["fetch land", "harrow", "crucible of worlds", "ramunap excavator"], "landfall engine", 2),
    (["aura", "equipment", "attach", "equip"],
     ["commander damage", "double strike", "trample", "hexproof", "shroud"], "voltron package", 2),
    (["whenever a creature you control dies", "blood artist", "zulaport cutthroat"],
     ["sacrifice outlet", "ashnod's altar", "phyrexian altar", "altar of dementia"], "aristocrats engine", 3),
    (["+1/+1 counter", "proliferate", "doubling season"],
     ["hardened scales", "branching evolution", "vorinclex", "corpsejack menace"], "counter doubling", 2),
    (["create a", "token", "anointed procession", "parallel lives"],
     ["craterhoof behemoth", "overrun", "overwhelming stampede", "beastmaster ascension"], "token pump", 2),
    (["thassa's oracle", "laboratory maniac", "jace, wielder of mysteries"],
     ["demonic consultation", "tainted pact", "doomsday"], "labman combo", 3),
    (["whenever an opponent casts", "whenever a player casts"],
     ["draw a card", "create a treasure", "counter target"], "reactive engine", 2),
]


# ── CMC brackets theo archetype ───────────────────────────────────────────────
# Mỗi bracket = (cmc_min, cmc_max_inclusive, target_ratio)
# target_ratio: % trong non-land cards (62 cards)
CMC_BRACKETS_BY_ARCHETYPE: dict[str, list[tuple[float, float, float]]] = {
    "aggro":    [(0, 1, 0.22), (2, 2, 0.28), (3, 3, 0.24), (4, 4, 0.16), (5, 99, 0.10)],
    "midrange": [(0, 1, 0.10), (2, 2, 0.18), (3, 3, 0.28), (4, 4, 0.24), (5, 99, 0.20)],
    "control":  [(0, 1, 0.12), (2, 2, 0.22), (3, 3, 0.25), (4, 4, 0.22), (5, 99, 0.19)],
    "combo":    [(0, 1, 0.15), (2, 2, 0.28), (3, 3, 0.25), (4, 4, 0.18), (5, 99, 0.14)],
    "stax":     [(0, 1, 0.18), (2, 2, 0.28), (3, 3, 0.28), (4, 4, 0.18), (5, 99, 0.08)],
    "generic":  [(0, 1, 0.12), (2, 2, 0.20), (3, 3, 0.26), (4, 4, 0.22), (5, 99, 0.20)],
}

# Penalty/bonus multipliers cho curve adjustment
CURVE_OVERFULL_PENALTY = 0.70   # card ở bracket đã đầy → score × 0.70
CURVE_UNDERFULL_BONUS  = 1.20   # card ở bracket còn thiếu → score × 1.20
CURVE_BRACKET_TOLERANCE = 0.05  # cho phép lệch 5% khỏi target trước khi penalty

# Chain buff multiplier khi card match pair rule với card đã trong deck
CHAIN_BUFF_MULTIPLIER = 2.0    # synergy × 2.0 khi có chain
CHAIN_BUFF_STRENGTH_3 = 2.5    # strength=3 pair (potential infinite) → × 2.5

# Non-land card target (99 - 37 lands)
NON_LAND_TARGET = 62


@dataclass
class DynamicScorer:
    """
    Stateful scorer tracking deck state trong quá trình build.

    Khởi tạo 1 lần trước khi build, cập nhật sau mỗi lần pick card.
    """
    archetype: str = "generic"

    # CMC distribution hiện tại {cmc_bucket: count}
    _cmc_counts: dict[int, int] = field(default_factory=lambda: defaultdict(int))
    _total_non_land: int = 0

    # Oracle text index để check chain (pre-built từ pool)
    _oracle_index: dict[str, str] = field(default_factory=dict)

    # Chain buff map: {card_name: multiplier}
    # Được cập nhật mỗi khi 1 card mới được pick
    _chain_buffs: dict[str, float] = field(default_factory=dict)

    # Trigger index: {trigger_keyword: [(payoff_keywords, label, strength)]}
    # Pre-built để tránh scan PAIR_RULES O(n) mỗi lần
    _trigger_index: dict[str, list] = field(default_factory=dict)

    def __post_init__(self):
        self._build_trigger_index()

    def _build_trigger_index(self):
        """
        Pre-build trigger → payoff index từ _PAIR_RULES_RAW.
        Dùng khi 1 card mới được thêm vào deck: chỉ lookup triggers
        của card đó thay vì scan toàn bộ rules.
        """
        idx: dict[str, list] = defaultdict(list)
        for triggers, payoffs, label, strength in _PAIR_RULES_RAW:
            for trigger_kw in triggers:
                idx[trigger_kw.lower()].append((payoffs, label, strength))
        self._trigger_index = dict(idx)

    def set_oracle_index(self, oracle_texts: dict[str, str]):
        """
        Khởi tạo oracle text lookup cho pool cards.
        Gọi một lần trước khi build.
        """
        self._oracle_index = {
            name: f"{name.lower()} {text.lower()}"
            for name, text in oracle_texts.items()
        }

    def adjust_score(self, card: dict) -> float:
        """
        Tính điểm đã được điều chỉnh cho một card trong pool.

        Args:
            card: dict với fields: name, synergy, cmc, slot

        Returns:
            adjusted_score: float — base synergy × curve_factor × chain_factor
        """
        base = card.get("synergy", 0.0) or 0.0
        cmc  = card.get("cmc", 0.0) or 0.0
        slot = card.get("slot", "synergy")

        # Lands không cần adjust
        if slot == "land":
            return base

        # 1. Curve factor
        curve_factor = self._curve_factor(cmc)

        # 2. Chain factor (pre-computed, lookup O(1))
        chain_factor = self._chain_buffs.get(card["name"], 1.0)

        return base * curve_factor * chain_factor

    def register_pick(self, card: dict):
        """
        Cập nhật trạng thái deck sau khi chọn 1 card.
        Phải gọi SAU khi card được thêm vào selected[].

        Thực hiện:
          1. Cập nhật CMC tracker
          2. Tìm payoff cards trong pool và cập nhật chain_buffs
        """
        slot = card.get("slot", "synergy")
        cmc  = card.get("cmc", 0.0) or 0.0

        if slot != "land":
            bucket = self._cmc_bucket(cmc)
            self._cmc_counts[bucket] += 1
            self._total_non_land += 1

        # Chain buff: card vừa pick có trigger nào?
        self._update_chain_buffs_for_new_card(card)

    def _update_chain_buffs_for_new_card(self, new_card: dict):
        """
        Khi card mới được pick, tìm payoff cards trong oracle index
        và cập nhật chain_buffs của chúng.

        Complexity: O(T × P) với T = số trigger keywords match,
        P = số cards trong oracle index — chỉ chạy khi có match.
        """
        card_text = self._oracle_index.get(new_card["name"], "")
        if not card_text:
            return

        matched_payoff_groups: list[tuple[list[str], int]] = []

        for trigger_kw, payoff_groups in self._trigger_index.items():
            if trigger_kw in card_text:
                matched_payoff_groups.extend(payoff_groups)

        if not matched_payoff_groups:
            return

        # Với mỗi pool card, check nếu nó là payoff của bất kỳ trigger nào
        for pool_name, pool_text in self._oracle_index.items():
            if pool_name == new_card["name"]:
                continue

            best_multiplier = 1.0
            for payoffs, label, strength in matched_payoff_groups:
                if any(kw.lower() in pool_text for kw in payoffs):
                    mult = (CHAIN_BUFF_STRENGTH_3
                            if strength >= 3
                            else CHAIN_BUFF_MULTIPLIER)
                    best_multiplier = max(best_multiplier, mult)

            if best_multiplier > 1.0:
                # Stack với buff hiện tại (nếu có), nhưng cap ở 4.0
                current = self._chain_buffs.get(pool_name, 1.0)
                self._chain_buffs[pool_name] = min(4.0, current * best_multiplier)

    def _curve_factor(self, cmc: float) -> float:
        """
        Tính curve penalty/bonus dựa trên CMC và trạng thái curve hiện tại.
        """
        if self._total_non_land == 0:
            return 1.0

        brackets = CMC_BRACKETS_BY_ARCHETYPE.get(
            self.archetype, CMC_BRACKETS_BY_ARCHETYPE["generic"]
        )

        # Tìm bracket mà CMC này thuộc về
        card_bracket_target = None
        for cmc_min, cmc_max, target_ratio in brackets:
            if cmc_min <= cmc <= cmc_max:
                card_bracket_target = target_ratio
                # Đếm cards đã có trong bracket này
                bucket = self._cmc_bucket(cmc)
                current_count = self._cmc_counts.get(bucket, 0)
                current_ratio = current_count / max(self._total_non_land, 1)
                break
        else:
            return 1.0  # CMC ngoài range

        if card_bracket_target is None:
            return 1.0

        # So sánh với target ratio, áp dụng penalty/bonus
        overshoot = current_ratio - card_bracket_target
        if overshoot > CURVE_BRACKET_TOLERANCE:
            # Bracket đã đầy hơn target — penalty cho card này
            excess = min(overshoot / card_bracket_target, 0.5)
            return CURVE_OVERFULL_PENALTY - excess * 0.2
        elif overshoot < -CURVE_BRACKET_TOLERANCE:
            # Bracket còn thiếu — bonus cho card này
            return CURVE_UNDERFULL_BONUS
        else:
            return 1.0  # trong tolerance

    @staticmethod
    def _cmc_bucket(cmc: float) -> int:
        """CMC → bucket: 0,1,2,3,4,5+ """
        return min(int(cmc), 5)

    def get_curve_summary(self) -> dict:
        """Debug: trả về CMC distribution hiện tại."""
        total = max(self._total_non_land, 1)
        return {
            f"cmc{k}+": f"{v} ({v/total:.0%})"
            for k, v in sorted(self._cmc_counts.items())
        }

    def get_top_buffed_cards(self, n: int = 5) -> list[tuple[str, float]]:
        """Debug: top N cards đang có chain buff."""
        return sorted(
            self._chain_buffs.items(),
            key=lambda x: -x[1]
        )[:n]
