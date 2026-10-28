import json

from sec_copilot.agent import toolbox as toolbox_module
from sec_copilot.agent.agent import MAX_MODEL_CALLS, answer_question, render_sources
from sec_copilot.agent.prompts import FINAL_ANSWER_NOTICE
from sec_copilot.eval.compare import compare, load, pass_counts
from tests.test_agent import FakeToolbox, ScriptedGemini, hit, make_toolbox, text_reply, tool_reply


class ToolboxWithResults(FakeToolbox):
    """A fake toolbox whose sources have the full shape that render_sources needs."""

    def __init__(self):
        super().__init__()
        self.sources = {"Q1": {"type": "sql", "tag": "Q1", "sql": "SELECT 1", "columns": ["n"], "rows": [[1]],
                               "total_rows": 1}}


def test_the_last_call_is_a_fresh_message_with_the_results_and_no_tools():
    script = [tool_reply(("run_sql", {"sql": f"SELECT {i}"})) for i in range(MAX_MODEL_CALLS - 1)] + [text_reply("Best effort [Q1].")]
    gemini = ScriptedGemini(script)
    result = answer_question("What is one?", gemini, ToolboxWithResults())
    assert result.model_calls == MAX_MODEL_CALLS == len(gemini.calls)
    assert all(call["tools"] is not None for call in gemini.calls[:-1])

    last = gemini.calls[-1]
    assert last["tools"] is None
    assert len(last["contents"]) == 1 and last["contents"][0].role == "user"      # no tool-call history replayed
    assert all(p.function_call is None and p.function_response is None for p in last["contents"][0].parts)
    prompt = last["contents"][0].parts[0].text
    assert "What is one?" in prompt and "[Q1] SQL result" in prompt and FINAL_ANSWER_NOTICE.strip() in prompt
    assert result.answer == "Best effort [Q1]." and result.hit_call_limit is True


def test_an_empty_final_answer_gets_a_fallback():
    script = [tool_reply(("run_sql", {"sql": "x"})) for _ in range(MAX_MODEL_CALLS - 1)] + [text_reply("")]
    result = answer_question("q", ScriptedGemini(script), ToolboxWithResults())
    assert result.answer == "I could not produce an answer."
    assert len(result.tool_calls) == MAX_MODEL_CALLS - 1


def test_with_a_budget_of_one_call_the_model_answers_without_tools():
    gemini = ScriptedGemini([text_reply("Directly.")])
    result = answer_question("q", gemini, ToolboxWithResults(), max_model_calls=1)
    assert result.answer == "Directly." and gemini.calls[0]["tools"] is None and result.hit_call_limit is False


def test_render_sources_shows_sql_rows_and_filing_excerpts():
    text = render_sources({
        "Q1": {"type": "sql", "sql": "SELECT a, b", "columns": ["a", "b"], "rows": [[1, "x"], [2, "y"]], "total_rows": 2},
        "C1": {"type": "filing", "ticker": "NVDA", "fiscal_year": 2026, "section": "Item 1A", "text": "Export controls hurt."},
    })
    assert "[Q1] SQL result (2 rows): SELECT a, b" in text and "columns: a, b" in text and "1 | x" in text
    assert "[C1] NVDA FY2026 Item 1A excerpt: Export controls hurt." in text
    assert render_sources({}) == ""


def test_filing_search_relaxes_guessed_filters_but_never_the_ticker(monkeypatch):
    seen = []

    def fake_hybrid(engine, embedder, query, k, **filters):
        seen.append(dict(filters))
        return [hit(1)] if filters["fiscal_year"] is None and filters["section"] is None else []

    monkeypatch.setattr(toolbox_module, "hybrid_search", fake_hybrid)
    out = make_toolbox().run("search_filings", {"query": "x", "ticker": "NVDA", "fiscal_year": 2025, "section": "Item 7"})
    assert [f["fiscal_year"] for f in seen] == [2025, None, None] and [f["section"] for f in seen] == ["Item 7", "Item 7", None]
    assert all(f["ticker"] == "NVDA" for f in seen)
    assert out["results"][0]["citation"] == "C1" and "fiscal_year and section filter" in out["note"]


def test_filing_search_with_no_matches_anywhere_keeps_the_ticker_and_says_so(monkeypatch):
    seen = []
    monkeypatch.setattr(toolbox_module, "hybrid_search", lambda e, em, q, k, **f: seen.append(f) or [])
    out = make_toolbox().run("search_filings", {"query": "x", "ticker": "NVDA", "fiscal_year": 2025})
    assert out["results"] == [] and "No matching text" in out["note"]
    assert all(f["ticker"] == "NVDA" for f in seen)


def test_comparing_runs_uses_pass_rates_across_repeated_runs(tmp_path):
    def write(name, passed_by_id):
        path = tmp_path / name
        path.write_text(json.dumps({"model": "m", "results": [{"id": k, "passed": v} for k, v in passed_by_id.items()]}))
        return load(str(path))

    old = [write("o1.json", {"a": True, "b": False, "c": True}), write("o2.json", {"a": True, "b": False, "c": True})]
    new = [write("n1.json", {"a": True, "b": True, "c": False, "d": True}), write("n2.json", {"a": True, "b": False, "c": False, "d": True})]
    assert pass_counts(old) == {"a": (2, 2), "b": (0, 2), "c": (2, 2)}
    text = "\n".join(compare(old, new))
    assert "b" in text and "<- better" in text and "<- WORSE" in text
    assert "before: 67% over 2 run(s)" in text and "better: 1   worse: 1" in text