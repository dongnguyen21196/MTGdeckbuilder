"""
engine/mana_pip.py — Mana pip analysis cho optimal basic land distribution.

Vấn đề với round-robin đều:
  Atraxa (WUBG) deck có pip distribution thực tế:
    W: 15 pips (Swords, Wrath, Atraxa...) → 23%
    U: 28 pips (Counterspells, Rhystic Study...) → 43%
    B: 14 pips (Demonic Tutor, Phyrexian Arena...) → 22%
    G: 8 pips (ramp spells...) → 12%
  Round-robin cho 20 basics = 5W 5U 5B 5G → sai.
  Pip-weighted cho 20 basics = 5W 9U 4B 2G → đúng.

Methodology (dựa trên MTG Salvation pip tracker):
  1. Scan oracle text + mana_cost của 99 cards (bỏ qua lands)
  2. Đếm colored mana symbols: {W}, {U}, {B}, {R}, {G}
  3. Hybrid pip ({W/U}, {2/W}...): đếm 0.5 mỗi màu có liên quan
  4. Phyrexian pip ({W/P}): đếm 0.5 W (thường dùng life thay mana)
  5. Tính tỉ lệ → phân bổ basic land theo tỉ lệ pip, làm tròn thông minh

Kết quả: land base luôn đủ mana cho mana cost thực tế của deck.
"""

import re
import json
from collections import Counter
from dataclasses import dataclass

# Regex patterns để extract pip từ mana cost string
# Scryfall format: "{W}", "{2}{U}{U}", "{W/U}", "{W/P}", "{X}{B}{B}"
# Matches: {W}, {U}{U}, {W/U}, {W/P}, {2/W} — all colored pip variants
_PIP_RE = re.compile(r'\{([WUBRG](?:/[WUBRGP2])?|[2](?:/[WUBRG])|[WUBRG])\}', re.IGNORECASE)

# Màu chính WUBRG
COLORS = ['W', 'U', 'B', 'R', 'G']

# Màu → basic land
COLOR_TO_BASIC = {
    'W': 'Plains',
    'U': 'Island',
    'B': 'Swamp',
    'R': 'Mountain',
    'G': 'Forest',
}
COLORLESS_BASIC = 'Wastes'


@dataclass
class PipAnalysis:
    """Kết quả phân tích pip của toàn bộ deck."""
    raw_counts: dict[str, float]    # {W: 28.5, U: 41.0, ...} — raw pip count
    total_pips: float               # tổng tất cả colored pips
    ratios: dict[str, float]        # {W: 0.23, U: 0.43, ...} — tỉ lệ mỗi màu
    present_colors: list[str]       # màu nào có pip > 0, sorted by ratio desc
    distribution_summary: str       # human-readable, vd "U43% W23% B22% G12%"


def analyze_pips(
    card_names: list[str],
    scryfall_data: dict[str, dict],
    commander_colors: list[str],
) -> PipAnalysis:
    """
    Phân tích pip distribution của deck.

    Args:
        card_names: tên 99 cards (bỏ qua lands, tự lọc trong hàm)
        scryfall_data: {name: scryfall_row} từ enrich_cards()
        commander_colors: color identity của commander — dùng để
                          đảm bảo tất cả màu trong CI đều được tính
                          kể cả khi không có pip nào (ví dụ WUBG deck
                          với ít card G)

    Returns:
        PipAnalysis với raw counts, ratios, và present colors
    """
    counts: Counter = Counter({c: 0.0 for c in commander_colors})

    for name in card_names:
        row = scryfall_data.get(name, {})
        if not row:
            continue

        # Bỏ qua lands — không đếm pip của land cards
        type_line = row.get('type_line', '') or ''
        if 'Land' in type_line:
            continue

        # Parse pip từ mana_cost (nguồn chính xác nhất)
        mana_cost = row.get('mana_cost', '') or ''
        _count_pips_from_string(mana_cost, counts)

        # Parse pip từ oracle_text cho activated abilities và modal costs
        # Ví dụ: Urborg, Tomb of Yawgmoth không có mana cost nhưng oracle
        # có "{B}" symbols trong ability → thực ra là colorless
        # → chỉ đếm oracle text cho double-faced cards và split cards
        oracle = row.get('oracle_text', '') or ''
        type_lower = type_line.lower()
        if '//' in (row.get('name', '') or ''):
            # Split/DFC: oracle text cũng chứa pip quan trọng
            _count_pips_from_string(oracle, counts, weight=0.3)

    raw_counts = dict(counts)
    total = sum(raw_counts.values())

    if total == 0:
        # Edge case: deck không có màu nào (full colorless)
        ratios = {c: 0.0 for c in COLORS}
        present = []
    else:
        ratios = {c: raw_counts.get(c, 0.0) / total for c in COLORS}
        present = sorted(
            [c for c in COLORS if raw_counts.get(c, 0.0) > 0],
            key=lambda c: -ratios[c]
        )

    summary = ' '.join(
        f"{c}{ratios[c]:.0%}"
        for c in present
    )

    return PipAnalysis(
        raw_counts=raw_counts,
        total_pips=total,
        ratios=ratios,
        present_colors=present,
        distribution_summary=summary,
    )


def _count_pips_from_string(text: str, counts: Counter, weight: float = 1.0):
    """
    Parse và đếm pip từ một mana cost string hoặc oracle text.

    Xử lý:
      {W}, {U}, {B}, {R}, {G}  → +weight cho màu đó (full pip)
      {W/U}, {G/R}              → +0.5*weight mỗi màu (true hybrid, chọn 1 trong 2)
      {W/P}, {B/P}              → +0.5*weight màu đó (Phyrexian, thường pay life)
      {2/W}, {2/U}              → +0.5*weight (generic hybrid, thường pay generic)
      {X}, {C}, {1}, {T}, {S}   → bỏ qua (colorless/variable)

    Lý do Phyrexian và 2/C chỉ 0.5:
      Phyrexian: caster thường trả 2 life thay vì mana → ít đòi màu hơn full pip.
      2/C: thường trả 2 generic → màu optional, contribution thấp hơn.
    """
    for match in _PIP_RE.finditer(text):
        symbol = match.group(1).upper()

        if '/' in symbol:
            parts = symbol.split('/')
            colored_parts = [p for p in parts if p in 'WUBRG']
            non_colored = [p for p in parts if p not in 'WUBRG']  # P, 2, etc.

            if colored_parts:
                if non_colored:
                    # Hybrid với non-color (Phyrexian W/P, generic 2/W):
                    # mỗi colored part nhận 0.5 × weight (optional mana requirement)
                    for c in colored_parts:
                        counts[c] += weight * 0.5
                else:
                    # True color hybrid (W/U, G/R): split đều giữa các màu
                    share = weight / len(colored_parts)
                    for c in colored_parts:
                        counts[c] += share
        elif symbol in 'WUBRG':
            counts[symbol] += weight


def calculate_basic_land_distribution(
    pip_analysis: PipAnalysis,
    total_basics: int,
    commander_colors: list[str],
    min_per_color: int = 1,
) -> dict[str, int]:
    """
    Tính số basic land tối ưu cho từng màu dựa trên pip analysis.

    Thuật toán:
      1. Nếu chỉ 1 màu → tất cả basics là màu đó
      2. Tính raw allocation: total_basics × ratio mỗi màu
      3. Floor mỗi màu → phân bổ phần dư theo "largest remainder"
         để tổng = total_basics chính xác
      4. Enforce min_per_color = 1 cho mỗi màu trong commander CI
         (kể cả màu có pip ít — vẫn cần mana fix)

    Args:
        pip_analysis: kết quả từ analyze_pips()
        total_basics: số basic land cần fill
        commander_colors: CI để enforce minimum
        min_per_color: minimum 1 basic per color in CI

    Returns:
        dict {color: count} — tổng = total_basics
    """
    if total_basics <= 0:
        return {}

    colors_in_ci = [c for c in commander_colors if c in COLORS]

    if not colors_in_ci:
        # Colorless commander
        return {'C': total_basics}  # caller dùng Wastes

    if len(colors_in_ci) == 1:
        return {colors_in_ci[0]: total_basics}

    # Tính raw allocation theo pip ratio
    ratios = pip_analysis.ratios
    color_ratios = {c: ratios.get(c, 0.0) for c in colors_in_ci}
    total_ratio = sum(color_ratios.values())

    if total_ratio == 0:
        # Không có pip data — chia đều
        per_color = total_basics // len(colors_in_ci)
        remainder = total_basics % len(colors_in_ci)
        result = {c: per_color for c in colors_in_ci}
        for i, c in enumerate(colors_in_ci):
            if i < remainder:
                result[c] += 1
        return result

    # Normalize ratios
    norm_ratios = {c: r / total_ratio for c, r in color_ratios.items()}

    # Floor allocation
    raw = {c: norm_ratios[c] * total_basics for c in colors_in_ci}
    floored = {c: int(v) for c, v in raw.items()}
    remainder = total_basics - sum(floored.values())

    # Largest remainder method để phân bổ phần dư
    remainders = {c: raw[c] - floored[c] for c in colors_in_ci}
    sorted_by_remainder = sorted(
        remainders.keys(),
        key=lambda c: -remainders[c]
    )
    for i in range(remainder):
        floored[sorted_by_remainder[i % len(sorted_by_remainder)]] += 1

    # Enforce minimum 1 per color
    adjusted = dict(floored)
    for c in colors_in_ci:
        if adjusted.get(c, 0) < min_per_color:
            deficit = min_per_color - adjusted.get(c, 0)
            adjusted[c] = min_per_color
            # Lấy bớt từ màu có nhiều nhất
            donor = max(
                [x for x in colors_in_ci if x != c],
                key=lambda x: adjusted.get(x, 0)
            )
            adjusted[donor] = max(0, adjusted[donor] - deficit)

    # Đảm bảo tổng đúng (sau khi enforce minimum có thể lệch)
    current_total = sum(adjusted.values())
    if current_total != total_basics:
        diff = total_basics - current_total
        # Thêm/bớt vào màu có ratio cao nhất
        top_color = max(colors_in_ci, key=lambda c: norm_ratios.get(c, 0))
        adjusted[top_color] += diff

    return adjusted


def build_basic_land_list(
    distribution: dict[str, int],
    collection_names: set[str],
) -> list[dict]:
    """
    Tạo list DeckCard-compatible cho basic lands từ distribution.

    Args:
        distribution: {color: count} từ calculate_basic_land_distribution()
        collection_names: để check is_owned

    Returns:
        list[dict] với name, slot, synergy, is_owned, cmc, type_line
    """
    result = []
    for color, count in distribution.items():
        if color == 'C':
            land_name = COLORLESS_BASIC
        else:
            land_name = COLOR_TO_BASIC.get(color, COLORLESS_BASIC)

        for _ in range(count):
            result.append({
                'name': land_name,
                'slot': 'land',
                'synergy': 0.0,
                'is_owned': land_name in collection_names,
                'cmc': 0.0,
                'type_line': 'Basic Land',
                'price_usd': None,
            })

    return result


def format_pip_report(pip: PipAnalysis) -> str:
    """Human-readable pip report cho debug/output."""
    if pip.total_pips == 0:
        return "Không có pip nào (colorless deck?)"

    lines = [f"Tổng pip: {pip.total_pips:.1f}"]
    for c in pip.present_colors:
        count = pip.raw_counts.get(c, 0)
        ratio = pip.ratios.get(c, 0)
        basic = COLOR_TO_BASIC.get(c, '?')
        lines.append(f"  {c} ({basic}): {count:.1f} pip = {ratio:.0%}")

    return '\n'.join(lines)
