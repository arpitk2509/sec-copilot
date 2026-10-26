import pytest

from sec_copilot.tools.sql_safety import MAX_ROWS, UnsafeSQL, validate_and_limit

GOOD = [
    "SELECT ticker, revenue FROM v_quarterly_metrics",
    "SELECT * FROM companies WHERE ticker = 'AAPL'",
    "SELECT ticker, period_end, revenue, LAG(revenue, 4) OVER (PARTITION BY ticker ORDER BY period_end) FROM v_quarterly_metrics",
    "SELECT ticker, ROUND(AVG(operating_margin_pct), 2), COUNT(*), MAX(period_end) FROM v_quarterly_metrics GROUP BY ticker HAVING COUNT(*) > 4",
    "WITH x AS (SELECT cik, revenue FROM v_quarterly_metrics) SELECT c.ticker, x.revenue::numeric / 1e9 FROM x JOIN companies c ON c.cik = x.cik",
    "SELECT CASE WHEN revenue_yoy_pct > 10 THEN 'fast' ELSE 'slow' END, COALESCE(NULLIF(revenue, 0), 1) FROM v_quarterly_metrics",
    "SELECT * FROM companies WHERE name LIKE '%Apple%' AND ticker IN ('AAPL', 'MSFT')",
    "SELECT EXTRACT(year FROM period_end), DATE_TRUNC('quarter', period_end) FROM v_quarterly_metrics",
    'SELECT "ticker" FROM "companies"',
    "SELECT 1",
]

BAD = [
    ("DROP TABLE companies", "SELECT"),
    ("DELETE FROM companies", "SELECT"),
    ("UPDATE companies SET name = 'x'", "SELECT"),
    ("INSERT INTO companies VALUES (1, 'a', 'b', 'c')", "SELECT"),
    ("TRUNCATE companies", "SELECT"),
    ("COPY companies TO '/tmp/x'", "SELECT"),
    ("SET ROLE postgres", "SELECT"),
    ("EXPLAIN ANALYZE SELECT * FROM companies", "SELECT"),
    ("SELECT 1; DROP TABLE companies", "exactly one"),
    ("SELECT * FROM companies; SELECT * FROM companies", "exactly one"),
    ("SELECT * FROM financial_facts", "not available"),
    ("SELECT * FROM doc_chunks", "not available"),
    ("SELECT * FROM companies c, pg_class", "not available"),
    ("SELECT * FROM public.companies", "Schema-qualified"),
    ("SELECT * FROM pg_catalog.pg_tables", "Schema-qualified"),
    ("SELECT * FROM information_schema.tables", "Schema-qualified"),
    ("SELECT pg_sleep(10)", "PG_SLEEP"),
    ("SELECT version()", "not allowed"),
    ("SELECT current_setting('server_version')", "CURRENT_SETTING"),
    ("SELECT lo_import('/etc/passwd')", "LO_IMPORT"),
    ("SELECT repeat('a', 100000000)", "not allowed"),
    ("SELECT * FROM generate_series(1, 1000000000)", "not allowed"),
    ("SELECT * INTO newtable FROM companies", "not allowed"),
    ("WITH d AS (DELETE FROM companies RETURNING *) SELECT * FROM d", "not allowed"),
    ("SELECT * FROM companies FOR UPDATE", "not allowed"),
    ("SELECT * FROM companies WHERE name = $1", "not allowed"),
    ("", "empty"),
    ("   ;  ", "empty"),
    ("SELECT FROM WHERE", "parsed"),
    ("SELECT * FROM companies WHERE " + "ticker = 'A' OR " * 400 + "1=1", "longer than"),
]


@pytest.mark.parametrize("sql", GOOD)
def test_good_queries_pass_and_get_a_limit(sql):
    safe = validate_and_limit(sql)
    assert safe.upper().rstrip().endswith(f"LIMIT {MAX_ROWS}")


@pytest.mark.parametrize("sql, reason", BAD)
def test_bad_queries_are_rejected_with_a_reason(sql, reason):
    with pytest.raises(UnsafeSQL) as caught:
        validate_and_limit(sql)
    assert reason.lower() in str(caught.value).lower()


def test_comments_are_stripped_so_nothing_hides_in_them():
    safe = validate_and_limit("SELECT ticker FROM companies -- ; DROP TABLE companies\n /* more */")
    assert "DROP" not in safe.upper() and "--" not in safe and "/*" not in safe


def test_existing_limit_is_kept_if_small_and_capped_if_large():
    assert validate_and_limit("SELECT ticker FROM companies LIMIT 5").upper().endswith("LIMIT 5")
    assert validate_and_limit("SELECT ticker FROM companies LIMIT 100000").upper().endswith(f"LIMIT {MAX_ROWS}")


def test_the_original_text_is_never_returned_as_is():
    safe = validate_and_limit("select   ticker   from   companies")
    assert safe == f"SELECT ticker FROM companies LIMIT {MAX_ROWS}"



def test_descending_sorts_put_missing_values_last():
    safe = validate_and_limit("SELECT ticker FROM v_quarterly_metrics ORDER BY operating_margin_pct DESC")
    assert "DESC NULLS LAST" in safe.upper()


def test_ascending_sorts_and_explicit_nulls_first_are_left_alone():
    assert "NULLS" not in validate_and_limit("SELECT ticker FROM companies ORDER BY ticker").upper().replace("NULLS LAST", "")
    explicit = validate_and_limit("SELECT ticker FROM v_quarterly_metrics ORDER BY revenue DESC NULLS FIRST")
    assert "NULLS LAST" not in explicit.upper()