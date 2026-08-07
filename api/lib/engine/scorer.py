"""
engine/scorer.py — Composite deck scorer tích hợp 5 thành phần.

Composite score (weights):
  40% → synergy_score     : avg EDHREC synergy (đã có từ trước)
  20% → collection_coverage: % card owned (đã có)
  15% → curve_score       : mana curve quality (MỚI)
  15% → chain_score       : synergy chains + themes (MỚI)
  10% → slot_balance      : slot distribution (đã có)

Thêm vào output:
  - archetype label + confidence
  - top synergy pairs
  - curve verdict
  - dominant theme
"""

from dataclasses import dataclass, field
from db import cache
from engine.deck_builder import BuiltDeck
from engine.mana_curve import analyze_curve, CurveAnalysis
from engine.archetype import detect_archetype, ArchetypeResult
from engine.synergy_chain import analyze_synergy_chains, SynergyChainResult


@dataclass
class DeckScoreBreakdown:
    # Scores (0-1)
    synergy_score:    float
    coverage_score:   float
    curve_score:      float
    chain_score:      float
    balance_score:    float
    composite_score:  float

    # Grade
    grade:   str   # A / B / C / D
    summary: str   # 1-line

    # Rich analysis
    archetype:   ArchetypeResult   | None = None
    curve:       CurveAnalysis     | None = None
    chains:      SynergyChainResult| None = None


def score_deck(deck: BuiltDeck) -> DeckScoreBreakdown:
    """
    Score deck với đầy đủ 5 thành phần.
    Oracle texts được đọc từ SQLite cache để tránh API call.
    """
    # Lấy oracle text từ cache cho tất cả cards
    oracle_texts = _load_oracle_texts([c.name for c in deck.cards])

    # 1. Mana curve
    curve = analyze_curve(deck.cards)

    # 2. Archetype
    archetype = detect_archetype(deck.cards, oracle_texts)

    # 3. Synergy chains
    chains = analyze_synergy_chains(deck.cards, oracle_texts)

    # 4. Component scores
    synergy_norm = min(deck.synergy_score * 5, 1.0)

    composite = (
        0.40 * synergy_norm
        + 0.20 * deck.collection_coverage
        + 0.15 * curve.curve_score
        + 0.15 * chains.chain_score
        + 0.10 * deck.slot_balance_score
    )

    grade = _to_grade(composite)
    summary = _build_summary(deck, composite, grade, archetype, chains)

    return DeckScoreBreakdown(
        synergy_score=round(synergy_norm, 3),
        coverage_score=round(deck.collection_coverage, 3),
        curve_score=round(curve.curve_score, 3),
        chain_score=round(chains.chain_score, 3),
        balance_score=round(deck.slot_balance_score, 3),
        composite_score=round(composite, 3),
        grade=grade,
        summary=summary,
        archetype=archetype,
        curve=curve,
        chains=chains,
    )


def _load_oracle_texts(card_names: list[str]) -> dict[str, str]:
    """Đọc oracle text từ SQLite cache cho tất cả cards."""
    texts = {}
    for name in card_names:
        row = cache.get_scryfall_card(name)
        if row and row["oracle_text"]:
            texts[name] = row["oracle_text"]
    return texts


def _to_grade(score: float) -> str:
    if score >= 0.80: return "A"
    if score >= 0.65: return "B"
    if score >= 0.50: return "C"
    return "D"


def _build_summary(
    deck: BuiltDeck,
    composite: float,
    grade: str,
    archetype: ArchetypeResult | None,
    chains: SynergyChainResult | None,
) -> str:
    owned = sum(1 for c in deck.cards if c.is_owned)
    missing = len(deck.missing_cards)
    parts = []

    # Archetype
    if archetype and archetype.confidence >= 0.4:
        parts.append(archetype.label)

    # Coverage
    if deck.collection_coverage >= 0.90:
        parts.append("build được ngay")
    elif deck.collection_coverage >= 0.70:
        parts.append(f"cần mua {missing} card")
    else:
        parts.append(f"thiếu nhiều ({missing} cards)")

    # Chains
    if chains and chains.dominant_theme:
        parts.append(f"theme: {chains.dominant_theme}")
    if chains and len(chains.pairs) >= 5:
        parts.append(f"{len(chains.pairs)} synergy pairs")

    # Price
    if deck.total_price_missing > 0:
        parts.append(f"~${deck.total_price_missing:.0f} để hoàn chỉnh")

    return f"[{grade}] {deck.commander_name}: {', '.join(parts)}."
