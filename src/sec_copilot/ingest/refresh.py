"""Bring the database up to date: new quarterly numbers, and a new 10-K when a company files one.

Run it with:  uv run python -m sec_copilot.ingest.refresh
It re-downloads from SEC EDGAR (ignoring the local cache) and reuses the normal loaders, which are safe to repeat.
"""
import argparse
from dataclasses import dataclass, field

from sqlalchemy import create_engine, text

LATEST_QUARTERS = text("""
    SELECT c.ticker, MAX(f.period_end) AS latest
    FROM financial_facts f
    JOIN companies c ON c.cik = f.cik
    WHERE f.fiscal_period <> 'FY'
    GROUP BY c.ticker
""")
LOADED_FILINGS = text("SELECT DISTINCT accession_no FROM doc_chunks WHERE cik = :cik")
DELETE_OLDER_CHUNKS = text("DELETE FROM doc_chunks WHERE cik = :cik AND accession_no <> :keep")


@dataclass
class FactsResult:
    changes: list[tuple[str, str | None, str]] = field(default_factory=list)   # (ticker, latest quarter before, after)
    failures: list[str] = field(default_factory=list)


@dataclass
class FilingsResult:
    updated: list[tuple[str, str, int]] = field(default_factory=list)          # (ticker, accession, old chunks removed)
    unchanged: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)                        # a new 10-K whose sections could not be cut out
    failures: list[str] = field(default_factory=list)


def latest_quarters(engine) -> dict[str, str]:
    with engine.connect() as connection:
        return {row.ticker: str(row.latest) for row in connection.execute(LATEST_QUARTERS)}


def refresh_facts(engine, companies, download, load, log=print) -> FactsResult:
    """Re-download every company's facts, reload them, and report which companies gained a quarter."""
    before = latest_quarters(engine)
    result = FactsResult()
    for company in companies:
        try:
            download(company["cik"], refresh=True)      # overwrite the cached file with the SEC's current one
            load(engine, company)                       # the normal loader reads that file and upserts every row
        except Exception as error:                      # one bad company must not stop the others
            result.failures.append(company["ticker"])
            log(f"{company['ticker']}: FAILED ({error})")
    after = latest_quarters(engine)
    result.changes = [(ticker, before.get(ticker), latest) for ticker, latest in sorted(after.items())
                      if latest != before.get(ticker)]
    return result


def refresh_filings(engine, companies, list_filings, load, log=print) -> FilingsResult:
    """Load each company's newest 10-K if it is not loaded yet, then drop the older filing's chunks.

    Only the latest 10-K is kept searchable, and old text is deleted only after the new text loaded successfully.
    """
    result = FilingsResult()
    for company in companies:
        ticker, cik = company["ticker"], company["cik"]
        try:
            filings = list_filings(cik, refresh=True)   # newest first
            if not filings:
                result.unchanged.append(ticker)
                continue
            newest = filings[0]["accession_no"]
            with engine.connect() as connection:
                already_loaded = newest in {row[0] for row in connection.execute(LOADED_FILINGS, {"cik": cik})}
            if not already_loaded:
                counts = load(engine, company, filings[0])
                if all(count is None for count in counts.values()):
                    result.unreadable.append(ticker)    # keep whatever text we had: there is nothing to replace it with
                    continue
            with engine.begin() as connection:
                removed = connection.execute(DELETE_OLDER_CHUNKS, {"cik": cik, "keep": newest}).rowcount
            if already_loaded and not removed:
                result.unchanged.append(ticker)
            else:
                result.updated.append((ticker, newest, removed))
        except Exception as error:
            result.failures.append(ticker)
            log(f"{ticker}: FAILED ({error})")
    return result


def format_report(facts: FactsResult | None, filings: FilingsResult | None) -> list[str]:
    lines = []
    if facts is not None:
        lines.append("Quarterly numbers")
        lines += [f"  {ticker:<5} {before or 'none'} -> {after}" for ticker, before, after in facts.changes]
        if not facts.changes:
            lines.append("  no new quarters")
        if facts.failures:
            lines.append(f"  failed: {', '.join(facts.failures)}")
    if filings is not None:
        lines.append("10-K filings")
        lines += [f"  {ticker:<5} loaded {accession}, removed {removed} chunks of the older filing"
                  for ticker, accession, removed in filings.updated]
        if not filings.updated:
            lines.append("  no new 10-K filings")
        if filings.unreadable:
            lines.append(f"  new 10-K but sections could not be extracted (old text kept): {', '.join(filings.unreadable)}")
        if filings.failures:
            lines.append(f"  failed: {', '.join(filings.failures)}")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh the database from SEC EDGAR.")
    parser.add_argument("--facts-only", action="store_true", help="only the quarterly numbers")
    parser.add_argument("--filings-only", action="store_true", help="only the 10-K text")
    args = parser.parse_args()

    from sec_copilot.config import DATABASE_URL
    from sec_copilot.ingest.edgar import fetch_company_facts, resolve_tickers
    from sec_copilot.ingest.filings import list_10k_filings
    from sec_copilot.ingest.load_facts import TICKERS, load_company
    from sec_copilot.ingest.load_filings import load_filing

    engine = create_engine(DATABASE_URL)
    companies = resolve_tickers(TICKERS)
    facts = None if args.filings_only else refresh_facts(engine, companies, fetch_company_facts, load_company)
    filings = None if args.facts_only else refresh_filings(engine, companies, list_10k_filings, load_filing)

    print()
    print("\n".join(format_report(facts, filings)))
    print("\nNext steps:")
    if filings and filings.updated:
        print("  uv run python -m sec_copilot.rag.embed_chunks      (embed the new filing text)")
    if facts and facts.changes:
        print("  uv run python -m sec_copilot.ml.backtest           (re-run the forecast backtest on the new quarters)")
        print("  uv run python -m sec_copilot.eval.run_eval         (check that the golden questions still hold)")
    print("  docker compose up -d --build                        (the image contains the saved backtest results)")


if __name__ == "__main__":
    main()