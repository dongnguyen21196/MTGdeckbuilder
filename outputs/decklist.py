"""
outputs/decklist.py — Export deck thành text file chuẩn Moxfield / Archidekt.

Format:
  1 Commander Name *CMDR*

  1 Card Name
  1 Another Card
  ...
  37 Forest   (basic lands nhóm cuối)
"""

from pathlib import Path
from engine.deck_builder import BuiltDeck


def export_decklist(deck: BuiltDeck, output_path: str | Path | None = None) -> str:
    """
    Tạo decklist text.

    Returns:
        string decklist (cũng ghi ra file nếu output_path được chỉ định)
    """
    lines = []

    # Commander line
    lines.append(f"1 {deck.commander_name} *CMDR*")
    lines.append("")

    # Group cards by slot
    slot_order = ["land", "ramp", "draw", "removal", "wipe", "tutor", "synergy", "other"]
    slot_labels = {
        "land": "// Lands",
        "ramp": "// Ramp",
        "draw": "// Card draw",
        "removal": "// Removal",
        "wipe": "// Board wipes",
        "tutor": "// Tutors",
        "synergy": "// Synergy",
        "other": "// Other",
        "creature": "// Creatures",
        "instant": "// Instants",
        "sorcery": "// Sorceries",
        "enchantment": "// Enchantments",
        "artifact": "// Artifacts",
        "planeswalker": "// Planeswalkers",
    }

    grouped: dict[str, list] = {}
    for card in deck.cards:
        grouped.setdefault(card.slot, []).append(card)

    # Sort within each slot: owned first, then synergy desc
    for slot in grouped:
        grouped[slot].sort(key=lambda c: (not c.is_owned, -c.synergy))

    # Emit in slot order, then any remaining slots
    emitted_slots = set()
    for slot in slot_order:
        if slot not in grouped:
            continue
        lines.append(slot_labels.get(slot, f"// {slot.title()}"))
        for card in grouped[slot]:
            marker = "" if card.is_owned else "  // MISSING"
            lines.append(f"1 {card.name}{marker}")
        lines.append("")
        emitted_slots.add(slot)

    for slot, cards in grouped.items():
        if slot in emitted_slots:
            continue
        lines.append(slot_labels.get(slot, f"// {slot.title()}"))
        for card in cards:
            marker = "" if card.is_owned else "  // MISSING"
            lines.append(f"1 {card.name}{marker}")
        lines.append("")

    text = "\n".join(lines).rstrip() + "\n"

    if output_path:
        Path(output_path).write_text(text, encoding="utf-8")
        print(f"  Decklist đã lưu: {output_path}")

    return text


def print_decklist(deck: BuiltDeck):
    """In decklist ra stdout."""
    text = export_decklist(deck)
    print(text)


def default_filename(commander_name: str) -> str:
    safe = commander_name.replace("'", "").replace(",", "").replace(" ", "_")
    return f"{safe}_deck.txt"
