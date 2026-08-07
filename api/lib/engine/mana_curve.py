"""
engine/mana_curve.py — Phân tích mana curve của deck.

EDH curve chuẩn phụ thuộc vào archetype:
  Aggro/Tempo : heavy 1-3 CMC, ít 6+
  Midrange    : peak 3-4 CMC, một số payoffs 5-7
  Control     : trải đều 2-5, nhiều interaction
  Combo       : nhiều card CMC thấp + 1-2 combo pieces cao CMC
  Stax        : 1-4 CMC, nhiều artifact

Metric trả về:
  avg_cmc          : trung bình CMC của non-land cards
  curve_smoothness : 0-1, đo mức độ "trải đều" của curve
  early_game_pct   : % card CMC 1-2 (mở đầu)
  mid_game_pct     : % card CMC 3-4 (trung kỳ)
  late_game_pct    : % card CMC 5+ (hậu kỳ)
  curve_score      : 0-1, overall curve quality so với archetype target
"""

from dataclasses import dataclass
from collections import Counter
from engine.deck_builder import DeckCard


# Mana curve targets cho từng archetype (% of non-land cards)
CURVE_TARGETS: dict[str, dict] = {
    "aggro": {
        "avg_cmc_target": 2.5,
        "distribution": {1: 0.20, 2: 0.30, 3: 0.25, 4: 0.15, 5: 0.07, 6: 0.03},
    },
    "midrange": {
        "avg_cmc_target": 3.3,
        "distribution": {1: 0.08, 2: 0.18, 3: 0.28, 4: 0.24, 5: 0.14, 6: 0.08},
    },
    "control": {
        "avg_cmc_target": 3.0,
        "distribution": {1: 0.10, 2: 0.22, 3: 0.25, 4: 0.22, 5: 0.13, 6: 0.08},
    },
    "combo": {
        "avg_cmc_target": 2.8,
        "distribution": {1: 0.15, 2: 0.28, 3: 0.25, 4: 0.18, 5: 0.09, 6: 0.05},
    },
    "stax": {
        "avg_cmc_target": 2.8,
        "distribution": {1: 0.18, 2: 0.28, 3: 0.28, 4: 0.18, 5: 0.06, 6: 0.02},
    },
    "generic": {
        "avg_cmc_target": 3.1,
        "distribution": {1: 0.10, 2: 0.22, 3: 0.26, 4: 0.22, 5: 0.12, 6: 0.08},
    },
}


@dataclass
class CurveAnalysis:
    avg_cmc: float
    curve_distribution: dict[int, int]   # CMC → số lượng card
    curve_pct: dict[int, float]          # CMC → % của non-land cards
    early_game_pct: float                # CMC 1-2
    mid_game_pct: float                  # CMC 3-4
    late_game_pct: float                 # CMC 5+
    curve_smoothness: float              # 0-1
    curve_score: float                   # 0-1, so với archetype target
    archetype_fit: str                   # archetype curve khớp nhất
    verdict: str                         # nhận xét ngắn


def analyze_curve(cards: list[DeckCard], archetype: str = "generic") -> CurveAnalysis:
    """
    Phân tích mana curve của deck.

    Args:
        cards: 99 cards của deck (bao gồm cả non-owned)
        archetype: archetype để so sánh curve target

    Returns:
        CurveAnalysis với đầy đủ metrics
    """
    non_lands = [c for c in cards if c.slot != "land" and c.cmc is not None]
    if not non_lands:
        return _empty_analysis()

    cmcs = [c.cmc for c in non_lands]
    total = len(cmcs)

    # Distribution: CMC 0, 1, 2, 3, 4, 5, 6+
    raw_dist: Counter = Counter()
    for cmc in cmcs:
        bucket = min(int(cmc), 6)  # 6+ grouped together
        raw_dist[bucket] += 1

    curve_distribution = {i: raw_dist.get(i, 0) for i in range(7)}
    curve_pct = {
        cmc: count / total
        for cmc, count in curve_distribution.items()
    }

    avg_cmc = sum(cmcs) / total
    early = curve_pct.get(1, 0) + curve_pct.get(2, 0)
    mid   = curve_pct.get(3, 0) + curve_pct.get(4, 0)
    late  = sum(curve_pct.get(i, 0) for i in range(5, 7))

    # Smoothness: penalize bất kỳ "valley" nào giữa peak và end
    # Tính bằng cách đo std deviation của distribution (chuẩn hóa)
    import math
    pcts = [curve_pct.get(i, 0) for i in range(1, 7)]
    mean_pct = sum(pcts) / len(pcts)
    variance = sum((p - mean_pct) ** 2 for p in pcts) / len(pcts)
    std = math.sqrt(variance)
    # std thấp = đều = smooth; std cao = lệch nhiều
    smoothness = max(0.0, 1.0 - std * 3)  # scale: std~0.33 → score 0

    # So sánh với archetype target
    target = CURVE_TARGETS.get(archetype, CURVE_TARGETS["generic"])
    avg_cmc_diff = abs(avg_cmc - target["avg_cmc_target"]) / target["avg_cmc_target"]
    avg_cmc_score = max(0.0, 1.0 - avg_cmc_diff * 2)

    dist_error = sum(
        abs(curve_pct.get(cmc, 0) - target_pct)
        for cmc, target_pct in target["distribution"].items()
    )
    dist_score = max(0.0, 1.0 - dist_error)

    curve_score = 0.5 * avg_cmc_score + 0.5 * dist_score

    # Tìm archetype khớp nhất
    best_fit = _find_best_archetype_fit(curve_pct, avg_cmc)

    verdict = _build_verdict(avg_cmc, early, mid, late, curve_score)

    return CurveAnalysis(
        avg_cmc=round(avg_cmc, 2),
        curve_distribution=curve_distribution,
        curve_pct={k: round(v, 3) for k, v in curve_pct.items()},
        early_game_pct=round(early, 3),
        mid_game_pct=round(mid, 3),
        late_game_pct=round(late, 3),
        curve_smoothness=round(smoothness, 3),
        curve_score=round(curve_score, 3),
        archetype_fit=best_fit,
        verdict=verdict,
    )


def _find_best_archetype_fit(curve_pct: dict, avg_cmc: float) -> str:
    """Tìm archetype có curve target gần nhất với deck hiện tại."""
    best, best_error = "generic", float("inf")
    for name, target in CURVE_TARGETS.items():
        if name == "generic":
            continue
        avg_diff = abs(avg_cmc - target["avg_cmc_target"])
        dist_err = sum(
            abs(curve_pct.get(cmc, 0) - tp)
            for cmc, tp in target["distribution"].items()
        )
        error = avg_diff * 0.5 + dist_err * 0.5
        if error < best_error:
            best_error = error
            best = name
    return best


def _build_verdict(avg_cmc: float, early: float, mid: float, late: float, score: float) -> str:
    parts = []
    if avg_cmc < 2.5:
        parts.append("curve rất thấp, tempo-oriented")
    elif avg_cmc < 3.2:
        parts.append("curve cân bằng tốt")
    elif avg_cmc < 4.0:
        parts.append("curve hơi nặng, cần nhiều ramp")
    else:
        parts.append("curve nặng, phụ thuộc ramp mạnh")

    if early < 0.20:
        parts.append("thiếu card đầu game")
    if late > 0.30:
        parts.append("quá nhiều card nặng mana")
    if score >= 0.75:
        parts.append("curve nhất quán")

    return "; ".join(parts) if parts else "curve bình thường"


def _empty_analysis() -> CurveAnalysis:
    return CurveAnalysis(
        avg_cmc=0.0, curve_distribution={}, curve_pct={},
        early_game_pct=0.0, mid_game_pct=0.0, late_game_pct=0.0,
        curve_smoothness=0.0, curve_score=0.0,
        archetype_fit="unknown", verdict="không đủ data",
    )
