"""
importers/archidekt_api.py — Lấy collection trực tiếp từ Archidekt REST API.

Docs: https://archidekt.com/api/swagger/
Endpoint: GET /api/collection/v2/
Auth: Bearer token (API key từ Archidekt account settings)

Rate limit: Archidekt không publish giới hạn cụ thể.
Chúng ta dùng page size 100 và sleep 0.5s giữa các page.
"""

import time
import os
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://archidekt.com/api"
PAGE_SIZE = 100
SLEEP_BETWEEN_PAGES = 0.5  # seconds


def _get_headers() -> dict:
    api_key = os.getenv("ARCHIDEKT_API_KEY", "").strip()
    if not api_key:
        raise EnvironmentError(
            "ARCHIDEKT_API_KEY chưa được set trong .env\n"
            "Lấy API key tại: Archidekt → Settings → API Keys"
        )
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }


def fetch_collection() -> list[dict]:
    """
    Fetch toàn bộ collection từ Archidekt API.

    Returns:
        list of dicts: [{name, quantity, set_code, foil, condition}, ...]
    """
    headers = _get_headers()
    cards = []
    page = 1
    total_pages = None

    print("Đang tải collection từ Archidekt API...")

    while True:
        url = f"{BASE_URL}/collection/v2/"
        params = {
            "page": page,
            "pageSize": PAGE_SIZE,
            "ordering": "card__oracleCard__name",
        }

        resp = requests.get(url, headers=headers, params=params, timeout=30)

        if resp.status_code == 401:
            raise PermissionError(
                "API key không hợp lệ hoặc đã hết hạn. "
                "Kiểm tra ARCHIDEKT_API_KEY trong .env"
            )
        if resp.status_code == 429:
            print("  Rate limited, chờ 10 giây...")
            time.sleep(10)
            continue

        resp.raise_for_status()
        data = resp.json()

        if total_pages is None:
            total_count = data.get("count", 0)
            total_pages = (total_count + PAGE_SIZE - 1) // PAGE_SIZE
            print(f"  Tổng {total_count} card, {total_pages} trang")

        for item in data.get("results", []):
            card_data = item.get("card", {})
            oracle = card_data.get("oracleCard", {})
            edition = card_data.get("edition", {})

            name = oracle.get("name", "").strip()
            if not name:
                continue

            cards.append({
                "name": name,
                "quantity": item.get("qty", 1),
                "set_code": edition.get("editioncode", "").upper() or None,
                "foil": int(item.get("foil", False)),
                "condition": _normalize_condition(item.get("condition", "")),
            })

        print(f"  Trang {page}/{total_pages} — {len(cards)} cards")

        if page >= (total_pages or 1):
            break

        page += 1
        time.sleep(SLEEP_BETWEEN_PAGES)

    print(f"Hoàn thành: {len(cards)} cards.")
    return cards


def _normalize_condition(raw: str) -> str:
    mapping = {
        "m": "NM", "nm": "NM",
        "lp": "LP",
        "mp": "MP",
        "hp": "HP",
        "d": "D",
    }
    return mapping.get(raw.lower().strip(), raw or "NM")
