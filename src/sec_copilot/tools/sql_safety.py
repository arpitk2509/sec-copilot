"""Check SQL written by an LLM before it touches the database.

Rules: one SELECT statement, only whitelisted views, only whitelisted functions, no tricks, and a row limit.
We never run the original text. We run SQL regenerated from the parsed tree, so what we checked is what runs.
"""
import re

import sqlglot
import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

MAX_SQL_CHARS = 4000
MAX_ROWS = 200

# The only relations the model may read. Everything else, including the raw tables, is off limits.
ALLOWED_RELATIONS = frozenset({"v_quarterly_metrics", "companies"})

# The only functions the model may call (sqlglot's names for them). Anything unknown is rejected,
# which blocks pg_sleep, version, repeat, generate_series, current_setting, file and network functions.
ALLOWED_FUNCTIONS = frozenset({
    "Abs", "And", "Avg", "Case", "Cast", "Ceil", "Coalesce", "Count", "CurrentDate", "DenseRank",
    "Extract", "FirstValue", "Floor", "Greatest", "If", "Lag", "LastValue", "Lead", "Least", "Ln",
    "Lower", "Max", "Min", "NthValue", "Nullif", "Ntile", "Or", "PercentRank", "Pow", "Rank",
    "Round", "RowNumber", "Sqrt", "Stddev", "StddevPop", "StddevSamp", "Sum", "TimestampTrunc",
    "TryCast", "Upper", "Variance", "VariancePop",
})

# Statement types that must never appear anywhere in the query, even nested inside a CTE.
_FORBIDDEN_NAMES = (
    "Insert", "Update", "Delete", "Drop", "Create", "Alter", "AlterTable", "Command", "Merge", "Into",
    "Copy", "Set", "Use", "Lock", "Transaction", "Commit", "Rollback", "Grant", "Revoke",
    "TruncateTable", "Pragma", "Describe", "Show", "Analyze", "Comment", "Placeholder", "Parameter",
)
FORBIDDEN_NODES = tuple(getattr(exp, name) for name in _FORBIDDEN_NAMES if hasattr(exp, name))


class UnsafeSQL(ValueError):
    """The query was rejected. The message says why, so it can be shown to the model for a retry."""


def validate_and_limit(sql: str, max_rows: int = MAX_ROWS) -> str:
    """Return a safe version of the query (with a row limit), or raise UnsafeSQL."""
    if not sql or not sql.strip().strip(";").strip():
        raise UnsafeSQL("The query is empty.")
    if len(sql) > MAX_SQL_CHARS:
        raise UnsafeSQL(f"The query is longer than {MAX_SQL_CHARS} characters.")

    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except SqlglotError as error:
        raise UnsafeSQL(f"The query could not be parsed: {error}") from error
    if len(statements) != 1:
        raise UnsafeSQL("Send exactly one statement.")

    statement = statements[0]
    if not isinstance(statement, exp.Select):
        raise UnsafeSQL("Only SELECT queries are allowed.")

    for node in statement.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise UnsafeSQL(f"{type(node).__name__.upper()} is not allowed.")
        if isinstance(node, exp.Func) and type(node).__name__ not in ALLOWED_FUNCTIONS:
            name = node.name or type(node).__name__
            raise UnsafeSQL(f"The function {name.upper()} is not allowed.")

    cte_names = {cte.alias_or_name.lower() for cte in statement.find_all(exp.CTE)}
    for table in statement.find_all(exp.Table):
        name = table.name.lower()
        if table.db or table.catalog:
            raise UnsafeSQL("Schema-qualified names are not allowed. Use plain view names.")
        if name in cte_names:
            continue
        if name not in ALLOWED_RELATIONS:
            shown = name or "a table function"
            allowed = ", ".join(sorted(ALLOWED_RELATIONS))
            raise UnsafeSQL(f"{shown} is not available. You may only read: {allowed}.")
        
    # Postgres puts NULLs FIRST when sorting DESC, so a company with no data tops a "highest" ranking.
    # Unless the query explicitly asks for NULLS FIRST, we put missing values last.
    order = statement.args.get("order")
    if order is not None and not re.search(r"nulls\s+first", sql, re.IGNORECASE):
        for ordered in order.expressions:
            if isinstance(ordered, exp.Ordered) and ordered.args.get("desc") and ordered.args.get("nulls_first"):
                ordered.set("nulls_first", False)
    limit = statement.args.get("limit")
    requested = None
    if isinstance(limit, exp.Limit) and isinstance(limit.expression, exp.Literal) and limit.expression.is_int:
        requested = int(limit.expression.this)
    final_limit = min(requested, max_rows) if requested is not None else max_rows

    return statement.limit(final_limit).sql(dialect="postgres", comments=False)