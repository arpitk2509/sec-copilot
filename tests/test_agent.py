from datetime import date
from decimal import Decimal

from google.genai import types

from sec_copilot.agent import toolbox as toolbox_module
from sec_copilot.agent.agent import MAX_MODEL_CALLS, answer_question, check_citations
from sec_copilot.agent.prompts import FINAL_ANSWER_NOTICE, SYSTEM_PROMPT
from sec_copilot.agent.toolbox import MAX_ROWS_TO_MODEL, Toolbox, jsonable
from sec_copilot.rag.embeddings import QuotaExceeded
from sec_copilot.rag.retrieval import Hit
from sec_copilot.tools.sql_runner import QueryFailed
from sec_copilot.tools.sql_safety import UnsafeSQL


# ---------- helpers: a scripted model and a fake toolbox ----------

def text_reply(text):
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=[types.Part(text=text)]))])


def tool_reply(*calls):
    parts = [types.Part(function_call=types.FunctionCall(name=name, args=args)) for name, args in calls]
    return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=parts))])


class ScriptedGemini:
    def __init__(self, script):
        self.script, self.calls = list(script), []

    def generate(self, contents, system_instruction=None, tools=None):
        self.calls.append({"contents": list(contents), "system": system_instruction, "tools": tools})
        return self.script.pop(0)


class FakeToolbox:
    def __init__(self, results=None):
        self.sources = {"Q1": {"type": "sql"}}
        self.results = results or {}
        self.ran = []

    def run(self, name, args):
        self.ran.append((name, args))
        return self.results.get(name, {"citation": "Q1", "rows": [[1]]})


# ---------- the loop ----------

def test_answers_directly_when_no_tool_is_needed():
    gemini = ScriptedGemini([text_reply("Hello.")])
    result = answer_question("hi", gemini, FakeToolbox())
    assert result.answer == "Hello." and result.model_calls == 1 and result.tool_calls == []
    assert gemini.calls[0]["system"] == SYSTEM_PROMPT and gemini.calls[0]["tools"] is not None


def test_runs_a_tool_then_answers_with_the_conversation_replayed_correctly():
    first = tool_reply(("run_sql", {"sql": "SELECT 1"}))
    gemini = ScriptedGemini([first, text_reply("The answer is 1 [Q1].")])
    toolbox = FakeToolbox({"run_sql": {"citation": "Q1", "rows": [[1]]}})
    result = answer_question("what is one?", gemini, toolbox)

    assert result.answer == "The answer is 1 [Q1]." and result.model_calls == 2
    assert result.tool_calls == [("run_sql", {"sql": "SELECT 1"})] and toolbox.ran == result.tool_calls
    second_call = gemini.calls[1]["contents"]
    assert [c.role for c in second_call] == ["user", "model", "user"]
    assert second_call[1] is first.candidates[0].content                      # the model's own message, unchanged
    assert second_call[2].parts[0].function_response.response == {"citation": "Q1", "rows": [[1]]}


def test_several_tool_calls_in_one_turn_are_answered_together_in_order():
    gemini = ScriptedGemini([tool_reply(("run_sql", {"sql": "A"}), ("search_filings", {"query": "B"})), text_reply("Done.")])
    answer_question("q", gemini, FakeToolbox())
    replies = gemini.calls[1]["contents"][2].parts
    assert [p.function_response.name for p in replies] == ["run_sql", "search_filings"]


def test_tool_errors_are_given_to_the_model_so_it_can_retry():
    gemini = ScriptedGemini([
        tool_reply(("run_sql", {"sql": "DROP TABLE x"})),
        tool_reply(("run_sql", {"sql": "SELECT 1"})),
        text_reply("Fixed it."),
    ])
    toolbox = FakeToolbox({"run_sql": {"error": "Query rejected: Only SELECT queries are allowed."}})
    result = answer_question("q", gemini, toolbox)
    assert result.answer == "Fixed it." and result.model_calls == 3
    seen = gemini.calls[1]["contents"][2].parts[0].function_response.response
    assert "rejected" in seen["error"]


def test_the_last_allowed_call_has_no_tools_and_asks_for_an_answer():
    script = [tool_reply(("run_sql", {"sql": f"SELECT {i}"})) for i in range(MAX_MODEL_CALLS - 1)] + [text_reply("Best effort.")]
    gemini = ScriptedGemini(script)
    result = answer_question("q", gemini, FakeToolbox())
    assert result.model_calls == MAX_MODEL_CALLS == len(gemini.calls)
    assert gemini.calls[-1]["tools"] is None and FINAL_ANSWER_NOTICE.strip() in gemini.calls[-1]["system"]
    assert all(call["tools"] is not None for call in gemini.calls[:-1])
    assert result.answer == "Best effort." and result.hit_call_limit is True


def test_a_model_that_ignores_the_no_tools_rule_gets_a_fallback_answer():
    script = [tool_reply(("run_sql", {"sql": "x"})) for _ in range(MAX_MODEL_CALLS)]
    result = answer_question("q", ScriptedGemini(script), FakeToolbox())
    assert result.answer == "I could not produce an answer."
    assert len(result.tool_calls) == MAX_MODEL_CALLS - 1          # the tool request on the last call was ignored


def test_the_prompt_names_the_tools_and_the_data():
    for needle in ("run_sql", "search_filings", "v_quarterly_metrics", "companies", "NULL", "[Q1]", "[C2]"):
        assert needle in SYSTEM_PROMPT


# ---------- the toolbox ----------

def test_jsonable_turns_database_values_into_plain_values():
    assert jsonable(Decimal("109417000000")) == 109417000000 and isinstance(jsonable(Decimal("5")), int)
    assert jsonable(Decimal("50.06")) == 50.06
    assert jsonable(date(2026, 6, 27)) == "2026-06-27"
    assert jsonable(None) is None and jsonable("x") == "x"


def make_toolbox():
    return Toolbox(engine="main-engine", readonly_engine="readonly-engine", embedder="embedder")


def test_unknown_tools_and_crashing_tools_become_error_messages(monkeypatch):
    toolbox = make_toolbox()
    assert "Unknown tool" in toolbox.run("delete_everything", {})["error"]

    def boom(engine, sql):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(toolbox_module, "run_query", boom)
    assert "disk on fire" in toolbox.run("run_sql", {"sql": "SELECT 1"})["error"]


def test_sql_results_get_tags_are_trimmed_and_remembered(monkeypatch):
    rows = [[Decimal(i), date(2026, 1, 1)] for i in range(80)]
    monkeypatch.setattr(toolbox_module, "run_query",
                        lambda engine, sql: {"sql": "SELECT safe", "columns": ["n", "d"], "rows": rows})
    toolbox = make_toolbox()
    first = toolbox.run("run_sql", {"sql": "SELECT 1"})
    second = toolbox.run("run_sql", {"sql": "SELECT 2"})
    assert (first["citation"], second["citation"]) == ("Q1", "Q2")
    assert len(first["rows"]) == MAX_ROWS_TO_MODEL and first["row_count"] == 80 and first["truncated"] is True
    assert first["rows"][0] == [0, "2026-01-01"]
    assert toolbox.sources["Q1"]["sql"] == "SELECT safe" and toolbox.sources["Q1"]["total_rows"] == 80


def test_rejected_and_failed_queries_explain_why(monkeypatch):
    toolbox = make_toolbox()

    def reject(engine, sql):
        raise UnsafeSQL("financial_facts is not available.")

    monkeypatch.setattr(toolbox_module, "run_query", reject)
    assert toolbox.run("run_sql", {"sql": "x"}) == {"error": "Query rejected: financial_facts is not available."}

    def fail(engine, sql):
        raise QueryFailed('column "x" does not exist')

    monkeypatch.setattr(toolbox_module, "run_query", fail)
    assert "does not exist" in toolbox.run("run_sql", {"sql": "x"})["error"]
    assert toolbox.sources == {}                                           # failures are not citable sources


def hit(i):
    return Hit(i, "AAPL", "Item 1A", 2025, i, f"text {i}", 0.5, {"vector": 1})


def test_filing_search_tags_chunks_and_normalizes_filters(monkeypatch):
    seen = {}

    def fake_hybrid(engine, embedder, query, k, **filters):
        seen.update(query=query, k=k, **filters)
        return [hit(1), hit(2)]

    monkeypatch.setattr(toolbox_module, "hybrid_search", fake_hybrid)
    toolbox = make_toolbox()
    out = toolbox.run("search_filings", {"query": "supply chain", "ticker": "aapl", "fiscal_year": 2025.0, "section": "Item 1A"})
    assert [r["citation"] for r in out["results"]] == ["C1", "C2"] and "note" not in out
    assert seen["ticker"] == "AAPL" and seen["fiscal_year"] == 2025 and seen["section"] == "Item 1A"
    assert toolbox.sources["C2"]["text"] == "text 2" and toolbox.sources["C2"]["type"] == "filing"


def test_filing_search_falls_back_to_keywords_when_embeddings_are_out_of_quota(monkeypatch):
    def out_of_quota(*args, **kwargs):
        raise QuotaExceeded("daily limit")

    monkeypatch.setattr(toolbox_module, "hybrid_search", out_of_quota)
    monkeypatch.setattr(toolbox_module, "keyword_search", lambda engine, query, k, **f: [hit(7)])
    out = make_toolbox().run("search_filings", {"query": "tariffs"})
    assert out["results"][0]["citation"] == "C1" and "keyword search only" in out["note"]


def test_filing_search_input_problems_and_empty_results(monkeypatch):
    toolbox = make_toolbox()
    assert "required" in toolbox.run("search_filings", {"query": "  "})["error"]
    assert "section must be" in toolbox.run("search_filings", {"query": "x", "section": "Item 9"})["error"]
    monkeypatch.setattr(toolbox_module, "hybrid_search", lambda *a, **k: [])
    assert "No matching text" in toolbox.run("search_filings", {"query": "zzz"})["note"]


def test_citation_check_separates_real_cited_and_invented_tags():
    sources = {"Q1": {"type": "sql"}, "C1": {"type": "filing"}, "C2": {"type": "filing"}}
    cited, invented = check_citations("Revenue rose [Q1]. Risks exist [C2][C9]. Again [Q1].", sources)
    assert list(cited) == ["Q1", "C2"] and invented == ["C9"]
    assert check_citations("No sources here.", sources) == ({}, [])



def test_citation_check_understands_several_tags_in_one_bracket():
    sources = {"Q1": {}, "C1": {}, "C4": {}, "C5": {}}
    cited, invented = check_citations("Demand is strong [C1, C4]. Revenue grew [Q1][C5]. Odd [C9; C1] and [C].", sources)
    assert list(cited) == ["C1", "C4", "Q1", "C5"] and invented == ["C9"]