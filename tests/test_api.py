import threading
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from sec_copilot.agent.agent import AgentResult
from sec_copilot.api.app import client_id, create_app
from sec_copilot.api.service import (
    PACIFIC,
    AskService,
    AssistantUnavailable,
    Busy,
    InvalidQuestion,
    RateLimited,
    RateLimiter,
    build_response,
)


def sql_source(rows=30):
    rows_list = [[80.68]] + [[i] for i in range(1, rows)]       # the first value is the margin the default answer quotes
    return {"type": "sql", "tag": "Q1", "sql": "SELECT 1", "columns": ["n"], "rows": rows_list, "total_rows": rows}


def result(answer="Margin was 80.68% [Q1].", **overrides):
    sources = {"Q1": sql_source(), "C1": {"type": "filing", "tag": "C1", "ticker": "MU", "fiscal_year": 2026,
                                          "section": "Item 7", "chunk_index": 3, "text": "Demand is strong."}}
    base = dict(answer=answer, sources=sources, model_calls=2, tool_calls=[("run_sql", {"sql": "SELECT 1"})],
                tool_outcomes=["30 rows"])
    return AgentResult(**{**base, **overrides})


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make_service(run_agent=None, **kwargs):
    return AskService(run_agent or (lambda question: result()), model_name="test-model", **kwargs)


def api(service):
    return TestClient(create_app(lambda: service), raise_server_exceptions=False)


# ---------- what the visitor receives ----------

def test_a_good_answer_carries_only_the_cited_sources_and_short_tables():
    response = api(make_service()).post("/ask", json={"question": "Which company has the highest margin?"})
    assert response.status_code == 200
    body = response.json()
    assert body["answer"].startswith("Margin was") and body["model_calls"] == 2 and body["warnings"] == []
    assert [s["tag"] for s in body["sources"]] == ["Q1"]                         # C1 was retrieved but not cited
    assert len(body["sources"][0]["rows"]) == 20 and body["sources"][0]["total_rows"] == 30
    assert body["tool_calls"] == [{"name": "run_sql", "args": {"sql": "SELECT 1"}, "outcome": "30 rows"}]


def test_numbers_that_no_source_supports_become_a_warning():
    shown = build_response(result("Margin was 99.99%, up 12.5% [Q1]."), 1.2)
    assert shown["warnings"] == ["These numbers do not appear in any source the assistant saw, so treat them with "
                                 "caution: 99.99%, 12.5%."]
    clean = build_response(result("The first row is 5 [Q1]."), 1.2)
    assert clean["warnings"] == []


def test_running_out_of_steps_is_reported():
    assert "used all of its steps" in build_response(result(hit_call_limit=True), 3.0)["warnings"][0]


# ---------- input checks ----------

def test_bad_questions_are_rejected_before_any_model_call():
    calls = []
    service = make_service(lambda q: calls.append(q) or result())
    client = api(service)
    assert client.post("/ask", json={}).status_code == 422
    assert client.post("/ask", json={"question": ""}).status_code == 422
    assert client.post("/ask", json={"question": "x" * 501}).status_code == 422
    blank = client.post("/ask", json={"question": "    "})
    assert blank.status_code == 422 and "type a question" in blank.json()["detail"]
    assert calls == []


# ---------- protecting the free quota ----------

def test_each_visitor_gets_a_few_questions_per_minute():
    clock = Clock()
    client = api(make_service(per_minute=2, clock=clock))
    def ask(who):
        return client.post("/ask", json={"question": "q"}, headers={"x-forwarded-for": who})
    assert [ask("1.1.1.1").status_code for _ in range(3)] == [200, 200, 429]
    assert ask("2.2.2.2").status_code == 200                                     # someone else is unaffected
    clock.now += 61                                                              # a minute later
    assert ask("1.1.1.1").status_code == 200


def test_the_daily_limit_stops_the_demo_and_resets_the_next_pacific_day():
    day = {"now": datetime(2026, 11, 10, 15, 0, tzinfo=PACIFIC)}
    service = make_service(daily_limit=2, per_minute=99, now=lambda: day["now"])
    client = api(service)
    assert [client.post("/ask", json={"question": "q"}).status_code for _ in range(3)] == [200, 200, 429]
    assert service.health()["questions_left_today"] == 0
    refusal = client.post("/ask", json={"question": "q"}).json()["detail"]
    assert "daily question limit" in refusal and "midnight Pacific" in refusal
    day["now"] += timedelta(days=1)
    assert client.post("/ask", json={"question": "q"}).status_code == 200
    assert service.health()["questions_left_today"] == 1


def test_only_one_question_uses_the_model_at_a_time_and_extra_visitors_are_told_it_is_busy():
    started, release = threading.Event(), threading.Event()

    def slow_agent(question):
        started.set()
        release.wait(timeout=5)
        return result()

    service = make_service(slow_agent, max_waiting=0, per_minute=99)
    first = threading.Thread(target=lambda: service.ask("first", "a"))
    first.start()
    assert started.wait(timeout=5)
    with pytest.raises(Busy):
        service.ask("second", "b")
    release.set()
    first.join(timeout=5)
    assert service.ask("third", "c")["answer"]                                   # free again afterwards


def test_the_rate_limiter_forgets_old_events():
    clock = Clock()
    limiter = RateLimiter(2, 60, clock)
    limiter.check("x"), limiter.check("x")
    with pytest.raises(RateLimited):
        limiter.check("x")
    clock.now += 61
    limiter.check("x")


# ---------- errors the visitor sees, and ones they must not ----------

def test_known_problems_use_their_own_status_and_message():
    def unavailable(question):
        raise AssistantUnavailable("The language model is unavailable right now.")

    response = api(make_service(unavailable)).post("/ask", json={"question": "q"})
    assert response.status_code == 503 and "unavailable" in response.json()["detail"]


def test_unexpected_errors_never_leak_internal_details():
    def broken(question):
        raise RuntimeError("password=hunter2 at /srv/db")

    response = api(make_service(broken)).post("/ask", json={"question": "q"})
    assert response.status_code == 500
    assert "hunter2" not in response.text and "srv" not in response.text
    assert response.json()["detail"].startswith("Something went wrong")


def test_the_service_is_built_lazily_and_health_reports_the_basics():
    built = []

    def factory():
        built.append(1)
        return make_service(data_as_of=lambda: "2026-09-30", daily_limit=50)

    client = TestClient(create_app(factory))
    assert built == []                                                           # importing the app connects to nothing
    health = client.get("/health").json()
    assert health == {"status": "ok", "model": "test-model", "questions_left_today": 50, "daily_limit": 50,
                      "data_as_of": "2026-09-30"}
    client.get("/health")
    assert built == [1]


def test_the_visitor_address_comes_from_the_proxy_header_when_present():
    class Fake:
        def __init__(self, headers, host):
            self.headers, self.client = headers, type("C", (), {"host": host})()

    assert client_id(Fake({"x-forwarded-for": "9.9.9.9, 10.0.0.1"}, "10.0.0.1")) == "9.9.9.9"
    assert client_id(Fake({}, "127.0.0.1")) == "127.0.0.1"


def test_service_errors_carry_their_status():
    assert InvalidQuestion("x").status_code == 422 and Busy("x").status_code == 503


def test_the_bare_address_sends_visitors_to_the_documentation():
    response = api(make_service()).get("/", follow_redirects=False)
    assert response.status_code in (302, 307) and response.headers["location"] == "/docs"