# EDH Deck Builder

Build optimized Commander (EDH) decks from your Archidekt collection.

## Features

- Import collection từ Archidekt CSV export hoặc Archidekt API
- Enrichment data từ Scryfall (card text, legality) và EDHREC (synergy scores)
- Cache local SQLite — không gọi API lại cho card đã biết
- Gợi ý commander từ collection hoặc toàn bộ commander hợp lệ
- Greedy deck builder theo slot (ramp, draw, removal, wipe, tutor, combo, land)
- Auto-filter banned list EDH (sync từ Scryfall)
- 4 output modes: ranked decks, decklist text, card swaps, buylist

## Cài đặt

```bash
pip install -r requirements.txt
```

## Cấu hình

Copy `.env.example` thành `.env` và điền:

```
ARCHIDEKT_API_KEY=your_key_here   # optional, dùng CSV nếu không có
ARCHIDEKT_USERNAME=your_username  # dùng khi fetch qua API
```

## Sử dụng

### Import collection

```bash
# Từ CSV export (Archidekt → Collection → Export)
python cli.py import --csv my_collection.csv

# Từ Archidekt API
python cli.py import --api

# Xem collection đã import
python cli.py collection --list
```

### Build deck

```bash
# Gợi ý top 5 deck từ collection, output ranked
python cli.py build --output ranked --top 5

# Build deck với commander cụ thể
python cli.py build --commander "Atraxa, Praetors' Voice" --output decklist

# Gợi ý swap cho deck hiện tại (so với collection)
python cli.py build --commander "Atraxa, Praetors' Voice" --output swap

# Buylist — card còn thiếu để hoàn thiện deck tốt nhất
python cli.py build --commander "Atraxa, Praetors' Voice" --output buylist

# Bao gồm commander chưa có trong collection (maximize reuse)
python cli.py build --output ranked --include-unowned-commanders --top 10
```

### Update data

```bash
# Update banned list từ Scryfall
python cli.py update --banned-list

# Xóa cache EDHREC cũ (> 7 ngày)
python cli.py update --clear-edhrec-cache
```

## Cấu trúc project

```
edh-deck-builder/
├── cli.py                    # Entry point, argparse
├── importers/
│   ├── archidekt_csv.py      # Parse CSV export từ Archidekt
│   └── archidekt_api.py      # Gọi Archidekt REST API
├── enrichers/
│   ├── scryfall.py           # Card data, legality, price
│   └── edhrec.py             # Synergy scores, inclusion rates
├── engine/
│   ├── commander_picker.py   # Score và rank commanders
│   ├── deck_builder.py       # Greedy slot-based deck builder
│   └── scorer.py             # Composite deck scoring
├── filters/
│   └── banned_list.py        # EDH banned list, auto-sync
├── outputs/
│   ├── ranked.py             # Top N decks với giải thích
│   ├── decklist.py           # Moxfield-compatible text export
│   ├── swap.py               # Upgrade suggestions từ collection
│   └── buylist.py            # Missing cards + price
├── db/
│   └── cache.py              # SQLite wrapper
└── data/
    └── slots.json            # Card type → slot mapping rules
```

## Giải thích scoring

Mỗi deck được chấm điểm tổng hợp:
- **Synergy score** (50%): trung bình EDHREC synergy của 99 card với commander
- **Collection coverage** (30%): % card có trong collection (không cần mua)
- **Slot balance** (20%): mana curve và tỉ lệ slot có đúng theo chuẩn

Commander mode "include-unowned" tính thêm bonus collection reuse để tránh suggest commander cần mua quá nhiều card mới.
