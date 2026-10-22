"""Download company facts from SEC EDGAR."""
import json
import time
from pathlib import Path

import httpx

from sec_copilot.config import require_user_agent

BASE_URL = "https://data.sec.gov/api/xbrl/companyfacts"
TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
RAW_DIR = Path("data/raw")


def fetch_company_facts(cik: int, refresh: bool = False) -> dict:
    """Get all XBRL facts for one company.

    The result is saved to data/raw/, so later calls read from disk
    instead of hitting the SEC again.
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = RAW_DIR / f"companyfacts_{cik:010d}.json"

    if cache_file.exists() and not refresh:
        return json.loads(cache_file.read_text())

    url = f"{BASE_URL}/CIK{cik:010d}.json"       # the SEC wants the CIK padded to 10 digits
    headers = {"User-Agent": require_user_agent()}
    response = httpx.get(url, headers=headers, timeout=30)
    response.raise_for_status()                  # stops with an error on 403, 404, etc.

    cache_file.write_text(response.text)
    time.sleep(0.2)                              # stay well under 10 requests/second
    return response.json()


def resolve_tickers(tickers: list[str]) -> list[dict]:
    """Look up each ticker's CIK and official name in the SEC's ticker file."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = RAW_DIR / "company_tickers.json"

    if cache_file.exists():
        data = json.loads(cache_file.read_text())
    else:
        response = httpx.get(TICKER_URL, headers={"User-Agent": require_user_agent()}, timeout=30)
        response.raise_for_status()
        cache_file.write_text(response.text)
        data = response.json()
        time.sleep(0.2)

    by_ticker = {row["ticker"].upper(): row for row in data.values()}
    companies = []
    for ticker in tickers:
        row = by_ticker.get(ticker.upper())
        if row is None:
            print(f"WARNING: ticker {ticker} not found in the SEC ticker file, skipping")
            continue
        companies.append({
            "cik": int(row["cik_str"]),
            "ticker": ticker.upper(),
            "name": row["title"],
            "sector": "Technology",
        })
    return companies