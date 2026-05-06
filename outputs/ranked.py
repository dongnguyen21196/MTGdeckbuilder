"""
outputs/ranked.py — In top N deck gợi ý với breakdown đầy đủ.
Tích hợp archetype, mana curve, và synergy chains vào display.
"""

from tabulate import tabulate
from engine.deck_builder import BuiltDeck
from engine.scorer import score_deck, DeckScoreBreakdown


def print_ranked(decks: list[BuiltDeck], top_n: int = 5):
    if not decks:
        print("Không có deck nào được generate.")
        return

    print(f"\n{'='*72}")
    print(f"  TOP {min(top_n, len(decks))} EDH DECK GỢI Ý")
    print(f"{'='*72}\n")

    # Summary table
    table_rows = []
    scored = []
    for i, deck in enumerate(decks[:top_n], 1):
        sc = score_deck(deck)
        scored.append((deck, sc))
        owned = sum(1 for c in deck.cards if c.is_owned)
        archetype_label = sc.archetype.label if sc.archetype else "?"
        table_rows.append([
            f"#{i}",
            deck.commander_name[:32],
            f"{sc.grade}  ({sc.composite_score:.0%})",
            archetype_label,
            f"{owned}/{len(deck.cards)}",
            f"{sc.synergy_score:.0%}",
            f"${deck.total_price_missing:.0f}" if deck.total_price_missing else "—",
        ])

    print(tabulate(
        table_rows,
        headers=["#", "Commander", "Score", "Archetype", "Owned", "Synergy", "Buy"],
        tablefmt="rounded_outline",
    ))

    # Detail per deck
    print()
    for i, (deck, sc) in enumerate(scored, 1):
        _print_deck_detail(i, deck, sc)


def _print_deck_detail(rank: int, deck: BuiltDeck, sc: DeckScoreBreakdown):
    owned = sum(1 for c in deck.cards if c.is_owned)

    print(f"{'─'*72}")
    print(f"  #{rank}  {deck.commander_name}")

    # Score breakdown
    print(f"       Score: {sc.grade} ({sc.composite_score:.0%})  "
          f"Synergy={sc.synergy_score:.0%}  "
          f"Curve={sc.curve_score:.0%}  "
          f"Chains={sc.chain_score:.0%}  "
          f"Coverage={sc.coverage_score:.0%}")

    # Archetype
    if sc.archetype:
        conf = f"{sc.archetype.confidence:.0%} confidence"
        print(f"       Archetype: {sc.archetype.label.upper()}  ({conf})")
        print(f"       → {sc.archetype.description}")

    # Mana curve
    if sc.curve:
        c = sc.curve
        dist = "  ".join(
            f"{cmc}cmc:{c.curve_distribution.get(cmc, 0)}"
            for cmc in range(1, 7)
        )
        print(f"       Curve: avg={c.avg_cmc:.1f}  [{dist}]")
        print(f"       → {c.verdict}  (fit: {c.archetype_fit})")

    # Mana pip analysis — basic land distribution
    if deck.pip_analysis and deck.basic_distribution:
        pip = deck.pip_analysis
        dist_parts = []
        from engine.mana_pip import COLOR_TO_BASIC
        for color in pip.present_colors:
            count = deck.basic_distribution.get(color, 0)
            basic = COLOR_TO_BASIC.get(color, color)
            pct = pip.ratios.get(color, 0)
            dist_parts.append(f"{basic}×{count}({pct:.0%}pip)")
        print(f"       Mana base: {pip.distribution_summary}  →  {', '.join(dist_parts)}")

    # Synergy chains
    if sc.chains:
        ch = sc.chains
        if ch.dominant_theme:
            print(f"       Theme: {ch.dominant_theme.upper()}  "
                  f"({len(ch.pairs)} synergy pairs)")
        if ch.top_pairs:
            print(f"       Top chains:")
            for pair_str in ch.top_pairs[:3]:
                print(f"         • {pair_str}")

    # Card highlights
    top_owned = sorted(
        [c for c in deck.cards if c.is_owned],
        key=lambda c: -c.synergy,
    )[:5]
    if top_owned:
        card_str = ", ".join(f"{c.name}(+{c.synergy:.0%})" for c in top_owned)
        print(f"       Key cards: {card_str}")

    if deck.missing_cards:
        top_miss = sorted(deck.missing_cards, key=lambda c: -c.synergy)[:3]
        miss_str = ", ".join(
            f"{c.name}" + (f"(${c.price_usd:.1f})" if c.price_usd else "")
            for c in top_miss
        )
        print(f"       Nên mua:   {miss_str}")

    if deck.total_price_missing:
        print(f"       Buy total: ~${deck.total_price_missing:.2f} USD")

    print()
