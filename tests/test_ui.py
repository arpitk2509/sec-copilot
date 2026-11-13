from pathlib import Path

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from sec_copilot.ui import api_client
from sec_copilot.ui.api_client import ApiProblem

APP = str(Path(__file__).parents[1] / "src" / "sec_copilot" / "ui" / "app.py")

PAYLOAD = {
    "answer": "Micron's revenue was $54.2 billion, up 379.27% [Q1]. Demand is strong [C1]. Forecast $64.3 billion [F1].",
    "sources": [
        {"type": "sql", "tag": "Q1", "sql": "SELECT revenue FROM v_quarterly_metrics", "columns": ["revenue"],
         "rows": [[54229000000]], "total_rows": 40},
        {"type": "filing", "tag": "C1", "ticker": "MU", "fiscal_year": 2026, "section": "Item 7", "chunk_index": 3,
         "text": "AI demand is outpacing supply, with prices of $10 and $20 rising."},
        {"type": "forecast", "tag": "F1", "ticker": "MU", "model": "ets", "summary": "Forecast: $64.3 billion to $88.6 billion.",
         "caveat": "A statistical estimate."},
    ],
    "warnings": ["These numbers do not appear in any source: 12.5%."],
    "tool_calls": [{"name": "run_sql", "args": {}, "outcome": "1 rows"}],
    "model_calls": 2, "seconds": 7.4,
}
HEALTH = {"status": "ok", "model": "test-model", "questions_left_today": 42, "daily_limit": 100, "data_as_of": "2026-09-30"}


@pytest.fixture
def asked(monkeypatch):
    """Replace the real API with a fake that records the questions it receives."""
    questions = []
    monkeypatch.setattr(api_client, "health", lambda *a, **k: HEALTH)
    monkeypatch.setattr(api_client, "ask", lambda q, *a, **k: questions.append(q) or PAYLOAD)
    return questions


def page():
    return AppTest.from_file(APP, default_timeout=15).run()


def texts(elements):
    return " ".join(str(e.value) for e in elements)


def test_the_page_loads_with_examples_and_data_freshness(asked):
    at = page()
    assert not at.exception and at.title[0].value == "SEC Copilot"
    assert "Data as of 2026-09-30" in texts(at.sidebar.caption) and "42 questions left today" in texts(at.sidebar.caption)
    assert len(at.sidebar.button) == 5 and not at.chat_message


def test_an_answer_shows_text_warnings_and_every_kind_of_source(asked):
    at = page()
    at.chat_input[0].set_value("Tell me about Micron").run()
    assert not at.exception and asked == ["Tell me about Micron"]
    assert [m.name for m in at.chat_message] == ["user", "assistant"]
    assert "\\$54.2 billion" in texts(at.markdown) and "\\$64.3 billion" in texts(at.markdown)     # dollars escaped
    assert "do not appear in any source: 12.5%" in texts(at.warning)
    titles = [e.label for e in at.expander]
    assert "[Q1] SQL result (40 rows)" in titles and "[C1] MU FY2026 Item 7 (filing excerpt)" in titles
    assert "[F1] Forecast for MU (ets model)" in titles and "What the assistant did" in titles
    assert "Showing the first 1 of 40 rows." in texts(at.caption)
    assert "\\$10 and \\$20" in texts(at.markdown)                                                  # excerpts escaped too
    assert at.code[0].value.startswith("SELECT revenue")


def test_clicking_an_example_asks_that_question(asked):
    at = page()
    at.sidebar.button[3].click().run()
    assert asked == ["Forecast Apple's revenue for its next quarter."]
    assert at.chat_message[0].markdown[0].value.startswith("Forecast Apple")


def test_limits_are_shown_as_friendly_notes_and_faults_as_errors(monkeypatch):
    monkeypatch.setattr(api_client, "health", lambda *a, **k: HEALTH)

    def limited(question, *a, **k):
        raise ApiProblem("The demo has reached its daily question limit.", level="info")

    monkeypatch.setattr(api_client, "ask", limited)
    at = page()
    at.chat_input[0].set_value("q").run()
    assert not at.exception and "daily question limit" in texts(at.info) and not at.error

    def broken(question, *a, **k):
        raise ApiProblem("Cannot reach the API.")

    monkeypatch.setattr(api_client, "ask", broken)
    at = page()
    at.chat_input[0].set_value("q").run()
    assert "Cannot reach the API" in texts(at.error)


def test_the_page_survives_an_unreachable_api(monkeypatch):
    monkeypatch.setattr(api_client, "health", lambda *a, **k: None)
    at = page()
    assert not at.exception and "API is not reachable" in texts(at.sidebar.caption)


# ---------- the API client itself ----------

class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


def test_the_client_maps_each_kind_of_answer(monkeypatch):
    def reply(status, body):
        monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResponse(status, body))

    reply(200, {"answer": "ok"})
    assert api_client.ask("q") == {"answer": "ok"}
    for status, level in ((429, "info"), (503, "info"), (422, "info"), (500, "error")):
        reply(status, {"detail": "message"})
        with pytest.raises(ApiProblem) as caught:
            api_client.ask("q")
        assert caught.value.level == level and caught.value.message == "message"
    reply(422, {"detail": [{"msg": "too long"}]})
    with pytest.raises(ApiProblem) as caught:
        api_client.ask("q")
    assert "between 1 and 500" in caught.value.message
    reply(502, None)
    with pytest.raises(ApiProblem) as caught:
        api_client.ask("q")
    assert "502" in caught.value.message


def test_the_client_explains_an_unreachable_api_and_health_degrades_quietly(monkeypatch):
    def down(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", down)
    monkeypatch.setattr(httpx, "get", down)
    with pytest.raises(ApiProblem) as caught:
        api_client.ask("q", base_url="http://localhost:9")
    assert "Cannot reach the API at http://localhost:9" in caught.value.message
    assert api_client.health("http://localhost:9") is None