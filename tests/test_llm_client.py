import httpx
import pytest
from google.genai import errors, types

from sec_copilot.llm.client import MAX_ATTEMPTS, MIN_SECONDS_BETWEEN_CALLS, Gemini, LLMUnavailable
from sec_copilot.llm.smoke_test import run


def text_reply(text):
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=[types.Part(text=text)]))])


def tool_reply(name, args):
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))]))])


class FakeClient:
    """Plays back a script of replies. A reply that is an Exception is raised instead."""

    def __init__(self, script):
        self.script, self.calls = list(script), []
        self.models = self

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def api_error(code):
    return errors.APIError(code, {"error": {"message": "x", "status": "X"}})


class Clock:
    """A fake clock whose sleep() moves time forward, so pacing can be tested instantly."""

    def __init__(self):
        self.now, self.sleeps = 100.0, []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def make(script, model="test-model"):
    clock = Clock()
    return Gemini(FakeClient(script), model=model, sleep=clock.sleep, clock=clock.time), clock


def test_generate_sends_model_temperature_and_disables_auto_tool_running():
    gemini, _ = make([text_reply("hi")])
    gemini.generate("hello", system_instruction="be brief")
    sent = gemini._client.calls[0]
    assert sent["model"] == "test-model"
    assert sent["config"].temperature == 0.0
    assert sent["config"].system_instruction == "be brief"
    assert sent["config"].automatic_function_calling.disable is True


def test_calls_are_spaced_out():
    gemini, clock = make([text_reply("a"), text_reply("b")])
    gemini.generate("one")
    gemini.generate("two")
    assert clock.sleeps == [MIN_SECONDS_BETWEEN_CALLS]


def test_rate_limit_is_retried_then_succeeds():
    gemini, clock = make([api_error(429), text_reply("ok")])
    assert gemini.generate("x").text == "ok"
    assert 20 in clock.sleeps and gemini.calls_made == 1


def test_gives_up_after_max_attempts():
    gemini, _ = make([api_error(503)] * MAX_ATTEMPTS)
    with pytest.raises(LLMUnavailable):
        gemini.generate("x")


def test_unknown_model_is_not_retried():
    gemini, _ = make([api_error(404)])
    with pytest.raises(errors.APIError):
        gemini.generate("x")
    assert len(gemini._client.calls) == 1


def test_smoke_test_walks_through_plain_answer_tool_call_and_final_answer():
    gemini, _ = make([text_reply("OK"), tool_reply("run_sql", {"sql": "SELECT count(*) FROM companies"}),
                      text_reply("There are 15 companies.")])
    logs = []
    assert run(gemini, log=logs.append) is True
    third_call = gemini._client.calls[2]["contents"]
    assert third_call[1].role == "model"                                   # the model's own message is replayed
    assert third_call[2].parts[0].function_response.response == {"rows": [[15]]}
    assert any("15 companies" in line for line in logs)


def test_smoke_test_reports_when_the_model_ignores_the_tool():
    gemini, _ = make([text_reply("OK"), text_reply("I think there are about ten.")])
    logs = []
    assert run(gemini, log=logs.append) is False
    assert any("did NOT ask" in line for line in logs)



def test_a_dropped_connection_is_retried_then_succeeds():
    gemini, clock = make([httpx.RemoteProtocolError("Server disconnected without sending a response."), text_reply("ok")])
    assert gemini.generate("x").text == "ok"
    assert 20 in clock.sleeps


def test_repeated_dropped_connections_end_as_llm_unavailable():
    gemini, _ = make([httpx.ReadTimeout("slow")] * MAX_ATTEMPTS)
    with pytest.raises(LLMUnavailable) as caught:
        gemini.generate("x")
    assert "ReadTimeout" in str(caught.value)



