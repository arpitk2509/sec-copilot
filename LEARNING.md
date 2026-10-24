# Learning log
## Phase 1
- Git loop: status → add → commit → push.
- uv writes `requires-python` into pyproject.toml, and the pinned Python must match it.

CTE (WITH) splits a query into named steps that read top to bottom.
MAX(...) FILTER (WHERE ...) is a pivot trick. The unique constraint guarantees one value per group, so MAX just picks that single value.
LAG(x, n) OVER (PARTITION BY ... ORDER BY ...) reads the value n rows back within each company.
NULLIF(revenue, 0) turns a zero into NULL, so division never crashes.
The q_index guard: plain LAG(revenue, 4) silently compares the wrong periods if a quarter is missing. Checking that the earlier row’s index is exactly 4 quarters back makes the view return NULL instead of a wrong number. In an interview this shows you thought about data quality.

Don’t trust vendor labels when you can derive them from the underlying dates.
A guard that skips a suspicious derived number beats storing a wrong one.
SQL quality checks caught two problems that eyeballing a table would have missed.

HTML source line breaks are not real line breaks.
Why headings are matched only at the start of a line.
The table of contents has the same headings as the real section, so we keep the longest span.
My tests caught a bug before real data did.

Pasting several docker compose exec commands together can swallow the next one, so use -T for one-shot queries.
Why failures like IBM’s and Intel’s are reported openly (NOT FOUND) instead of hidden.