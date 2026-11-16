"""The chat page's connection to the API. The page never talks to the model or the database itself."""
import os

import httpx

API_URL = os.getenv("API_URL", "http://127.0.0.1:8010")


class ApiProblem(Exception):
    """Something the visitor should read. level is 'info' for limits and busy messages, 'error' for real faults."""

    def __init__(self, message: str, level: str = "error"):
        super().__init__(message)
        self.message, self.level = message, level


def _detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    if isinstance(detail, list):                      # the framework's own input-validation messages
        return "Please type a question between 1 and 500 characters."
    return detail or f"The service answered with an error ({response.status_code})."


def visitor_from_headers(headers) -> str | None:
    """The visitor's address as recorded by the proxy in front of this page: the last X-Forwarded-For entry."""
    forwarded = (headers or {}).get("X-Forwarded-For", "")
    return forwarded.split(",")[-1].strip() or None


def ask(question: str, base_url: str | None = None, timeout: float = 150.0, visitor: str | None = None) -> dict:
    base_url = base_url or API_URL
    secret = os.getenv("UI_SHARED_SECRET")
    headers = {"x-ui-secret": secret, "x-visitor-id": visitor} if (secret and visitor) else {}
    try:
        response = httpx.post(f"{base_url}/ask", json={"question": question}, headers=headers, timeout=timeout)
    except httpx.HTTPError as error:
        raise ApiProblem(f"Cannot reach the API at {base_url}. Is it running? ({type(error).__name__})") from error
    if response.status_code == 200:
        return response.json()
    raise ApiProblem(_detail(response), level="info" if response.status_code in (422, 429, 503) else "error")


def health(base_url: str | None = None) -> dict | None:
    try:
        response = httpx.get(f"{base_url or API_URL}/health", timeout=5)
        return response.json() if response.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None