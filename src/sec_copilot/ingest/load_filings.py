"""Download each company's latest 10-K, cut out the sections, chunk them, and store them in Postgres."""
from datetime import date

import pandas as pd
from sqlalchemy import create_engine, text

from sec_copilot.config import DATABASE_URL
from sec_copilot.ingest.chunking import build_chunks, find_page_furniture
from sec_copilot.ingest.edgar import resolve_tickers
from sec_copilot.ingest.filings import ARCHIVE_URL, download_filing, list_10k_filings
from sec_copilot.ingest.load_facts import TICKERS, UPSERT_COMPANY
from sec_copilot.ingest.sections import SECTION_RULES, extract_section, html_to_text
from sec_copilot.ingest.transform import fiscal_year_of

FILINGS_PER_COMPANY = 1   # raise this later to load older years too

UPSERT_FILING = text("""
    INSERT INTO filings (accession_no, cik, form_type, filing_date, period_end, source_url)
    VALUES (:accession_no, :cik, '10-K', :filing_date, :period_end, :source_url)
    ON CONFLICT (accession_no) DO UPDATE
    SET filing_date = EXCLUDED.filing_date, period_end = EXCLUDED.period_end, source_url = EXCLUDED.source_url
""")

UPSERT_CHUNK = text("""
    INSERT INTO doc_chunks (accession_no, cik, section, fiscal_year, chunk_index, content)
    VALUES (:accession_no, :cik, :section, :fiscal_year, :chunk_index, :content)
    ON CONFLICT (accession_no, section, chunk_index) DO UPDATE
    SET fiscal_year = EXCLUDED.fiscal_year, content = EXCLUDED.content
""")

# If a section now has fewer chunks than last time, remove the leftover ones at the end.
DELETE_LEFTOVER_CHUNKS = text("""
    DELETE FROM doc_chunks
    WHERE accession_no = :accession_no AND section = :section AND chunk_index >= :count
""")


def load_filing(engine, company: dict, filing: dict) -> dict[str, int | None]:
    """Process one 10-K. Returns {section: chunk count, or None if the section wasn't found}."""
    text_of_filing = html_to_text(download_filing(filing))
    furniture = find_page_furniture(text_of_filing)
    sections = {item: extract_section(text_of_filing, item) for item in SECTION_RULES}
    found = {item: body for item, body in sections.items() if body}

    counts: dict[str, int | None] = {item: None for item in SECTION_RULES}
    if not found:
        return counts

    chunks = build_chunks(company, filing, found, furniture)
    source_url = ARCHIVE_URL.format(
        cik=filing["cik"], accession=filing["accession_no"].replace("-", ""), document=filing["document"]
    )
    with engine.begin() as conn:  # all of this filing's rows load, or none do
        conn.execute(UPSERT_COMPANY, company)
        conn.execute(UPSERT_FILING, {
            "accession_no": filing["accession_no"],
            "cik": company["cik"],
            "filing_date": date.fromisoformat(filing["filing_date"]),
            "period_end": date.fromisoformat(filing["period_end"]) if filing["period_end"] else None,
            "source_url": source_url,
        })
        for item in found:
            item_chunks = [c for c in chunks if c["section"] == item]
            counts[item] = len(item_chunks)
            conn.execute(DELETE_LEFTOVER_CHUNKS, {
                "accession_no": filing["accession_no"], "section": item, "count": len(item_chunks),
            })
            conn.execute(UPSERT_CHUNK, [
                {k: c[k] for k in ("accession_no", "cik", "section", "fiscal_year", "chunk_index", "content")}
                for c in item_chunks
            ])
    return counts


def main() -> None:
    engine = create_engine(DATABASE_URL)
    report = []
    for company in resolve_tickers(TICKERS):
        try:
            filings = list_10k_filings(company["cik"])[:FILINGS_PER_COMPANY]
        except Exception as error:  # keep going: one bad company shouldn't stop the rest
            report.append((company["ticker"], "-", f"FAILED to list filings ({error})"))
            continue
        if not filings:
            report.append((company["ticker"], "-", "no 10-K in the recent filings list"))
            continue
        for filing in filings:
            label = f"FY{fiscal_year_of(pd.Timestamp(filing['period_end']))} filed {filing['filing_date']}"
            try:
                counts = load_filing(engine, company, filing)
            except Exception as error:
                report.append((company["ticker"], label, f"FAILED ({error})"))
                continue
            summary = " | ".join(
                f"{item}: {n} chunks" if n is not None else f"{item}: NOT FOUND" for item, n in counts.items()
            )
            report.append((company["ticker"], label, summary))
            print(f"{company['ticker']:<5} {label}  {summary}")

    print("\nSummary")
    for ticker, label, summary in report:
        print(f"  {ticker:<5} {label:<28} {summary}")


if __name__ == "__main__":
    main()