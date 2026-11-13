"""The chat page's connection to the API. The page never talks to the model or the database itself."""
import os

import httpx

API_URL = os.getenv("API_URL", "http://localhost:8000")


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


def ask(question: str, base_url: str | None = None, timeout: float = 150.0) -> dict:
    base_url = base_url or API_URL
    try:
        response = httpx.post(f"{base_url}/ask", json={"question": question}, timeout=timeout)
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