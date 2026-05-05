"""
engine/scorer.py — Composite deck scorer với giải thích chi tiết.

Dùng cho output "ranked" — mỗi deck được trình bày kèm breakdown điểm.
"""

from dataclasses import dataclass
from engine.deck_builder import BuiltDeck


@dataclass
class DeckScoreBreakdown:
    synergy_score: float        # 0-1, avg EDHREC synergy normalized
    coverage_score: float       # 0-1, % card từ collection
    balance_score: float        # 0-1, slot balance
    composite_score: float      # 0-1, weighted sum
    grade: str                  # A / B / C / D
    summary: str                # 1-line text summary


def score_deck(deck: BuiltDeck) -> DeckScoreBreakdown:
    synergy_norm = min(deck.synergy_score * 5, 1.0)
    composite = (
        0.50 * synergy_norm
        + 0.30 * deck.collection_coverage
        + 0.20 * deck.slot_balance_score
    )

    grade = _to_grade(composite)
    summary = _build_summary(deck, composite, grade)

    return DeckScoreBreakdown(
        synergy_score=synergy_norm,
        coverage_score=deck.collection_coverage,
        balance_score=deck.slot_balance_score,
        composite_score=composite,
        grade=grade,
        summary=summary,
    )


def _to_grade(score: float) -> str:
    if score >= 0.80:
        return "A"
    if score >= 0.65:
        return "B"
    if score >= 0.50:
        return "C"
    return "D"


def _build_summary(deck: BuiltDeck, composite: float, grade: str) -> str:
    owned = sum(1 for c in deck.cards if c.is_owned)
    total = len(deck.cards)
    missing = len(deck.missing_cards)

    parts = []
    if deck.collection_coverage >= 0.90:
        parts.append("gần như build được ngay")
    elif deck.collection_coverage >= 0.70:
        parts.append(f"cần mua thêm {missing} card")
    else:
        parts.append(f"cần mua nhiều ({missing} card thiếu)")

    if deck.synergy_score >= 0.15:
        parts.append("synergy rất cao")
    elif deck.synergy_score >= 0.08:
        parts.append("synergy ổn")

    if deck.total_price_missing and deck.total_price_missing > 0:
        parts.append(f"~${deck.total_price_missing:.0f} để hoàn chỉnh")

    return f"[{grade}] {deck.commander_name}: {', '.join(parts)}."
