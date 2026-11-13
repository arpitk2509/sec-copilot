"""Answer questions for the API: run the agent, check the answer, and protect the free-tier quota."""
import threading
import time
from collections import defaultdict, deque
from datetime import datetime
from zoneinfo import ZoneInfo

from sec_copilot.agent.agent import AgentResult, check_citations
from sec_copilot.agent.verify import unsupported_numbers

MAX_QUESTION_CHARS = 500
MAX_ROWS_SHOWN = 20
PACIFIC = ZoneInfo("America/Los_Angeles")      # Google's daily quotas reset at midnight Pacific time


class ServiceError(Exception):
    """A problem the visitor should be told about. Each kind carries the HTTP status it maps to."""

    status_code = 500

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class InvalidQuestion(ServiceError):
    status_code = 422


class RateLimited(ServiceError):
    status_code = 429


class DailyLimitReached(ServiceError):
    status_code = 429


class Busy(ServiceError):
    status_code = 503


class AssistantUnavailable(ServiceError):
    status_code = 503


class RateLimiter:
    """At most `max_events` per `window_seconds` for each visitor."""

    def __init__(self, max_events: int, window_seconds: float, clock=time.monotonic):
        self.max_events, self.window, self._clock = max_events, window_seconds, clock
        self._events: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = self._clock()
        with self._lock:
            events = self._events[key]
            while events and now - events[0] > self.window:
                events.popleft()
            if len(events) >= self.max_events:
                raise RateLimited(f"Too many questions too quickly. Please wait about {int(self.window)} seconds and try again.")
            events.append(now)


def shorten(source: dict) -> dict:
    """A source as the visitor sees it: long SQL results are cut to the first rows."""
    if source["type"] == "sql":
        return {**source, "rows": source["rows"][:MAX_ROWS_SHOWN]}
    return source


def build_response(result: AgentResult, seconds: float) -> dict:
    cited, _ = check_citations(result.answer, result.sources)
    unsupported = unsupported_numbers(result.answer, result.sources)
    warnings = []
    if unsupported:
        warnings.append("These numbers do not appear in any source the assistant saw, so treat them with caution: "
                        + ", ".join(unsupported) + ".")
    if result.hit_call_limit:
        warnings.append("The assistant used all of its steps and answered with what it had gathered so far.")
    return {
        "answer": result.answer,
        "sources": [shorten(source) for source in cited.values()],
        "warnings": warnings,
        "tool_calls": [{"name": name, "args": args, "outcome": outcome}
                       for (name, args), outcome in zip(result.tool_calls, result.tool_outcomes, strict=False)],
        "model_calls": result.model_calls,
        "seconds": round(seconds, 1),
    }


class AskService:
    def __init__(self, run_agent, model_name: str = "unknown", data_as_of=None, daily_limit: int = 100,
                 per_minute: int = 3, max_waiting: int = 2, clock=time.monotonic,
                 now=lambda: datetime.now(PACIFIC)):
        self._run_agent = run_agent                      # question -> AgentResult
        self.model_name, self._data_as_of = model_name, data_as_of
        self.daily_limit, self._now, self._clock = daily_limit, now, clock
        self._limiter = RateLimiter(per_minute, 60, clock)
        self._max_in_flight = 1 + max_waiting
        self._in_flight = 0
        self._counter_lock = threading.Lock()
        self._agent_lock = threading.Lock()              # the model is used by one question at a time
        self._day, self._asked_today = now().date(), 0

    def _questions_today(self) -> int:
        if self._now().date() != self._day:              # a new day (Pacific time): the quota has reset
            self._day, self._asked_today = self._now().date(), 0
        return self._asked_today

    def health(self) -> dict:
        return {"status": "ok", "model": self.model_name,
                "questions_left_today": max(self.daily_limit - self._questions_today(), 0),
                "daily_limit": self.daily_limit, "data_as_of": self._data_as_of() if self._data_as_of else None}

    def ask(self, question: str, client: str = "anonymous") -> dict:
        question = (question or "").strip()
        if not question:
            raise InvalidQuestion("Please type a question.")
        if len(question) > MAX_QUESTION_CHARS:
            raise InvalidQuestion(f"Please keep the question under {MAX_QUESTION_CHARS} characters.")
        self._limiter.check(client)

        with self._counter_lock:
            if self._questions_today() >= self.daily_limit:
                raise DailyLimitReached("The demo has reached its daily question limit. It resets at midnight Pacific "
                                        "time. Meanwhile you can read the evaluation results in the project README.")
            if self._in_flight >= self._max_in_flight:
                raise Busy("The assistant is answering other questions. Please try again in a minute.")
            self._in_flight += 1
            self._asked_today += 1
        try:
            with self._agent_lock:
                started = self._clock()
                result = self._run_agent(question)
                return build_response(result, self._clock() - started)
        finally:
            with self._counter_lock:
                self._in_flight -= 1