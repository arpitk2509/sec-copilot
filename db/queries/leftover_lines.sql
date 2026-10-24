-- Short lines repeated 5+ times inside one company's chunks: usually leftover page headers or footers.
WITH lines AS (
    SELECT c.ticker, d.section, l AS line, regexp_replace(lower(l), '\d+', '#', 'g') AS norm
    FROM doc_chunks d
    JOIN companies c ON c.cik = d.cik,
         LATERAL regexp_split_to_table(d.content, E'\n') AS l
    WHERE length(l) BETWEEN 1 AND 100
)
SELECT ticker, count(*) AS times, min(line) AS example
FROM lines
GROUP BY ticker, norm
HAVING count(*) >= 5
ORDER BY times DESC
LIMIT 25;