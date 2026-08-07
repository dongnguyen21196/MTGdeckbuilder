"""
importers/archidekt_csv.py — Parse CSV export từ Archidekt.

Archidekt CSV format (Collection → Export CSV):
  Quantity, Name, Edition, Edition Code, Condition, Foil

Ví dụ dòng:
  1,Atraxa Praetors' Voice,Commander 2016,C16,Near Mint,False
"""

import csv
import io
from pathlib import Path


REQUIRED_COLS = {"Quantity", "Name"}

CONDITION_MAP = {
    "Near Mint": "NM",
    "Lightly Played": "LP",
    "Moderately Played": "MP",
    "Heavily Played": "HP",
    "Damaged": "D",
    "": "NM",
}


def parse_csv(source: str | Path | io.TextIOBase) -> list[dict]:
    """
    Parse Archidekt CSV export.

    Args:
        source: file path (str/Path) hoặc file-like object

    Returns:
        list of dicts: [{name, quantity, set_code, foil, condition}, ...]
    """
    if isinstance(source, (str, Path)):
        with open(source, encoding="utf-8-sig") as f:
            content = f.read()
    else:
        content = source.read()

    reader = csv.DictReader(io.StringIO(content))
    headers = set(reader.fieldnames or [])

    missing = REQUIRED_COLS - headers
    if missing:
        raise ValueError(
            f"CSV thiếu cột bắt buộc: {missing}. "
            f"Đảm bảo export từ Archidekt Collection → Export CSV."
        )

    cards = []
    skipped = 0

    for row in reader:
        name = row.get("Name", "").strip()
        if not name:
            skipped += 1
            continue

        try:
            quantity = int(row.get("Quantity", "1").strip() or "1")
        except ValueError:
            quantity = 1

        foil_raw = row.get("Foil", "False").strip().lower()
        foil = foil_raw in ("true", "1", "yes", "foil")

        condition_raw = row.get("Condition", "").strip()
        condition = CONDITION_MAP.get(condition_raw, condition_raw or "NM")

        set_code = row.get("Edition Code", row.get("Set Code", "")).strip().upper()

        cards.append({
            "name": name,
            "quantity": quantity,
            "set_code": set_code or None,
            "foil": int(foil),
            "condition": condition,
        })

    if not cards:
        raise ValueError("CSV không có card nào hợp lệ.")

    if skipped:
        print(f"  [!] Bỏ qua {skipped} dòng trống.")

    return cards


def deduplicate(cards: list[dict]) -> list[dict]:
    """
    Gộp các bản của cùng 1 card (khác edition/foil) thành 1 entry,
    cộng dồn quantity. Dùng khi chỉ cần biết "có bao nhiêu bản".
    """
    merged: dict[str, dict] = {}
    for card in cards:
        name = card["name"]
        if name in merged:
            merged[name]["quantity"] += card["quantity"]
        else:
            merged[name] = dict(card)
    return list(merged.values())
