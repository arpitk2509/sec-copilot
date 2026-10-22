-- Data coverage per company: how many quarterly rows exist for each metric, and the date range.
SELECT c.ticker,
       COUNT(*) FILTER (WHERE f.metric = 'revenue')          AS revenue,
       COUNT(*) FILTER (WHERE f.metric = 'gross_profit')     AS gross_profit,
       COUNT(*) FILTER (WHERE f.metric = 'operating_income') AS op_income,
       COUNT(*) FILTER (WHERE f.metric = 'net_income')       AS net_income,
       MIN(f.period_end) FILTER (WHERE f.metric = 'revenue') AS revenue_from,
       MAX(f.period_end)                                     AS latest_period
FROM financial_facts f
JOIN companies c ON c.cik = f.cik
WHERE f.fiscal_period <> 'FY'
GROUP BY c.ticker
ORDER BY c.ticker;