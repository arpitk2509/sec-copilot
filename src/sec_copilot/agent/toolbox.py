"""The two tools the model can call, plus the bookkeeping that makes citations possible."""
from datetime import date, datetime
from decimal import Decimal

from google.genai import types

from sec_copilot.rag.embeddings import QuotaExceeded
from sec_copilot.rag.retrieval import hybrid_search, keyword_search
from sec_copilot.tools.sql_runner import QueryFailed, run_query
from sec_copilot.tools.sql_safety import UnsafeSQL

MAX_ROWS_TO_MODEL = 50    # more rows than this would waste the model's context
SEARCH_RESULTS = 6
SECTIONS = ("Item 1A", "Item 7")

TOOL_DECLARATIONS = types.Tool(function_declarations=[
    types.FunctionDeclaration(
        name="run_sql",
        description="Run one read-only PostgreSQL SELECT on the financial database. Use for any number.",
        parameters_json_schema={
            "type": "object",
            "properties": {"sql": {"type": "string", "description": "A single SELECT statement."}},
            "required": ["sql"],
        },
    ),
    types.FunctionDeclaration(
        name="search_filings",
        description="Search the text of the companies' latest 10-K filings (risk factors and MD&A) for passages "
                    "relevant to a question. Use for reasons, risks, strategy and explanations.",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look for, in plain English."},
                "ticker": {"type": "string", "description": "Optional: restrict to one company, e.g. AAPL."},
                "section": {"type": "string", "enum": list(SECTIONS), "description": "Optional: risks or MD&A."},
                "fiscal_year": {"type": "integer", "description": "Optional: fiscal year of the 10-K."},
            },
            "required": ["query"],
        },
    ),
])


def jsonable(value):
    """Database values -> plain values the model can read."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


class Toolbox:
    """Runs the tools for the model and remembers every result under a citation tag (Q1, Q2... and C1, C2...)."""

    def __init__(self, engine, readonly_engine, embedder):
        self.engine = engine
        self.readonly_engine = readonly_engine
        self.embedder = embedder
        self.sources: dict[str, dict] = {}
        self._counts = {"Q": 0, "C": 0}

    def _new_tag(self, prefix: str) -> str:
        self._counts[prefix] += 1
        return f"{prefix}{self._counts[prefix]}"

    def run(self, name: str, args: dict) -> dict:
        """Run one tool. It never raises: a problem becomes an error message the model can react to."""
        try:
            if name == "run_sql":
                return self._run_sql(str(args.get("sql", "")))
            if name == "search_filings":
                return self._search_filings(args)
            return {"error": f"Unknown tool: {name}"}
        except Exception as error:  # a tool must never crash the whole conversation
            return {"error": f"The tool failed: {error}"}

    def _run_sql(self, sql: str) -> dict:
        try:
            result = run_query(self.readonly_engine, sql)
        except UnsafeSQL as error:
            return {"error": f"Query rejected: {error}"}
        except QueryFailed as error:
            return {"error": f"Database error: {error}"}

        rows = [[jsonable(value) for value in row] for row in result["rows"]]
        shown = rows[:MAX_ROWS_TO_MODEL]
        tag = self._new_tag("Q")
        self.sources[tag] = {"type": "sql", "tag": tag, "sql": result["sql"], "columns": result["columns"],
                             "rows": shown, "total_rows": len(rows)}
        return {"citation": tag, "columns": result["columns"], "rows": shown,
                "row_count": len(rows), "truncated": len(rows) > len(shown)}

    def _search_filings(self, args: dict) -> dict:
        query = str(args.get("query", "")).strip()
        if not query:
            return {"error": "query is required."}
        section = args.get("section") or None
        if section is not None and section not in SECTIONS:
            return {"error": f"section must be one of {list(SECTIONS)}."}
        ticker = str(args["ticker"]).upper() if args.get("ticker") else None
        year = int(args["fiscal_year"]) if args.get("fiscal_year") else None
        filters = {"ticker": ticker, "fiscal_year": year, "section": section}

        note = None
        try:
            hits = hybrid_search(self.engine, self.embedder, query, k=SEARCH_RESULTS, **filters)
        except QuotaExceeded:
            hits = keyword_search(self.engine, query, SEARCH_RESULTS, **filters)
            note = "Meaning-based search is unavailable right now (quota), so these come from keyword search only."

        results = []
        for hit in hits:
            tag = self._new_tag("C")
            self.sources[tag] = {"type": "filing", "tag": tag, "ticker": hit.ticker, "fiscal_year": hit.fiscal_year,
                                 "section": hit.section, "chunk_index": hit.chunk_index, "text": hit.content}
            results.append({"citation": tag, "company": hit.ticker, "fiscal_year": hit.fiscal_year,
                            "section": hit.section, "text": hit.content})
        response: dict = {"results": results}
        if note:
            response["note"] = note
        if not results:
            response["note"] = "No matching text. Try different words or remove the filters."
        return response