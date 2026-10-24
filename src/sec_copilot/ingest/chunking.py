"""Split filing sections into overlapping, paragraph-aligned chunks with metadata."""
import re
import sys
from collections import Counter

import pandas as pd

from sec_copilot.ingest.edgar import resolve_tickers
from sec_copilot.ingest.filings import download_filing, list_10k_filings
from sec_copilot.ingest.sections import SECTION_RULES, extract_section, html_to_text
from sec_copilot.ingest.transform import fiscal_year_of

MAX_CHARS = 1500      # about 350 tokens
OVERLAP_CHARS = 200   # the end of one chunk is repeated at the start of the next

# Page headers and footers repeat on every page. We only delete short lines that repeat
# and look like page furniture, so real content (and repeated table rows) stays.
REPEAT_THRESHOLD = 3
REPEATED_HEADER_THRESHOLD = 8   # a digit-free short line repeated this often is a running header, not content
MAX_FURNITURE_LENGTH = 100


def _normalize(line: str) -> str:
    """Lowercase and replace every number with #, so 'Form 10-K | 9' and 'Form 10-K | 10' match."""
    return re.sub(r"\d+", "#", line.strip().lower())


def _looks_like_furniture(line: str, normalized: str) -> bool:
    lowered = line.lower()
    return (
        "form 10-k" in lowered
        or ("10-k" in lowered and "|" in lowered)
        or ("annual report" in lowered and "#" in normalized)   # e.g. "2025 Annual Report 23"
        or normalized == "table of contents"
        or re.fullmatch(r"[\s\-–—|]*(page\s*)?#(\s*of\s*#)?[\s\-–—|]*", normalized) is not None
    )


def _is_running_header(normalized: str, count: int) -> bool:
    """A short line with no numbers repeated many times: 'PART II', 'CISCO SYSTEMS, INC.', '... (Continued)'."""
    return count >= REPEATED_HEADER_THRESHOLD and "#" not in normalized


def find_page_furniture(text: str) -> set[str]:
    """Find page headers/footers in the WHOLE document: short, repeated lines that look like furniture."""
    lines = [line for line in text.splitlines() if line.strip() and len(line) <= MAX_FURNITURE_LENGTH]
    counts = Counter(_normalize(line) for line in lines)
    return {
        _normalize(line)
        for line in lines
        if (counts[_normalize(line)] >= REPEAT_THRESHOLD and _looks_like_furniture(line, _normalize(line)))
        or _is_running_header(_normalize(line), counts[_normalize(line)])
    }


def strip_page_furniture(text: str, furniture: set[str]) -> str:
    """Remove the lines found by find_page_furniture."""
    return "\n".join(line for line in text.splitlines() if _normalize(line) not in furniture)


def _split_long_paragraph(paragraph: str, max_chars: int) -> list[str]:
    """Split an overly long paragraph at sentence ends."""
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z“\"(])", paragraph)
    pieces, current = [], ""
    for sentence in sentences:
        if current and len(current) + 1 + len(sentence) > max_chars:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    # A single sentence longer than the limit: cut it hard rather than keep a giant chunk.
    return [p[i:i + max_chars] for p in pieces for i in range(0, len(p), max_chars)]


def chunk_text(text: str, max_chars: int = MAX_CHARS, overlap_chars: int = OVERLAP_CHARS) -> list[str]:
    """Group paragraphs into chunks of up to max_chars, repeating a short tail for context."""
    paragraphs = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        paragraphs.extend(_split_long_paragraph(line, max_chars) if len(line) > max_chars else [line])

    chunks, current, size = [], [], 0
    for paragraph in paragraphs:
        if current and size + len(paragraph) + 1 > max_chars:
            chunks.append("\n".join(current))
            tail, tail_size = [], 0                      # carry the last short paragraphs forward
            for previous in reversed(current):
                if tail_size + len(previous) + 1 > overlap_chars:
                    break
                tail.insert(0, previous)
                tail_size += len(previous) + 1
            if tail_size + len(paragraph) + 1 > max_chars:
                tail, tail_size = [], 0
            current, size = tail, tail_size
        current.append(paragraph)
        size += len(paragraph) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def build_chunks(
    company: dict, filing: dict, sections: dict[str, str], furniture: frozenset | set = frozenset()
) -> list[dict]:
    """One record per chunk, with the metadata the database needs."""
    fiscal_year = fiscal_year_of(pd.Timestamp(filing["period_end"]))
    records = []
    for section_name, text in sections.items():
        cleaned = strip_page_furniture(text, furniture)
        for index, content in enumerate(chunk_text(cleaned)):
            records.append({
                "ticker": company["ticker"],
                "cik": company["cik"],
                "accession_no": filing["accession_no"],
                "fiscal_year": fiscal_year,
                "section": section_name,
                "chunk_index": index,
                "content": content,
            })
    return records


def main(ticker: str) -> None:
    companies = resolve_tickers([ticker])
    if not companies:
        raise SystemExit(f"Unknown ticker: {ticker}")
    company = companies[0]
    filing = list_10k_filings(company["cik"])[0]
    text = html_to_text(download_filing(filing))
    sections = {item: s for item in SECTION_RULES if (s := extract_section(text, item))}

    # Diagnostic: the most repeated short lines in the whole document. Page footers show up here.
    furniture = find_page_furniture(text)
    lines = [line for line in text.splitlines() if 0 < len(line.strip()) <= MAX_FURNITURE_LENGTH]
    examples = {}
    for line in lines:
        examples.setdefault(_normalize(line), line)
    print(f"Page furniture patterns found: {len(furniture)}")
    print("Most repeated short lines in the document:")
    for norm, count in Counter(_normalize(line) for line in lines).most_common(6):
        status = "removed" if norm in furniture else "kept"
        print(f"  {count:>3} x [{status}] {examples[norm][:80]!r}")

    chunks = build_chunks(company, filing, sections, furniture)
    print(f"\n{len(chunks)} chunks for {company['ticker']} fiscal {chunks[0]['fiscal_year']}")
    for name, section_text in sections.items():
        before = len(section_text.splitlines())
        after = len(strip_page_furniture(section_text, furniture).splitlines())
        sizes = [len(c["content"]) for c in chunks if c["section"] == name]
        print(f"  {name}: {before - after} furniture lines removed, {len(sizes)} chunks, "
              f"characters min/avg/max = {min(sizes)}/{sum(sizes) // len(sizes)}/{max(sizes)}")

    for sample in (chunks[len(chunks) // 3], chunks[-1]):
        print(f"\n--- {sample['section']} chunk {sample['chunk_index']} ({len(sample['content'])} chars) ---")
        print(sample["content"][:600])


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "AAPL")