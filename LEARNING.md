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
Real filings vary in ways synthetic tests can’t predict, so scaling from 1 company to 15 exposed three problems.
Why regex patterns should be as loose as the data allows on the end markers and as strict as needed on the start markers.
Why you check results by querying the stored data, not just by reading the code.
Two sources of noise showed up only after running on 15 companies.
Why we check what’s stored, not just what the code says.
Why the “repeated header” rule only applies to lines without numbers, so real data rows are safe.
The free-tier limit counts texts, not requests, so we found it by hitting it.
Why the job stops cleanly and saves each batch, and why you only re-run it after the reset.
Why hybrid search fuses ranks instead of scores.
Why exact-word search and meaning-based search fail on different questions.
An evaluation harness needs its own tests against real output. Both bugs came from data shapes my fake data never had.
A failing case can mean the agent, the check, or the golden data is wrong, so read the answer before changing anything.
A fix for one question can break another, so keep running the whole set. The price refusal is the proof.
Warnings in logs, like the SDK’s “non-text parts” message, are clues worth reading.
A model at temperature 0 still varies between runs, so measure with repeated runs.
My “forbid tools” fix didn’t survive a real run. A change isn’t done until the logs stop showing the symptom.
A fix for one case (the price refusal) once broke another, which is why the whole set runs every time.