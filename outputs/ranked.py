"""
outputs/ranked.py — In top N deck gợi ý với breakdown điểm và synergy lý do.
"""

from tabulate import tabulate
from engine.deck_builder import BuiltDeck
from engine.scorer import score_deck, DeckScoreBreakdown


def print_ranked(decks: list[BuiltDeck], top_n: int = 5):
    """In bảng ranked decks ra stdout."""
    if not decks:
        print("Không có deck nào được generate.")
        return

    print(f"\n{'='*70}")
    print(f"  TOP {min(top_n, len(decks))} EDH DECK GỢI Ý")
    print(f"{'='*70}\n")

    table_rows = []
    for i, deck in enumerate(decks[:top_n], 1):
        sc = score_deck(deck)
        owned = sum(1 for c in deck.cards if c.is_owned)
        table_rows.append([
            f"#{i}",
            deck.commander_name,
            f"{sc.grade}  ({sc.composite_score:.0%})",
            f"{owned}/{len(deck.cards)}",
            f"{sc.synergy_score:.0%}",
            f"${deck.total_price_missing:.0f}" if deck.total_price_missing else "—",
        ])

    print(tabulate(
        table_rows,
        headers=["#", "Commander", "Score", "Owned", "Synergy", "Buy"],
        tablefmt="rounded_outline",
    ))

    print()
    for i, deck in enumerate(decks[:top_n], 1):
        sc = score_deck(deck)
        _print_deck_detail(i, deck, sc)


def _print_deck_detail(rank: int, deck: BuiltDeck, sc: DeckScoreBreakdown):
    owned = sum(1 for c in deck.cards if c.is_owned)
    colors = " ".join(deck.cards[0].type_line[:5] if deck.cards else "")

    print(f"{'─'*70}")
    print(f"  #{rank}  {deck.commander_name}")
    print(f"       Score: {sc.grade} ({sc.composite_score:.0%})  "
          f"| Synergy: {sc.synergy_score:.0%}  "
          f"| Owned: {owned}/{len(deck.cards)} ({sc.coverage_score:.0%})  "
          f"| Balance: {sc.balance_score:.0%}")

    if deck.total_price_missing:
        print(f"       Mua thêm: {len(deck.missing_cards)} cards "
              f"(~${deck.total_price_missing:.2f})")

    # Top 5 synergy cards
    top_owned = sorted(
        [c for c in deck.cards if c.is_owned],
        key=lambda c: -c.synergy,
    )[:5]
    if top_owned:
        card_str = ", ".join(
            f"{c.name} (+{c.synergy:.0%})" for c in top_owned
        )
        print(f"       Điểm nhấn: {card_str}")

    # Missing highlights
    if deck.missing_cards:
        top_missing = sorted(deck.missing_cards, key=lambda c: -c.synergy)[:3]
        miss_str = ", ".join(
            f"{c.name}" + (f" (${c.price_usd:.1f})" if c.price_usd else "")
            for c in top_missing
        )
        print(f"       Nên mua:   {miss_str}")

    print()
