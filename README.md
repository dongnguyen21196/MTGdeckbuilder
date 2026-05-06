# EDH Deck Builder

**Build optimized Commander (EDH) decks from your Archidekt card collection.**

A Python CLI tool that analyzes your MTG collection, scores commanders by synergy potential, and suggests fully optimized 99-card decklists — powered by Scryfall and EDHREC data.

---

## Features

- **Collection import** — CSV export from Archidekt or direct API sync
- **Card enrichment** — Scryfall (oracle text, legality, price) + EDHREC (synergy scores, inclusion rates)
- **Smart commander picking** — color identity pre-filter reduces 1,800+ commanders to ~50–200 relevant candidates before scoring
- **Partner commander support** — detects partner ability, merges color identities, generates correct EDHREC slugs
- **Greedy slot-based deck builder** — fills 7 slots: land, ramp, draw, removal, wipe, tutor, synergy
- **Mana pip analysis** — counts `{W}{U}{B}{R}{G}` pips across all 99 cards (incl. hybrid & Phyrexian) and distributes basic lands by actual pip ratio, not round-robin
- **Archetype detection** — auto-classifies decks as combo / control / stax / aggro / midrange with hybrid support
- **Mana curve scoring** — evaluates curve against archetype targets (aggro, midrange, control, combo, stax)
- **Synergy chain detection** — finds meaningful card pairs (ETB loops, aristocrats, wheels, storm, voltron…) and theme density
- **Reprint deduplication** — ownership check uses oracle name so any printing of a card counts as owned
- **Composite scoring** — 5-component score: EDHREC synergy 40% + collection coverage 20% + curve 15% + chains 15% + slot balance 10%
- **Auto-filter banned list** — synced from Scryfall, enforced automatically
- **4 output modes** — ranked suggestions, Moxfield-ready decklist, card swap upgrades, buylist with prices
- **SQLite local cache** — oracle data cached 30 days, prices cached 7 days; offline after first fetch

---

## Requirements

- Python 3.11+
- Internet connection (Scryfall + EDHREC APIs)

---

## Installation

```bash
git clone https://github.com/dongnguyen21196/MTGdeckbuilder.git
cd MTGdeckbuilder
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:

```
ARCHIDEKT_API_KEY=your_key_here   # optional — only needed for --api import
ARCHIDEKT_USERNAME=your_username  # optional
```

---

## Quick Start

```bash
# 1. One-time setup — fetch banned list + commander list (~3–5 min)
python cli.py update --banned-list --commanders

# 2. Import your collection
python cli.py import --csv my_collection.csv

# 3. Get deck suggestions
python cli.py build --output ranked --top 5
```

---

## Usage

### Import collection

```bash
# From Archidekt CSV export  (Archidekt → Collection → Export)
python cli.py import --csv my_collection.csv

# From Archidekt API (requires ARCHIDEKT_API_KEY in .env)
python cli.py import --api

# View collection stats
python cli.py collection --stats
python cli.py collection --list
```

### Build decks

```bash
# Top 5 deck suggestions from your collection
python cli.py build --output ranked --top 5

# Include commanders you don't own (maximizes card reuse)
python cli.py build --output ranked --include-unowned-commanders --top 10

# Build a specific commander
python cli.py build --commander "Atraxa, Praetors' Voice" --output ranked

# Export decklist (Moxfield / Archidekt compatible)
python cli.py build --commander "Atraxa, Praetors' Voice" --output decklist
python cli.py build --commander "Atraxa, Praetors' Voice" --output decklist --save atraxa.txt

# Card swap suggestions from your collection
python cli.py build --commander "Atraxa, Praetors' Voice" --output swap

# Buylist — missing cards with prices
python cli.py build --commander "Atraxa, Praetors' Voice" --output buylist
python cli.py build --commander "Atraxa, Praetors' Voice" --output buylist --save buylist.csv

# Partner commanders
python cli.py build --commander "Tymna the Weaver" --partner "Thrasios, Triton Hero" --output ranked
```

### Update data

```bash
python cli.py update --banned-list          # refresh EDH banned list from Scryfall
python cli.py update --commanders           # refresh commander list from Scryfall
python cli.py update --clear-edhrec-cache   # force re-fetch EDHREC synergy data
python cli.py update --refresh-prices       # force re-fetch all card prices
python cli.py update --db-stats             # show database statistics
```

---

## Project Structure

```
MTGdeckbuilder/
├── cli.py                        # Entry point — Click CLI
├── importers/
│   ├── archidekt_csv.py          # Archidekt CSV parser
│   └── archidekt_api.py          # Archidekt REST API client
├── enrichers/
│   ├── scryfall.py               # Card oracle data + price, two-pass TTL
│   └── edhrec.py                 # Synergy scores via json.edhrec.com
├── engine/
│   ├── commander_picker.py       # Color pre-filter + composite commander scoring
│   ├── deck_builder.py           # Greedy slot-based deck builder
│   ├── mana_pip.py               # Pip analysis + Largest Remainder distribution
│   ├── mana_curve.py             # Curve analysis vs archetype targets
│   ├── archetype.py              # Signal-based archetype detector
│   ├── synergy_chain.py          # Pair + theme synergy detection
│   └── scorer.py                 # 5-component composite scorer
├── filters/
│   └── banned_list.py            # EDH banned list + color identity enforcement
├── outputs/
│   ├── ranked.py                 # Rich ranked output with archetype / curve / chains
│   ├── decklist.py               # Moxfield-format text export
│   ├── swap.py                   # Upgrade suggestions from collection
│   └── buylist.py                # Missing cards sorted by priority + price
├── db/
│   └── cache.py                  # SQLite wrapper with schema migrations
└── data/
    └── slots.json                # Slot targets + keyword rules per archetype
```

---

## Scoring System

Each deck receives a composite score from 5 components:

| Component | Weight | Description |
|---|---|---|
| EDHREC synergy | 40% | Average synergy score of 99 cards with the commander |
| Collection coverage | 20% | % of cards already owned (no purchase needed) |
| Mana curve | 15% | Curve quality vs archetype target (aggro/midrange/control/combo/stax) |
| Synergy chains | 15% | Meaningful card pairs + theme density (aristocrats, ETB loops, wheels…) |
| Slot balance | 10% | How well slots match the archetype's ideal distribution |

**Grade scale:** A ≥ 80% · B ≥ 65% · C ≥ 50% · D < 50%

### Mana pip analysis

Basic land distribution is calculated by counting colored mana symbols across all 99 non-land cards:

| Symbol type | Weight |
|---|---|
| `{W}` `{U}` `{B}` `{R}` `{G}` | 1.0 per pip |
| `{W/U}` `{G/R}` (hybrid) | 0.5 per color |
| `{W/P}` `{B/P}` (Phyrexian) | 0.5 (often paid with life) |
| `{2/W}` `{2/U}` (generic hybrid) | 0.5 (often paid with generic) |
| `{X}` `{C}` `{1}` (colorless) | 0.0 — ignored |

Allocation uses the **Largest Remainder Method** to guarantee exact totals with minimum 1 basic per color.

**Example — Atraxa WUBG deck, 20 basics needed:**

| Method | Plains | Island | Swamp | Forest |
|---|---|---|---|---|
| Old (round-robin) | 5 | 5 | 5 | 5 |
| New (pip-weighted) | 6 | 7 | 5 | 2 |

Actual pip distribution: U 36% · W 27% · B 27% · G 9%

---

## Data Sources

| Source | Data | Cache TTL |
|---|---|---|
| [Scryfall](https://scryfall.com/docs/api) | Oracle text, type, color identity, legality | 30 days |
| [Scryfall](https://scryfall.com/docs/api) | Card prices (USD / EUR) | 7 days |
| [EDHREC](https://edhrec.com) | Synergy scores, inclusion rates, slot tags | 7 days |
| [Archidekt](https://archidekt.com) | User collection | On import |

All data is cached in a local SQLite database (`data/edh_builder.db`). Schema migrations run automatically on startup.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ARCHIDEKT_API_KEY` | — | Archidekt API key (optional) |
| `ARCHIDEKT_USERNAME` | — | Archidekt username (optional) |
| `SCRYFALL_CACHE_TTL_DAYS` | 30 | Oracle data cache lifetime |
| `SCRYFALL_PRICE_TTL_DAYS` | 7 | Price cache lifetime |
| `EDHREC_CACHE_TTL_DAYS` | 7 | EDHREC data cache lifetime |

---

---

# EDH Deck Builder — Tiếng Việt

**Tạo deck Commander (EDH) tối ưu từ bộ sưu tập card Archidekt của bạn.**

Công cụ CLI Python phân tích collection, chấm điểm commander theo tiềm năng synergy, và gợi ý decklist 99 card tối ưu — dựa trên dữ liệu từ Scryfall và EDHREC.

---

## Tính năng

- **Import collection** — CSV export từ Archidekt hoặc sync trực tiếp qua API
- **Làm giàu dữ liệu card** — Scryfall (oracle text, legality, giá) + EDHREC (synergy scores, tỉ lệ inclusion)
- **Commander picker thông minh** — pre-filter theo color identity, thu hẹp 1.800+ commanders còn 50–200 candidates trước khi chấm điểm
- **Hỗ trợ partner commanders** — detect partner ability, merge color identity, tạo slug EDHREC đúng format
- **Deck builder theo slot** — fill 7 slots: land, ramp, draw, removal, wipe, tutor, synergy
- **Mana pip analysis** — đếm pip `{W}{U}{B}{R}{G}` từ 99 cards (kể cả hybrid & Phyrexian), phân bổ basic land theo tỉ lệ pip thực tế, không chia đều
- **Phát hiện archetype tự động** — combo / control / stax / aggro / midrange, hỗ trợ hybrid
- **Mana curve scoring** — đánh giá curve so với target của từng archetype
- **Phát hiện synergy chains** — tìm cặp card có tương tác (ETB loops, aristocrats, wheels, storm...) và mật độ theme
- **Reprint deduplication** — check ownership theo oracle name, mọi printing đều được tính là owned
- **Chấm điểm tổng hợp** — 5 thành phần: synergy EDHREC 40% + coverage 20% + curve 15% + chains 15% + slot balance 10%
- **Auto-filter banned list** — sync từ Scryfall, tự động loại card bị banned
- **4 output modes** — ranked suggestions, decklist Moxfield-ready, gợi ý swap, buylist có giá
- **Cache SQLite local** — oracle data cache 30 ngày, giá cache 7 ngày; offline sau lần fetch đầu

---

## Yêu cầu hệ thống

- Python 3.11+
- Kết nối Internet (Scryfall + EDHREC APIs)

---

## Cài đặt

```bash
git clone https://github.com/dongnguyen21196/MTGdeckbuilder.git
cd MTGdeckbuilder
pip install -r requirements.txt
cp .env.example .env
```

Chỉnh sửa `.env`:

```
ARCHIDEKT_API_KEY=your_key_here   # tùy chọn — chỉ cần khi dùng --api
ARCHIDEKT_USERNAME=your_username  # tùy chọn
```

---

## Bắt đầu nhanh

```bash
# 1. Setup lần đầu — tải banned list + danh sách commander (~3–5 phút)
python cli.py update --banned-list --commanders

# 2. Import collection
python cli.py import --csv my_collection.csv

# 3. Xem gợi ý deck
python cli.py build --output ranked --top 5
```

---

## Hướng dẫn sử dụng

### Import collection

```bash
# Từ CSV export của Archidekt  (Archidekt → Collection → Export)
python cli.py import --csv my_collection.csv

# Từ Archidekt API (cần ARCHIDEKT_API_KEY trong .env)
python cli.py import --api

# Xem thống kê collection
python cli.py collection --stats
python cli.py collection --list
```

### Build deck

```bash
# Top 5 deck gợi ý từ collection
python cli.py build --output ranked --top 5

# Bao gồm commander chưa có (maximize tái dùng card)
python cli.py build --output ranked --include-unowned-commanders --top 10

# Build commander cụ thể
python cli.py build --commander "Atraxa, Praetors' Voice" --output ranked

# Xuất decklist (Moxfield / Archidekt)
python cli.py build --commander "Atraxa, Praetors' Voice" --output decklist
python cli.py build --commander "Atraxa, Praetors' Voice" --output decklist --save atraxa.txt

# Gợi ý swap card từ collection
python cli.py build --commander "Atraxa, Praetors' Voice" --output swap

# Buylist — card còn thiếu kèm giá
python cli.py build --commander "Atraxa, Praetors' Voice" --output buylist
python cli.py build --commander "Atraxa, Praetors' Voice" --output buylist --save buylist.csv

# Partner commanders
python cli.py build --commander "Tymna the Weaver" --partner "Thrasios, Triton Hero" --output ranked
```

### Cập nhật dữ liệu

```bash
python cli.py update --banned-list          # cập nhật banned list từ Scryfall
python cli.py update --commanders           # cập nhật danh sách commander
python cli.py update --clear-edhrec-cache   # xóa cache EDHREC (force re-fetch)
python cli.py update --refresh-prices       # cập nhật giá tất cả card
python cli.py update --db-stats             # xem thống kê database
```

---

## Cấu trúc project

```
MTGdeckbuilder/
├── cli.py                        # Entry point — Click CLI
├── importers/
│   ├── archidekt_csv.py          # Parser CSV Archidekt
│   └── archidekt_api.py          # Archidekt REST API client
├── enrichers/
│   ├── scryfall.py               # Oracle data + giá, TTL 2 tầng
│   └── edhrec.py                 # Synergy scores qua json.edhrec.com
├── engine/
│   ├── commander_picker.py       # Pre-filter màu + chấm điểm commander
│   ├── deck_builder.py           # Greedy deck builder theo slot
│   ├── mana_pip.py               # Pip analysis + phân bổ Largest Remainder
│   ├── mana_curve.py             # Phân tích curve so với target archetype
│   ├── archetype.py              # Phát hiện archetype qua signal scoring
│   ├── synergy_chain.py          # Phát hiện pairs + theme density
│   └── scorer.py                 # Composite scorer 5 thành phần
├── filters/
│   └── banned_list.py            # Banned list + color identity enforcement
├── outputs/
│   ├── ranked.py                 # Output ranked với archetype / curve / chains
│   ├── decklist.py               # Export text format Moxfield
│   ├── swap.py                   # Gợi ý upgrade từ collection
│   └── buylist.py                # Card thiếu theo priority + giá
├── db/
│   └── cache.py                  # SQLite wrapper có schema migrations
└── data/
    └── slots.json                # Slot targets + keyword rules
```

---

## Hệ thống chấm điểm

M��i deck được chấm điểm tổng hợp từ 5 thành phần:

| Thành phần | Trọng số | Mô tả |
|---|---|---|
| EDHREC synergy | 40% | Trung bình synergy score của 99 cards với commander |
| Collection coverage | 20% | % card đang sở hữu (không cần mua thêm) |
| Mana curve | 15% | Chất lượng curve so với target archetype |
| Synergy chains | 15% | Cặp card có tương tác + mật độ theme |
| Slot balance | 10% | Tỉ lệ slot có khớp archetype |

**Thang điểm:** A ≥ 80% · B ≥ 65% · C ≥ 50% · D < 50%

### Mana pip analysis

Phân bổ basic land bằng cách đếm mana symbol màu từ 99 non-land cards:

| Loại symbol | Trọng số |
|---|---|
| `{W}` `{U}` `{B}` `{R}` `{G}` | 1.0 mỗi pip |
| `{W/U}` `{G/R}` (hybrid) | 0.5 mỗi màu |
| `{W/P}` `{B/P}` (Phyrexian) | 0.5 (thường trả life) |
| `{2/W}` `{2/U}` (generic hybrid) | 0.5 (thường trả generic) |
| `{X}` `{C}` `{1}` (colorless) | 0.0 — bỏ qua |

Phân bổ dùng **Largest Remainder Method** để đảm bảo tổng chính xác, tối thiểu 1 basic mỗi màu.

**Ví dụ — Atraxa WUBG, cần 20 basics:**

| Phương pháp | Plains | Island | Swamp | Forest |
|---|---|---|---|---|
| Cũ (chia đều) | 5 | 5 | 5 | 5 |
| Mới (theo pip) | 6 | 7 | 5 | 2 |

Pip thực tế của deck: U 36% · W 27% · B 27% · G 9%

---

## Nguồn dữ liệu

| Nguồn | Dữ liệu | Cache TTL |
|---|---|---|
| [Scryfall](https://scryfall.com/docs/api) | Oracle text, type, color identity, legality | 30 ngày |
| [Scryfall](https://scryfall.com/docs/api) | Giá card (USD / EUR) | 7 ngày |
| [EDHREC](https://edhrec.com) | Synergy scores, inclusion rates, slot tags | 7 ngày |
| [Archidekt](https://archidekt.com) | Collection của user | Khi import |

Tất cả dữ liệu được cache trong SQLite local (`data/edh_builder.db`). Schema migrations chạy tự động khi khởi động.

---

## Biến môi trường

| Biến | Mặc định | Mô tả |
|---|---|---|
| `ARCHIDEKT_API_KEY` | — | Archidekt API key (tùy chọn) |
| `ARCHIDEKT_USERNAME` | — | Archidekt username (tùy chọn) |
| `SCRYFALL_CACHE_TTL_DAYS` | 30 | Thời gian cache oracle data |
| `SCRYFALL_PRICE_TTL_DAYS` | 7 | Thời gian cache giá |
| `EDHREC_CACHE_TTL_DAYS` | 7 | Thời gian cache EDHREC |
