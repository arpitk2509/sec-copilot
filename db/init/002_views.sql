-- Quarterly metrics per company: margins, quarter-over-quarter and year-over-year growth.
CREATE OR REPLACE VIEW v_quarterly_metrics AS
WITH pivoted AS (
    -- Turn "one row per metric" into "one row per quarter with a column per metric".
    SELECT
        f.cik,
        c.ticker,
        f.fiscal_year,
        f.fiscal_period,
        f.fiscal_year * 4 + RIGHT(f.fiscal_period, 1)::int AS q_index,
        MAX(f.period_end) AS period_end,
        MAX(f.value) FILTER (WHERE f.metric = 'revenue')          AS revenue,
        MAX(f.value) FILTER (WHERE f.metric = 'gross_profit')     AS gross_profit,
        MAX(f.value) FILTER (WHERE f.metric = 'operating_income') AS operating_income,
        MAX(f.value) FILTER (WHERE f.metric = 'net_income')       AS net_income
    FROM financial_facts f
    JOIN companies c ON c.cik = f.cik
    WHERE f.fiscal_period IN ('Q1', 'Q2', 'Q3', 'Q4')
    GROUP BY f.cik, c.ticker, f.fiscal_year, f.fiscal_period
),
lagged AS (
    -- LAG(x, n) looks n rows back inside each company, ordered by time.
    SELECT
        p.*,
        LAG(revenue, 1) OVER w AS prev_q_revenue,
        LAG(q_index, 1) OVER w AS prev_q_index,
        LAG(revenue, 4) OVER w AS prev_y_revenue,
        LAG(q_index, 4) OVER w AS prev_y_index
    FROM pivoted p
    WINDOW w AS (PARTITION BY cik ORDER BY q_index)
)
SELECT
    cik, ticker, fiscal_year, fiscal_period, period_end,
    revenue, gross_profit, operating_income, net_income,
    ROUND(100.0 * gross_profit / NULLIF(revenue, 0), 2)     AS gross_margin_pct,
    ROUND(100.0 * operating_income / NULLIF(revenue, 0), 2) AS operating_margin_pct,
    ROUND(100.0 * net_income / NULLIF(revenue, 0), 2)       AS net_margin_pct,
    -- Only compare when the earlier row really is the previous quarter / same quarter last year.
    CASE WHEN prev_q_index = q_index - 1
         THEN ROUND(100.0 * (revenue - prev_q_revenue) / NULLIF(prev_q_revenue, 0), 2)
    END AS revenue_qoq_pct,
    CASE WHEN prev_y_index = q_index - 4
         THEN ROUND(100.0 * (revenue - prev_y_revenue) / NULLIF(prev_y_revenue, 0), 2)
    END AS revenue_yoy_pct
FROM lagged;