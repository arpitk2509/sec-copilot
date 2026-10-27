"""Run LLM-written SQL safely: validate it, then execute it as a read-only database user."""
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from sec_copilot.config import READONLY_DATABASE_URL
from sec_copilot.tools.sql_safety import MAX_ROWS, UnsafeSQL, validate_and_limit


class QueryFailed(Exception):
    """The query was allowed but the database rejected or stopped it (typo in a column name, timeout...)."""


def run_query(engine, sql: str, max_rows: int = MAX_ROWS) -> dict:
    """Returns {'sql': the query that really ran, 'columns': [...], 'rows': [[...], ...]}."""
    safe_sql = validate_and_limit(sql, max_rows)
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")   # a second lock, on top of the role's own setting
            # text() would treat ':' as a parameter marker, so escape every colon.
            result = conn.execute(text(safe_sql.replace(":", "\\:")))
            rows = [list(row) for row in result.fetchmany(max_rows)]
            columns = list(result.keys())
    except DBAPIError as error:
        raise QueryFailed(str(error.orig).splitlines()[0]) from error
    return {"sql": safe_sql, "columns": columns, "rows": rows}


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit('Usage: python -m sec_copilot.tools.sql_runner "SELECT ..."')
    engine = create_engine(READONLY_DATABASE_URL)
    try:
        result = run_query(engine, sys.argv[1])
    except UnsafeSQL as error:
        raise SystemExit(f"REJECTED before reaching the database: {error}") from error
    except QueryFailed as error:
        raise SystemExit(f"DATABASE ERROR: {error}") from error

    print("Ran:", result["sql"])
    print(" | ".join(result["columns"]))
    for row in result["rows"][:20]:
        print(" | ".join(str(value) for value in row))
    print(f"({len(result['rows'])} rows)")


if __name__ == "__main__":
    main()