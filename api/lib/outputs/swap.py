"""
outputs/swap.py — Gợi ý swap card trong deck hiện tại với card tốt hơn từ collection.

Logic:
  1. Nhận deck đang có (99 cards)
  2. Với mỗi card trong deck có synergy thấp (bottom quartile),
     tìm card trong collection chưa được dùng có synergy cao hơn và cùng slot
  3. Xếp hạng swap theo delta synergy
"""

from tabulate import tabulate
from engine.deck_builder import BuiltDeck, DeckCard
from enrichers import edhrec
from db import cache


def suggest_swaps(deck: BuiltDeck, max_swaps: int = 10) -> list[dict]:
    """
    Tìm swap opportunities trong deck.

    Returns:
        list of dicts: [{out_card, in_card, slot, synergy_delta, reason}]
    """
    collection_names = cache.get_collection_names()
    deck_card_names = {c.name for c in deck.cards}
    edhrec_cards = edhrec.get_commander_cards(deck.commander_slug)
    edhrec_lookup = {c["card_name"]: c for c in edhrec_cards}

    # Cards trong collection chưa trong deck
    available = [
        c for c in edhrec_cards
        if c["card_name"] in collection_names
        and c["card_name"] not in deck_card_names
    ]

    available_by_slot: dict[str, list] = {}
    for c in available:
        slot = c.get("slot_tag", "synergy") or "synergy"
        available_by_slot.setdefault(slot, []).append(c)
    for slot in available_by_slot:
        available_by_slot[slot].sort(key=lambda c: -c["synergy"])

    # Cards trong deck với synergy thấp (bottom 25%)
    deck_with_synergy = sorted(deck.cards, key=lambda c: c.synergy)
    cutoff_idx = max(len(deck_with_synergy) // 4, 5)
    swap_candidates = deck_with_synergy[:cutoff_idx]

    swaps = []
    used_in_candidates = set()

    for weak_card in swap_candidates:
        slot = weak_card.slot
        best_replacement = None

        for candidate in available_by_slot.get(slot, []):
            if candidate["card_name"] in used_in_candidates:
                continue
            delta = candidate["synergy"] - weak_card.synergy
            if delta > 0.02:  # minimum meaningful improvement
                best_replacement = candidate
                break

        # Fallback: thử slot synergy nếu không tìm được cùng slot
        if not best_replacement:
            for candidate in available_by_slot.get("synergy", []):
                if candidate["card_name"] in used_in_candidates:
                    continue
                delta = candidate["synergy"] - weak_card.synergy
                if delta > 0.05:
                    best_replacement = candidate
                    break

        if best_replacement:
            used_in_candidates.add(best_replacement["card_name"])
            delta = best_replacement["synergy"] - weak_card.synergy
            swaps.append({
                "out_card": weak_card.name,
                "in_card": best_replacement["card_name"],
                "slot": slot,
                "synergy_out": weak_card.synergy,
                "synergy_in": best_replacement["synergy"],
                "synergy_delta": delta,
                "reason": _explain_swap(weak_card, best_replacement),
            })

    swaps.sort(key=lambda s: -s["synergy_delta"])
    return swaps[:max_swaps]


def print_swaps(deck: BuiltDeck, max_swaps: int = 10):
    swaps = suggest_swaps(deck, max_swaps)

    if not swaps:
        print(f"\nKhông tìm thấy swap nào cải thiện deck {deck.commander_name}.")
        print("Collection của bạn đã được tối ưu cho commander này!")
        return

    print(f"\n{'='*70}")
    print(f"  CARD SWAP GỢI Ý — {deck.commander_name}")
    print(f"  {len(swaps)} swap tìm thấy từ collection của bạn")
    print(f"{'='*70}\n")

    rows = []
    for s in swaps:
        rows.append([
            f"OUT: {s['out_card']}",
            f"IN:  {s['in_card']}",
            s["slot"],
            f"{s['synergy_out']:+.0%} → {s['synergy_in']:+.0%}",
            f"+{s['synergy_delta']:.0%}",
        ])

    print(tabulate(
        rows,
        headers=["Bỏ ra", "Thêm vào", "Slot", "Synergy", "Delta"],
        tablefmt="rounded_outline",
    ))

    print()
    for s in swaps:
        if s["reason"]:
            print(f"  • {s['reason']}")
    print()


def _explain_swap(out: DeckCard, in_card: dict) -> str:
    delta_pct = (in_card["synergy"] - out.synergy) * 100
    return (
        f"Swap {out.name} → {in_card['card_name']} "
        f"(+{delta_pct:.0f}% synergy, slot: {out.slot})"
    )
