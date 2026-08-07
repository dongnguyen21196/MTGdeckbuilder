"""
outputs/buylist.py — Danh sách card thiếu cần mua để hoàn thiện deck.

Output gồm:
  - Card name + slot + synergy score
  - Giá USD từ Scryfall (nếu có)
  - Tổng estimated cost
  - Priority: HIGH (synergy > 0.2), MEDIUM (0.1-0.2), LOW (<0.1)
"""

from tabulate import tabulate
from pathlib import Path
from engine.deck_builder import BuiltDeck


def print_buylist(deck: BuiltDeck, output_path: str | Path | None = None):
    missing = deck.missing_cards
    if not missing:
        print(f"\nKhông cần mua thêm gì! Deck {deck.commander_name} "
              f"build được hoàn toàn từ collection.")
        return

    missing_sorted = sorted(missing, key=lambda c: (-c.synergy, c.slot))

    rows = []
    for card in missing_sorted:
        priority = _priority(card.synergy)
        price_str = f"${card.price_usd:.2f}" if card.price_usd else "—"
        rows.append([
            priority,
            card.name,
            card.slot,
            f"{card.synergy:+.0%}",
            price_str,
        ])

    print(f"\n{'='*70}")
    print(f"  BUYLIST — {deck.commander_name}")
    print(f"  {len(missing)} cards cần mua")
    if deck.total_price_missing:
        print(f"  Estimated total: ~${deck.total_price_missing:.2f} USD")
    print(f"{'='*70}\n")

    print(tabulate(
        rows,
        headers=["Priority", "Card", "Slot", "Synergy", "Price USD"],
        tablefmt="rounded_outline",
    ))

    # Breakdown by priority
    high = [c for c in missing if c.synergy >= 0.20]
    med = [c for c in missing if 0.10 <= c.synergy < 0.20]
    low = [c for c in missing if c.synergy < 0.10]

    print(f"\n  HIGH priority ({len(high)} cards): "
          + (f"${sum(c.price_usd for c in high if c.price_usd):.0f}" if high else "—"))
    print(f"  MED  priority ({len(med)} cards): "
          + (f"${sum(c.price_usd for c in med if c.price_usd):.0f}" if med else "—"))
    print(f"  LOW  priority ({len(low)} cards): "
          + (f"${sum(c.price_usd for c in low if c.price_usd):.0f}" if low else "—"))
    print()

    if output_path:
        _export_csv(missing_sorted, deck, output_path)


def _export_csv(missing, deck: BuiltDeck, path: str | Path):
    """Export buylist as CSV cho tiện mua hàng."""
    lines = ["priority,card_name,slot,synergy,price_usd"]
    for card in missing:
        lines.append(
            f"{_priority(card.synergy)},"
            f"{card.name},"
            f"{card.slot},"
            f"{card.synergy:.3f},"
            f"{card.price_usd or ''}"
        )
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    print(f"  Buylist CSV đã lưu: {path}")


def _priority(synergy: float) -> str:
    if synergy >= 0.20:
        return "HIGH"
    if synergy >= 0.10:
        return "MED"
    return "LOW"
