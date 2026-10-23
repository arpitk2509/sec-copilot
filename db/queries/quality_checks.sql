-- Check 1: missing quarters.
-- Lists every place where a company's quarterly history skips one or more quarters.
WITH q AS (
    SELECT ticker, period_end,
           fiscal_year * 4 + RIGHT(fiscal_period, 1)::int AS q_index
    FROM v_quarterly_metrics
), g AS (
    SELECT ticker, period_end, q_index,
           LAG(q_index)    OVER (PARTITION BY ticker ORDER BY q_index) AS prev_index,
           LAG(period_end) OVER (PARTITION BY ticker ORDER BY q_index) AS prev_end
    FROM q
)
SELECT ticker, prev_end AS last_before_gap, period_end AS first_after_gap,
       q_index - prev_index - 1 AS missing_quarters
FROM g
WHERE q_index - prev_index > 1
ORDER BY ticker, period_end;

-- Check 2: gross profit larger than revenue.
-- This means the "revenue" tag is only part of total revenue, so the tag is wrong.
SELECT ticker, fiscal_year, fiscal_period, period_end, revenue, gross_profit
FROM v_quarterly_metrics
WHERE gross_profit > revenue
ORDER BY ticker, period_end;