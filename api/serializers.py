"""
serializers.py — Chuyển các dataclass của engine thành JSON cho UI.

Bản CLI in kết quả ra terminal (outputs/ranked.py, swap.py, buylist.py).
UI cần cùng dữ liệu đó ở dạng cấu trúc, nên đây là lớp dịch — không tính toán
lại gì, chỉ đọc các trường mà engine đã điền sẵn.
"""

from dataclasses import asdict, is_dataclass

from engine.deck_builder import BuiltDeck
from engine.mana_pip import COLOR_TO_BASIC
from engine.scorer import DeckScoreBreakdown, score_deck


def _plain(value):
    """dataclass → dict, giữ nguyên các kiểu JSON gốc."""
    return asdict(value) if is_dataclass(value) else value


def card_json(card) -> dict:
    return {
        "name": card.name,
        "slot": card.slot,
        "synergy": round(card.synergy, 4),
        "isOwned": card.is_owned,
        "cmc": card.cmc,
        "typeLine": card.type_line,
        "priceUsd": card.price_usd,
    }


def score_json(sc: DeckScoreBreakdown) -> dict:
    return {
        "grade": sc.grade,
        "composite": round(sc.composite_score, 4),
        "summary": sc.summary,
        "components": {
            "synergy": round(sc.synergy_score, 4),
            "coverage": round(sc.coverage_score, 4),
            "curve": round(sc.curve_score, 4),
            "chains": round(sc.chain_score, 4),
            "slotBalance": round(sc.balance_score, 4),
        },
        "archetype": None if not sc.archetype else {
            "label": sc.archetype.label,
            "confidence": round(sc.archetype.confidence, 4),
            "description": sc.archetype.description,
        },
        "curve": None if not sc.curve else {
            "avgCmc": round(sc.curve.avg_cmc, 2),
            "distribution": sc.curve.curve_distribution,
            "verdict": sc.curve.verdict,
            "archetypeFit": sc.curve.archetype_fit,
        },
        "chains": None if not sc.chains else {
            "dominantTheme": sc.chains.dominant_theme,
            "pairCount": len(sc.chains.pairs),
            "topPairs": list(sc.chains.top_pairs[:5]),
        },
    }


def mana_base_json(deck: BuiltDeck) -> dict | None:
    """Phân bổ basic land theo tỉ lệ pip. Đây là thứ CLI in ở dòng 'Mana base'."""
    pip = deck.pip_analysis
    if not pip or not deck.basic_distribution:
        return None
    return {
        "summary": pip.distribution_summary,
        "basics": [
            {
                "color": color,
                "basic": COLOR_TO_BASIC.get(color, color),
                "count": deck.basic_distribution.get(color, 0),
                "pipRatio": round(pip.ratios.get(color, 0), 4),
            }
            for color in pip.present_colors
        ],
    }


def deck_summary_json(deck: BuiltDeck, sc: DeckScoreBreakdown) -> dict:
    """Bản gọn cho danh sách ranked — không kèm 99 card."""
    owned = sum(1 for c in deck.cards if c.is_owned)
    top_owned = sorted((c for c in deck.cards if c.is_owned), key=lambda c: -c.synergy)[:5]
    top_missing = sorted(deck.missing_cards, key=lambda c: -c.synergy)[:5]
    return {
        "commander": deck.commander_name,
        "slug": deck.commander_slug,
        "score": score_json(sc),
        "cardCount": len(deck.cards),
        "ownedCount": owned,
        "missingCount": len(deck.missing_cards),
        "totalPriceMissing": round(deck.total_price_missing, 2),
        "keyCards": [card_json(c) for c in top_owned],
        "topMissing": [card_json(c) for c in top_missing],
        "chainBuffs": [
            {"name": name, "multiplier": round(mult, 2)}
            for name, mult in (deck.top_chain_buffs or [])[:5]
        ],
    }


def deck_detail_json(deck: BuiltDeck, sc: DeckScoreBreakdown | None = None) -> dict:
    """Bản đầy đủ cho trang deck — kèm toàn bộ card và mana base."""
    sc = sc or score_deck(deck)
    return {
        **deck_summary_json(deck, sc),
        "cards": [card_json(c) for c in deck.cards],
        "missingCards": [card_json(c) for c in deck.missing_cards],
        "manaBase": mana_base_json(deck),
        "curveSummary": _plain(deck.curve_summary),
    }


def decklist_text(deck: BuiltDeck) -> str:
    """Text format Moxfield/Archidekt.

    export_decklist() chỉ ghi file khi được truyền output_path — bỏ trống thì
    nó trả về chuỗi, đúng thứ ta cần trên filesystem read-only của Vercel."""
    from outputs.decklist import export_decklist

    return export_decklist(deck)


def swaps_json(deck: BuiltDeck, max_swaps: int = 10) -> dict:
    """Gợi ý thay card yếu bằng card mạnh hơn có sẵn trong collection."""
    from outputs.swap import suggest_swaps

    rows = suggest_swaps(deck, max_swaps=max_swaps)
    return {
        "commander": deck.commander_name,
        "swaps": [
            {
                "outCard": s["out_card"],
                "inCard": s["in_card"],
                "slot": s["slot"],
                "synergyOut": round(s["synergy_out"], 4),
                "synergyIn": round(s["synergy_in"], 4),
                "synergyDelta": round(s["synergy_delta"], 4),
                "reason": s["reason"],
            }
            for s in rows
        ],
    }


def buylist_json(deck: BuiltDeck) -> dict:
    """Card còn thiếu, sắp theo synergy giảm dần, kèm giá và tổng tiền."""
    rows = sorted(deck.missing_cards, key=lambda c: -c.synergy)
    return {
        "commander": deck.commander_name,
        "items": [card_json(c) for c in rows],
        "totalUsd": round(sum(c.price_usd or 0 for c in rows), 2),
        "pricedCount": sum(1 for c in rows if c.price_usd),
        "unpricedCount": sum(1 for c in rows if not c.price_usd),
    }
