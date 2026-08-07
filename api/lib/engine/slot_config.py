"""
engine/slot_config.py — Dynamic slot targets theo archetype và commander CMC.

Thay thế việc đọc cứng từ slots.json cho mọi archetype.

V2 FIX — Archetype-aware slot targets:
  slots.json chỉ là baseline "generic". Module này điều chỉnh
  targets theo archetype đã detect, phản ánh đúng cách mỗi
  playstyle phân bổ 62 non-land card slots.

  Ví dụ:
    Combo:   tutor=7, wipe=1, synergy=31  (ít wipe, nhiều tutor tìm combo)
    Aggro:   removal=10, draw=7, synergy=30 (nhiều removal, ít draw)
    Stax:    removal=10, wipe=4, draw=8   (nhiều interaction, đủ draw)
    Control: draw=12, removal=10, wipe=4  (nhiều card advantage + answers)

V7 FIX — Commander CMC adjusts ramp target:
  Commander CMC cao → cần nhiều ramp hơn để cast đúng hạn.
  Công thức: ramp_target = base + max(0, commander_cmc - 4) × 1.5
  Cap tại max từ slots.json (14).

  Ví dụ:
    Commander CMC=2 (Tymna): ramp = 10 (base, không đổi)
    Commander CMC=5 (Atraxa): ramp = 10 + (5-4)×1.5 = 11 (→ 11)
    Commander CMC=7 (Jodah):  ramp = 10 + (7-4)×1.5 = 14 (→ 14, capped)
    Commander CMC=9 (Emrakul): ramp = 10 + (9-4)×1.5 = 17 → 14 (capped)

Đảm bảo: tổng non-land targets ≈ 62 sau mọi điều chỉnh.
Tier-based pool picking tự xử lý nếu lệch nhẹ (không cần cân bằng tuyệt đối).
"""

import json
from pathlib import Path

_SLOTS_FILE = Path(__file__).parent.parent / "data" / "slots.json"

# ── Archetype slot target overrides ──────────────────────────────────────────
# Chỉ ghi các slot cần THAY ĐỔI so với generic baseline.
# Slot không liệt kê → giữ nguyên từ slots.json.
# Tổng non-land phải ≈ 62 (ramp+draw+removal+wipe+tutor+synergy).

_ARCHETYPE_OVERRIDES: dict[str, dict[str, int]] = {
    # Combo: win bằng infinite hoặc instant-win condition
    # Cần nhiều tutor tìm pieces, ít wipe (làm chậm combo của mình),
    # draw cao để cycle qua deck, synergy cao để pack combo pieces
    "combo": {
        "ramp":    9,   # -1 (combo thường chạy fast mana artifact nhẹ)
        "draw":    11,  # +1 (cần cycle)
        "removal": 6,   # -2 (ít interaction, focus on combo)
        "wipe":    2,   # -1 (min=2 từ slots.json — wipe reset combo của mình)
        "tutor":   6,   # +3 (capped by slots.json max=6 — cốt lõi combo)
        "synergy": 28,  # = (giữ)
    },

    # Control: kiểm soát bàn chơi, win bằng card advantage dài hạn
    # Cần nhiều draw, nhiều interaction, đủ wipe
    "control": {
        "ramp":    9,   # -1 (control thường nhẹ ramp, chơi mana rock)
        "draw":    13,  # +3 (card advantage là tất cả)
        "removal": 10,  # +2 (nhiều single-target interaction)
        "wipe":    4,   # +1 (reset board thường xuyên)
        "tutor":   2,   # -1 (ít cần tutor, thích raw card draw hơn)
        "synergy": 24,  # -4 (ít synergy piece hơn)
    },

    # Aggro: tấn công nhanh, creature beat-down
    # Ít draw (không cần), nhiều removal để clear blocker, ít tutor
    "aggro": {
        "ramp":    8,   # -2 (curve thấp, ít cần ramp nhiều)
        "draw":    8,   # giữ min (aggro vẫn cần draw để không cạn bài)
        "removal": 10,  # +2 (clear blockers)
        "wipe":    2,   # -1 (wipe kill creature của mình)
        "tutor":   1,   # -2 (aggro không cần tutor nhiều)
        "synergy": 33,  # +5 (creature + pump effects chiếm phần lớn)
    },

    # Stax: làm chậm đối thủ, tax + prison effects
    # Nhiều removal/wipe, đủ draw, ít tutor
    "stax": {
        "ramp":    10,  # = (cần ramp đủ để deploy pieces sớm)
        "draw":    9,   # -1 (stax thường có card draw trong engine)
        "removal": 10,  # +2 (nhiều targeted interaction)
        "wipe":    4,   # +1 (reset để deploy stax pieces lại)
        "tutor":   2,   # -1
        "synergy": 27,  # -1 (stax pieces chiếm synergy)
    },

    # Midrange: value-oriented, flexible
    # Gần với generic nhưng nghiêng về synergy hơn
    "midrange": {
        "ramp":    10,  # =
        "draw":    10,  # =
        "removal": 8,   # =
        "wipe":    3,   # =
        "tutor":   2,   # -1 (midrange thích draw hơn tutor)
        "synergy": 29,  # +1
    },

    # Generic / unknown: giữ nguyên baseline
    "generic": {},
}

# ── Ramp scaling theo commander CMC (V7) ─────────────────────────────────────
# Base CMC ngưỡng không cần tăng ramp
_CMC_RAMP_BASE = 4
# Mỗi CMC vượt ngưỡng → +1.5 ramp target
_CMC_RAMP_SCALE = 1.5


# ── Land target constants ──────────────────────────────────────────────────────
# Nguồn: EDHREC Superior Numbers — Nate Burgess formula
# "Number of lands = 31 + num_colors + commander_cmc"
# (với 0-CMC mana rocks đếm như lands, nhưng không track ở đây)
_LAND_BASE        = 31
_LAND_MIN         = 33   # deck combo/aggro curve thấp
_LAND_MAX         = 35   # hard cap — maximum 35 lands per deck

# Avg CMC → land adjustment (từ cEDH Nexus + EDHREC data):
# avg CMC < 2.5 → giảm thêm 2 (cEDH-style, rất ít land)
# avg CMC 2.5–3.5 → không đổi
# avg CMC > 3.5 → tăng thêm 1
_AVG_CMC_LAND_CUTS: list[tuple[float, int]] = [
    (2.0, -3),   # avg CMC ≤ 2.0 → -3 land
    (2.5, -2),   # avg CMC ≤ 2.5 → -2 land
    (3.0, -1),   # avg CMC ≤ 3.0 → -1 land
    (3.5,  0),   # avg CMC ≤ 3.5 → baseline
    (4.5,  1),   # avg CMC ≤ 4.5 → +1 land
    (99.0, 2),   # avg CMC > 4.5 → +2 land
]


def calc_land_target(
    num_colors: int,
    commander_cmc: float,
    avg_deck_cmc: float = 3.2,
) -> int:
    """
    Tính số land tối ưu theo EDHREC Superior Numbers formula:
      land = 31 + num_colors + commander_cmc

    Điều chỉnh thêm theo avg CMC thực tế của deck:
      avg CMC thấp (combo/aggro) → ít land hơn
      avg CMC cao (battlecruiser) → nhiều land hơn

    Args:
        num_colors:    số màu trong color identity (1-5)
        commander_cmc: CMC của commander
        avg_deck_cmc:  avg CMC của deck sau khi build (default 3.2 nếu chưa biết)

    Returns:
        int: số land target, capped trong [_LAND_MIN, _LAND_MAX]

    Ví dụ:
        Mono-color CMC=3:   31+1+3 = 35
        2-color CMC=2:      31+2+2 = 35
        4-color CMC=5:      31+4+5 = 40 → 35 (capped)
        5-color CMC=7:      31+5+7 = 43 → 35 (capped)
        Tymna+Thrasios:     31+4+2 = 37 → 35 (capped)
    """
    base = _LAND_BASE + num_colors + int(round(commander_cmc))

    # Adjust theo avg CMC
    cmc_adj = 0
    for threshold, adjustment in _AVG_CMC_LAND_CUTS:
        if avg_deck_cmc <= threshold:
            cmc_adj = adjustment
            break

    land = base + cmc_adj
    return max(_LAND_MIN, min(_LAND_MAX, land))


def get_slot_targets(
    archetype: str = "generic",
    commander_cmc: float = 3.0,
    partner_cmc: float = 0.0,
    num_colors: int = 3,
    avg_deck_cmc: float = 3.2,
) -> dict[str, int]:
    """
    Trả về slot targets điều chỉnh theo archetype, commander CMC,
    số màu, và avg CMC của deck.

    Args:
        archetype:     archetype đã detect
        commander_cmc: CMC của commander chính
        partner_cmc:   CMC của partner (nếu có), 0 nếu không
        num_colors:    số màu trong color identity (1-5) — dùng cho land target
        avg_deck_cmc:  avg CMC deck (sau build hoặc ước tính) — dùng cho land

    Returns:
        dict {slot: target_count} đã điều chỉnh
    """
    # Load baseline từ slots.json
    with open(_SLOTS_FILE, encoding="utf-8") as f:
        slots_data = json.load(f)
    baseline = {s: d["target"] for s, d in slots_data["slots"].items()}
    slot_min  = {s: d["min"]    for s, d in slots_data["slots"].items()}
    slot_max  = {s: d["max"]    for s, d in slots_data["slots"].items()}

    # Apply archetype overrides
    overrides = _ARCHETYPE_OVERRIDES.get(archetype, {})
    targets = {**baseline, **overrides}

    # V1 FIX: Land target theo num_colors + commander CMC + avg deck CMC
    # Thay thế LAND_TARGET = 37 cứng
    effective_cmc_for_land = commander_cmc
    if partner_cmc > 0:
        # Partner: dùng tổng CMC chia 2 (cả hai đều cần cast)
        effective_cmc_for_land = (commander_cmc + partner_cmc) / 2

    land_target = calc_land_target(num_colors, effective_cmc_for_land, avg_deck_cmc)
    targets["land"] = land_target

    # V7: Adjust ramp theo commander CMC
    effective_cmc = commander_cmc
    if partner_cmc > 0:
        effective_cmc = min(commander_cmc, partner_cmc)

    cmc_ramp_bonus = max(0.0, effective_cmc - _CMC_RAMP_BASE) * _CMC_RAMP_SCALE
    new_ramp = int(round(targets.get("ramp", 10) + cmc_ramp_bonus))
    targets["ramp"] = min(new_ramp, slot_max.get("ramp", 14))

    # Đảm bảo không xuống dưới min / vượt max
    for slot in targets:
        if slot in slot_min:
            targets[slot] = max(targets[slot], slot_min[slot])
        if slot in slot_max:
            targets[slot] = min(targets[slot], slot_max[slot])

    return targets


def describe_adjustments(
    archetype: str,
    commander_cmc: float,
    targets: dict[str, int],
    baseline: dict[str, int] | None = None,
    num_colors: int = 3,
    avg_deck_cmc: float = 3.2,
) -> str:
    """Human-readable mô tả các điều chỉnh so với baseline."""
    if baseline is None:
        with open(_SLOTS_FILE, encoding="utf-8") as f:
            baseline = {s: d["target"] for s, d in json.load(f)["slots"].items()}

    parts = []

    # Luôn hiển thị land target vì đây là thay đổi quan trọng
    land_new = targets.get("land", 37)
    land_old = baseline.get("land", 37)
    land_sign = "+" if land_new > land_old else ""
    parts.append(
        f"land={land_new}({land_sign}{land_new - land_old})"
        f"[{num_colors}色+CMC{commander_cmc:.0f}+avgCMC{avg_deck_cmc:.1f}]"
    )

    for slot, new_val in targets.items():
        if slot == "land":
            continue
        old_val = baseline.get(slot, new_val)
        if new_val != old_val:
            sign = "+" if new_val > old_val else ""
            parts.append(f"{slot}={new_val}({sign}{new_val - old_val})")

    return f"Archetype={archetype}, CMC={commander_cmc:.0f} → {', '.join(parts)}"
