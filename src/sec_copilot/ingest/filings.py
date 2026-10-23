"""Find and download 10-K filings from SEC EDGAR."""
import json
import time
from pathlib import Path

import httpx

from sec_copilot.config import require_user_agent

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
RAW_DIR = Path("data/raw")
FILINGS_DIR = RAW_DIR / "filings"


def _get(url: str) -> httpx.Response:
    """Download a URL politely: with our identity, and slowly enough for the SEC's rate limit."""
    response = httpx.get(
        url, headers={"User-Agent": require_user_agent()}, timeout=60, follow_redirects=True
    )
    response.raise_for_status()
    time.sleep(0.2)
    return response


def list_10k_filings(cik: int, refresh: bool = False) -> list[dict]:
    """The company's recent 10-K filings, newest first."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = RAW_DIR / f"submissions_{cik:010d}.json"

    if cache_file.exists() and not refresh:
        data = json.loads(cache_file.read_text())
    else:
        response = _get(SUBMISSIONS_URL.format(cik=cik))
        cache_file.write_text(response.text)
        data = response.json()

    recent = data["filings"]["recent"]
    filings = []
    for form, accession, filed, period, document in zip(
        recent["form"], recent["accessionNumber"], recent["filingDate"],
        recent["reportDate"], recent["primaryDocument"],
    ):
        if form == "10-K":
            filings.append({
                "cik": cik,
                "accession_no": accession,
                "filing_date": filed,
                "period_end": period,
                "document": document,
            })
    return sorted(filings, key=lambda f: f["filing_date"], reverse=True)


def download_filing(filing: dict) -> str:
    """The filing's main HTML document, cached in data/raw/filings/."""
    FILINGS_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = FILINGS_DIR / f"{filing['accession_no']}.htm"
    if cache_file.exists():
        return cache_file.read_text(encoding="utf-8", errors="ignore")

    url = ARCHIVE_URL.format(
        cik=filing["cik"],
        accession=filing["accession_no"].replace("-", ""),
        document=filing["document"],
    )
    response = _get(url)
    cache_file.write_text(response.text, encoding="utf-8")
    return response.text