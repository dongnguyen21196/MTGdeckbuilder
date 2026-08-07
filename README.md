# EDH Deck Builder

**Web app dựng deck Commander (EDH) tối ưu từ bộ sưu tập card Archidekt của bạn.**

Upload CSV collection → app chấm điểm commander theo tiềm năng synergy → trả về decklist 99 card, gợi ý swap, và buylist kèm giá. Dữ liệu từ Scryfall và EDHREC.

Chạy trên Vercel: Next.js frontend + FastAPI backend + Neon Postgres.

---

## Kiến trúc

```
├── web/                    # Next.js (App Router, Tailwind) — service "web"
├── api/                    # FastAPI — service "api"
│   ├── index.py            #   entrypoint Vercel nạp
│   ├── routes.py           #   lớp HTTP
│   ├── serializers.py      #   dataclass engine → JSON
│   ├── scripts/seed.py     #   seeder, chạy trên GitHub Actions
│   └── lib/                #   toàn bộ logic nghiệp vụ
│       ├── engine/         #     chấm điểm, build deck, phân tích
│       ├── enrichers/      #     Scryfall + EDHREC
│       ├── filters/        #     banned list, color identity
│       ├── importers/      #     parser CSV Archidekt
│       ├── outputs/        #     decklist text, swap, buylist
│       └── db/cache.py     #     Postgres wrapper
├── vercel.json             # định nghĩa 2 service + routing
└── .github/workflows/      # seed.yml — nightly
```

Hai service dùng chung một domain. `vercel.json` route `/api/*` sang service Python, còn lại sang Next.js.

### Vì sao seeder chạy trên GitHub Actions

Dữ liệu Scryfall/EDHREC/banned-list là **dữ liệu toàn cục**, không phụ thuộc người dùng. Seed sẵn vào Postgres thì request lúc chạy chỉ còn đọc DB + tính toán CPU, vừa khít giới hạn 60s của Vercel Function.

Bản thân việc seed thì không vừa: ~1.800 commander × 1s delay ≈ 30 phút. Vercel Hobby chỉ cho cron 1 lần/ngày và Function trần 60s, nên job này chạy trên GitHub Actions (runner cho 6 giờ). Phụ thêm: EDHREC bị gọi từ IP GitHub thay vì IP Vercel.

---

## Setup

### 1. Tạo database

Trong Vercel Dashboard → project → **Storage** → **Create Database** → **Neon Postgres**. Vercel tự inject `DATABASE_URL` vào biến môi trường của project.

### 2. Seed dữ liệu lần đầu

Thêm `DATABASE_URL` vào GitHub repo secrets (Settings → Secrets and variables → Actions), rồi chạy workflow **Seed dữ liệu** bằng tay (`workflow_dispatch`). Lần đầu mất khoảng 45–60 phút.

Kiểm tra bằng `GET /api/health` — trường `ready` chuyển thành `true` khi seed xong.

### 3. Deploy

Push lên `main`. Vercel tự build cả hai service.

---

## Chạy local

```bash
# Backend
cd api
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt uvicorn
DATABASE_URL="postgresql://..." .venv/bin/python -m uvicorn index:app --port 8000 --reload
```

```bash
# Frontend (terminal khác) — next.config.ts proxy /api/* sang :8000 khi dev
cd web && npm install && npm run dev
```

Seed thủ công:

```bash
cd api && DATABASE_URL="postgresql://..." .venv/bin/python scripts/seed.py --all
```

---

## API

| Method | Route | Việc |
|---|---|---|
| `GET` | `/api/health` | Trạng thái DB + độ tươi của seed |
| `POST` | `/api/collection/import` | Upload CSV Archidekt (multipart) |
| `GET` | `/api/collection` | Collection của session hiện tại |
| `DELETE` | `/api/collection` | Xoá collection |
| `GET` | `/api/commanders/rank?top=5&ownedOnly=true` | Chấm điểm + build top N deck |
| `POST` | `/api/decks/build` | Build một deck: chi tiết + decklist + swap + buylist |

OpenAPI docs: `/api/docs`.

Collection lưu theo session ẩn danh (cookie `mtg_sid`, httpOnly, 30 ngày). Không cần đăng nhập; seeder dọn session cũ hằng đêm.

---

## Tính năng

- **Import collection** — CSV export từ Archidekt
- **Làm giàu dữ liệu card** — Scryfall (oracle text, legality, giá) + EDHREC (synergy scores, tỉ lệ inclusion)
- **Commander picker thông minh** — pre-filter theo color identity, thu hẹp 1.800+ commanders còn 50–200 candidates trước khi chấm điểm
- **Hỗ trợ partner commanders** — detect partner ability, merge color identity, tạo slug EDHREC đúng format
- **Deck builder theo slot** — fill 7 slots: land, ramp, draw, removal, wipe, tutor, synergy
- **Mana pip analysis** — đếm pip `{W}{U}{B}{R}{G}` từ 99 cards (kể cả hybrid & Phyrexian), phân bổ basic land theo tỉ lệ pip thực tế
- **Phát hiện archetype tự động** — combo / control / stax / aggro / midrange, hỗ trợ hybrid
- **Mana curve scoring** — đánh giá curve so với target của từng archetype
- **Phát hiện synergy chains** — ETB loops, aristocrats, wheels, storm, voltron… và mật độ theme
- **Reprint deduplication** — check ownership theo oracle name, mọi printing đều tính là owned
- **Auto-filter banned list** — sync từ Scryfall
- **4 output** — ranked suggestions, decklist Moxfield-ready, gợi ý swap, buylist có giá

---

## Hệ thống chấm điểm

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
| Chia đều | 5 | 5 | 5 | 5 |
| Theo pip | 6 | 7 | 5 | 2 |

Pip thực tế: U 36% · W 27% · B 27% · G 9%

---

## Nguồn dữ liệu

| Nguồn | Dữ liệu | Cache TTL |
|---|---|---|
| [Scryfall](https://scryfall.com/docs/api) | Oracle text, type, color identity, legality | 30 ngày |
| [Scryfall](https://scryfall.com/docs/api) | Giá card (USD / EUR) | 7 ngày |
| [EDHREC](https://edhrec.com) | Synergy scores, inclusion rates, slot tags | 7 ngày |
| [Archidekt](https://archidekt.com) | Collection của user | Khi upload |

## Biến môi trường

| Biến | Mặc định | Mô tả |
|---|---|---|
| `DATABASE_URL` | — | **Bắt buộc.** Connection string Neon Postgres |
| `SCRYFALL_CACHE_TTL_DAYS` | 30 | Thời gian cache oracle data |
| `SCRYFALL_PRICE_TTL_DAYS` | 7 | Thời gian cache giá |
| `EDHREC_CACHE_TTL_DAYS` | 7 | Thời gian cache EDHREC |
| `SESSION_RETENTION_DAYS` | 30 | Giữ collection của session bao lâu |
| `DB_POOL_MAX` | 5 | Số connection tối đa mỗi instance |
