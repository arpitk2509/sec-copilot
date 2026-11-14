import pytest
from sqlalchemy import create_engine, text

from sec_copilot.ingest.refresh import (
    FactsResult,
    FilingsResult,
    format_report,
    latest_quarters,
    refresh_facts,
    refresh_filings,
)

COMPANIES = [{"cik": 1, "ticker": "AAPL"}, {"cik": 2, "ticker": "MSFT"}]


@pytest.fixture
def engine():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE companies (cik INTEGER PRIMARY KEY, ticker TEXT)"))
        connection.execute(text("CREATE TABLE financial_facts (cik INTEGER, period_end TEXT, fiscal_period TEXT)"))
        connection.execute(text("CREATE TABLE doc_chunks (cik INTEGER, accession_no TEXT, chunk_index INTEGER)"))
        connection.execute(text("INSERT INTO companies VALUES (1, 'AAPL'), (2, 'MSFT')"))
        connection.execute(text("INSERT INTO financial_facts VALUES (1,'2026-03-28','Q2'), (1,'2026-06-27','Q3'), "
                                "(1,'2026-09-26','FY'), (2,'2026-03-31','Q3'), (2,'2026-06-30','Q4')"))
    return engine


def add_chunks(engine, cik, accession, count):
    with engine.begin() as connection:
        for index in range(count):
            connection.execute(text("INSERT INTO doc_chunks VALUES (:c, :a, :i)"), {"c": cik, "a": accession, "i": index})


def chunk_counts(engine):
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT cik, accession_no, COUNT(*) FROM doc_chunks GROUP BY 1, 2 ORDER BY 1, 2"))
        return [tuple(row) for row in rows]


# ---------- quarterly numbers ----------

def test_the_latest_quarter_ignores_full_year_rows(engine):
    assert latest_quarters(engine) == {"AAPL": "2026-06-27", "MSFT": "2026-06-30"}    # the 'FY' row dated 2026-09-26 is not a quarter


def test_a_refresh_downloads_fresh_files_reloads_and_reports_new_quarters(engine):
    downloads = []

    def download(cik, refresh):
        downloads.append((cik, refresh))

    def load(engine, company):
        if company["ticker"] == "AAPL":                       # Apple reported a new quarter, Microsoft did not
            with engine.begin() as connection:
                connection.execute(text("INSERT INTO financial_facts VALUES (1, '2026-09-26', 'Q4')"))

    result = refresh_facts(engine, COMPANIES, download, load, log=lambda m: None)
    assert downloads == [(1, True), (2, True)]                # the cache was bypassed for every company
    assert result.changes == [("AAPL", "2026-06-27", "2026-09-26")] and result.failures == []


def test_one_failing_company_does_not_stop_the_others(engine):
    loaded, logs = [], []

    def download(cik, refresh):
        if cik == 1:
            raise RuntimeError("SEC said 503")

    result = refresh_facts(engine, COMPANIES, download, lambda e, c: loaded.append(c["ticker"]), log=logs.append)
    assert result.failures == ["AAPL"] and loaded == ["MSFT"] and "SEC said 503" in logs[0]


# ---------- 10-K filings ----------

def filings_list(*accessions):
    return lambda cik, refresh: [{"accession_no": a} for a in accessions] if cik == 1 else []


def test_a_new_10k_replaces_the_old_text_only_after_it_loaded(engine):
    add_chunks(engine, 1, "OLD", 5)
    calls = []

    def load(engine, company, filing):
        calls.append(filing["accession_no"])
        add_chunks(engine, 1, "NEW", 3)
        return {"Item 1A": 2, "Item 7": 1}

    result = refresh_filings(engine, COMPANIES, filings_list("NEW", "OLD"), load, log=lambda m: None)
    assert calls == ["NEW"] and result.updated == [("AAPL", "NEW", 5)]
    assert chunk_counts(engine) == [(1, "NEW", 3)]            # the old filing's chunks are gone, the new ones remain
    assert result.unchanged == ["MSFT"]                       # Microsoft has no filings in this fake list


def test_the_same_10k_is_not_loaded_again(engine):
    add_chunks(engine, 1, "CURRENT", 4)
    calls = []
    result = refresh_filings(engine, COMPANIES, filings_list("CURRENT"), lambda e, c, f: calls.append(f), log=lambda m: None)
    assert calls == [] and result.updated == [] and "AAPL" in result.unchanged
    assert chunk_counts(engine) == [(1, "CURRENT", 4)]


def test_old_text_is_kept_when_the_new_10k_cannot_be_read(engine):
    add_chunks(engine, 1, "OLD", 5)
    result = refresh_filings(engine, COMPANIES, filings_list("NEW", "OLD"),
                             lambda e, c, f: {"Item 1A": None, "Item 7": None}, log=lambda m: None)
    assert result.unreadable == ["AAPL"] and result.updated == []
    assert chunk_counts(engine) == [(1, "OLD", 5)]            # nothing was deleted


def test_a_company_that_never_had_text_is_reported_each_time_without_harm(engine):
    result = refresh_filings(engine, COMPANIES, filings_list("NEW"), lambda e, c, f: {"Item 1A": None}, log=lambda m: None)
    assert result.unreadable == ["AAPL"] and chunk_counts(engine) == []


def test_leftover_old_chunks_are_cleaned_even_when_the_new_filing_was_already_loaded(engine):
    add_chunks(engine, 1, "NEW", 3)
    add_chunks(engine, 1, "OLD", 2)
    calls = []
    result = refresh_filings(engine, COMPANIES, filings_list("NEW", "OLD"), lambda e, c, f: calls.append(1), log=lambda m: None)
    assert calls == [] and result.updated == [("AAPL", "NEW", 2)] and chunk_counts(engine) == [(1, "NEW", 3)]


def test_a_failure_while_listing_filings_is_reported_and_the_rest_continue(engine):
    def listing(cik, refresh):
        if cik == 1:
            raise RuntimeError("network down")
        return []

    logs = []
    result = refresh_filings(engine, COMPANIES, listing, lambda e, c, f: {}, log=logs.append)
    assert result.failures == ["AAPL"] and result.unchanged == ["MSFT"] and "network down" in logs[0]


def test_the_listing_is_always_refreshed_from_the_sec(engine):
    seen = []

    def listing(cik, refresh):
        seen.append(refresh)
        return []

    refresh_filings(engine, COMPANIES, listing, lambda e, c, f: {}, log=lambda m: None)
    assert seen == [True, True]


# ---------- the report ----------

def test_the_report_says_what_changed_and_what_did_not():
    changed = format_report(FactsResult(changes=[("AAPL", "2026-06-27", "2026-09-26")], failures=["IBM"]),
                            FilingsResult(updated=[("AAPL", "0000320193-26-000123", 108)], unreadable=["INTC"]))
    text_report = "\n".join(changed)
    assert "AAPL  2026-06-27 -> 2026-09-26" in text_report and "failed: IBM" in text_report
    assert "loaded 0000320193-26-000123, removed 108 chunks" in text_report and "old text kept): INTC" in text_report
    quiet = "\n".join(format_report(FactsResult(), FilingsResult()))
    assert "no new quarters" in quiet and "no new 10-K filings" in quiet
    assert format_report(None, None) == []