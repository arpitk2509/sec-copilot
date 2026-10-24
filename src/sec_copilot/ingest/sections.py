"""Cut the Risk Factors (Item 1A) and MD&A (Item 7) sections out of a 10-K's HTML."""
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

from sec_copilot.ingest.edgar import resolve_tickers
from sec_copilot.ingest.filings import download_filing, list_10k_filings

# Where each section starts, and the headings that can end it (whichever comes first).
SEPARATOR = r"[\.\s:\-–—]*"
SECTION_RULES = {
    "Item 1A": {
        "start": rf"item\s*1a{SEPARATOR}risk\s*factors",
        "end": [r"item\s*1b\b", r"item\s*1c\b", r"item\s*2\b"],
    },
    "Item 7": {
        "start": rf"item\s*7{SEPARATOR}management",
        "end": [r"item\s*7a\b", r"item\s*8\b"],
    },
}

# Shorter than this is a table-of-contents entry or a "see the annual report" pointer, not a real section.
MIN_SECTION_CHARS = 3000

BLOCK_TAGS = ["p", "div", "tr", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "table"]
CELL_TAGS = ["td", "th"]


def html_to_text(html: str) -> str:
    """Turn filing HTML into plain text where every paragraph, heading, or table row is its own line."""
    html = re.sub(r"^\s*<\?xml[^>]*\?>", "", html)  # inline-XBRL files start with an XML declaration
    html = re.sub(r"\s+", " ", html)  # line breaks in the HTML source are just spaces
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    for tag in soup.find_all("ix:header"):  # hidden XBRL data at the top of the file
        tag.decompose()
    for tag in soup.find_all(BLOCK_TAGS):
        # Inside a table cell, paragraphs must NOT start new lines, or every number lands on its own line.
        # Only the end of a table row (or a table) breaks the line.
        inside_cell = tag.name not in ("tr", "table") and tag.find_parent(CELL_TAGS) is not None
        tag.append(" " if inside_cell else "\n")
    for tag in soup.find_all(CELL_TAGS):
        tag.append(" ")

    text = soup.get_text()
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return text.strip()


def _heading_positions(text: str, pattern: str) -> list[int]:
    """Where a heading begins a line. Cross-references inside sentences don't count."""
    return [m.start() for m in re.finditer(r"(?im)^[ \t]*" + pattern, text)]


def extract_section(text: str, item: str) -> str | None:
    """Return one section's text, or None if it can't be found.

    The table of contents and the real heading both match the start pattern. For each start we find
    the first heading that ends the section. If several starts share the same end, only the one
    closest to it counts, so a stray cross-reference earlier in the document can't stretch the section.
    The longest remaining candidate wins: the table of contents gives a short one, the real section a long one.
    """
    rule = SECTION_RULES[item]
    starts = _heading_positions(text, rule["start"])
    ends = sorted(pos for pattern in rule["end"] for pos in _heading_positions(text, pattern))

    nearest_start = {}  # end position -> the closest start before it
    for start in starts:
        end = next((e for e in ends if e > start), None)
        if end is not None:
            nearest_start[end] = max(start, nearest_start.get(end, -1))

    best = max((text[s:e].strip() for e, s in nearest_start.items()), key=len, default="")
    return best if len(best) >= MIN_SECTION_CHARS else None


def main(ticker: str) -> None:
    companies = resolve_tickers([ticker])
    if not companies:
        raise SystemExit(f"Unknown ticker: {ticker}")
    company = companies[0]

    filing = list_10k_filings(company["cik"])[0]
    print(f"{company['ticker']}: 10-K filed {filing['filing_date']}, fiscal period ending {filing['period_end']}")
    text = html_to_text(download_filing(filing))
    print(f"Whole document: {len(text):,} characters")

    out_dir = Path("data/processed")
    out_dir.mkdir(parents=True, exist_ok=True)
    for item in SECTION_RULES:
        section = extract_section(text, item)
        if section is None:
            print(f"\n{item}: NOT FOUND")
            continue
        path = out_dir / f"{company['ticker']}_{filing['period_end'][:4]}_{item.replace(' ', '').lower()}.txt"
        path.write_text(section, encoding="utf-8")
        print(f"\n{item}: {len(section):,} characters, saved to {path}")
        print("  starts:", section[:200].replace("\n", " "))
        print("  ends:  ", section[-200:].replace("\n", " "))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "AAPL")