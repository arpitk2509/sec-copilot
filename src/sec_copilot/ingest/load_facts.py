"""Load cleaned company facts into Postgres."""
from sqlalchemy import create_engine, text

from sec_copilot.config import DATABASE_URL
from sec_copilot.ingest.edgar import fetch_company_facts, resolve_tickers
from sec_copilot.ingest.transform import facts_to_frame

TICKERS = [
    "AAPL", "MSFT", "NVDA", "INTC", "AMD", "CSCO", "CRM", "ADBE",
    "IBM", "TXN", "MU", "AVGO", "NOW", "INTU", "AMAT",
]

# "Upsert": insert a new row, or update it if it already exists.
# This makes the script safe to run again and again.
UPSERT_COMPANY = text("""
    INSERT INTO companies (cik, ticker, name, sector)
    VALUES (:cik, :ticker, :name, :sector)
    ON CONFLICT (cik) DO UPDATE
    SET ticker = EXCLUDED.ticker, name = EXCLUDED.name, sector = EXCLUDED.sector
""")

UPSERT_FACT = text("""
    INSERT INTO financial_facts
        (cik, metric, period_start, period_end, fiscal_year, fiscal_period,
         value, unit, accession_no, is_derived)
    VALUES
        (:cik, :metric, :period_start, :period_end, :fiscal_year, :fiscal_period,
         :value, :unit, :accession_no, :is_derived)
    ON CONFLICT (cik, metric, fiscal_year, fiscal_period) DO UPDATE
    SET period_start = EXCLUDED.period_start,
        period_end   = EXCLUDED.period_end,
        value        = EXCLUDED.value,
        accession_no = EXCLUDED.accession_no,
        is_derived   = EXCLUDED.is_derived
""")


def load_company(engine, company: dict) -> int:
    """Fetch, clean, and store one company's facts. Returns the number of rows loaded."""
    raw = fetch_company_facts(company["cik"])
    facts = facts_to_frame(raw, label=company["ticker"])
    if facts.empty:
        return 0

    records = facts.assign(cik=company["cik"]).to_dict("records")
    with engine.begin() as conn:  # one transaction: every row loads, or none do
        conn.execute(UPSERT_COMPANY, company)
        conn.execute(UPSERT_FACT, records)
    return len(records)


def main() -> None:
    engine = create_engine(DATABASE_URL)
    failures = []
    for company in resolve_tickers(TICKERS):
        try:
            count = load_company(engine, company)
        except Exception as error:  # keep going: one bad company shouldn't stop the rest
            failures.append(company["ticker"])
            print(f"{company['ticker']}: FAILED ({error})")
            continue
        print(f"{company['ticker']}: loaded {count} rows" if count else f"{company['ticker']}: no usable facts, skipped")

    if failures:
        print(f"\nFailed: {', '.join(failures)}")


if __name__ == "__main__":
    main()