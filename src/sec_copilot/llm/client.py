"""Shared setup for talking to Gemini: which model, which key, and a polite pause between calls."""
import os
import time

from google import genai
from google.genai import errors, types

from sec_copilot import config  # noqa: F401  (importing it loads your .env file)

DEFAULT_MODEL = "gemini-2.5-flash"
MIN_SECONDS_BETWEEN_CALLS = 6.0   # free tiers allow only a handful of requests per minute
MAX_ATTEMPTS = 3
RETRY_WAIT_SECONDS = 20           # waits 20s, then 40s


class LLMUnavailable(Exception):
    """The model refused or was unavailable even after retries (usually a quota). Try again later."""


def model_name() -> str:
    """Set LLM_MODEL in .env to change it. Use check_models to see what your key can use."""
    return os.environ.get("LLM_MODEL", DEFAULT_MODEL)


def make_client(api_key: str | None = None):
    api_key = api_key or os.environ.get("LLM_API_KEY")
    if not api_key:
        raise RuntimeError("Set LLM_API_KEY in your .env file (a Google AI Studio key).")
    return genai.Client(api_key=api_key)


class Gemini:
    """One method, generate(): send a conversation, get the model's reply. Handles pacing and retries."""

    def __init__(self, client=None, model=None, sleep=time.sleep, clock=time.monotonic):
        self._client = client or make_client()
        self.model = model or model_name()
        self._sleep = sleep
        self._clock = clock
        self._last_call = None
        self.calls_made = 0

    def generate(self, contents, system_instruction=None, tools=None):
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=tools,
            temperature=0.0,                                            # same question, same behaviour
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),  # we run tools ourselves
        )
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._pace()
            try:
                response = self._client.models.generate_content(model=self.model, contents=contents, config=config)
                self.calls_made += 1
                return response
            except errors.APIError as error:
                if error.code not in (429, 500, 503):
                    raise                                              # bad key, unknown model, bad request: don't retry
                if attempt == MAX_ATTEMPTS:
                    raise LLMUnavailable(f"API error {error.code} after {MAX_ATTEMPTS} attempts") from error
                self._sleep(RETRY_WAIT_SECONDS * attempt)

    def _pace(self):
        """Keep a minimum gap between calls so we stay under the per-minute limit."""
        now = self._clock()
        if self._last_call is not None:
            wait = MIN_SECONDS_BETWEEN_CALLS - (now - self._last_call)
            if wait > 0:
                self._sleep(wait)
        self._last_call = self._clock()