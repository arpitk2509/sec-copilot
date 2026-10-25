"""Turn text into vectors with Google's Gemini embedding model."""
import math
import os
import time

from google import genai
from google.genai import errors, types

EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIMENSIONS = 768

MAX_ATTEMPTS = 4
RETRY_WAIT_SECONDS = 30   # waits 30s, 60s, 90s between attempts


class QuotaExceeded(Exception):
    """The API refused or was unavailable even after retries. Saved progress is kept, so try again later."""


def normalize(vector: list[float]) -> list[float]:
    """Scale a vector to length 1. Google only pre-normalizes the full 3072-number version."""
    length = math.sqrt(sum(x * x for x in vector))
    return [x / length for x in vector] if length else list(vector)


def to_pgvector(vector: list[float]) -> str:
    """Postgres reads a vector from text like '[0.1,0.2,0.3]'."""
    return "[" + ",".join(f"{x:.7g}" for x in vector) + "]"


class GeminiEmbedder:
    def __init__(self, api_key: str | None = None, client=None, sleep=time.sleep):
        if client is None:
            api_key = api_key or os.environ.get("LLM_API_KEY")
            if not api_key:
                raise RuntimeError("Set LLM_API_KEY in your .env file (a Google AI Studio key).")
            client = genai.Client(api_key=api_key)
        self._client = client
        self._sleep = sleep

    def _embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        config = types.EmbedContentConfig(task_type=task_type, output_dimensionality=EMBEDDING_DIMENSIONS)
        response = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = self._client.models.embed_content(
                    model=EMBEDDING_MODEL, contents=texts, config=config
                )
                break
            except errors.APIError as error:
                if error.code not in (429, 500, 503):
                    raise                                   # a real mistake (bad key, bad request): don't retry
                if attempt == MAX_ATTEMPTS:
                    raise QuotaExceeded(f"API error {error.code} after {MAX_ATTEMPTS} attempts") from error
                self._sleep(RETRY_WAIT_SECONDS * attempt)

        vectors = [normalize(list(e.values)) for e in response.embeddings]
        if len(vectors) != len(texts) or any(len(v) != EMBEDDING_DIMENSIONS for v in vectors):
            raise RuntimeError("The API returned a different number or size of vectors than expected.")
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """For chunks that will be searched. Google asks for a different task type than for questions."""
        return self._embed(texts, "RETRIEVAL_DOCUMENT")

    def embed_query(self, text: str) -> list[float]:
        """For a user's question."""
        return self._embed([text], "RETRIEVAL_QUERY")[0]


if __name__ == "__main__":
    from sec_copilot import config  # noqa: F401  (loads .env)

    vector = GeminiEmbedder().embed_query("What are Apple's supply chain risks?")
    print(f"OK: {EMBEDDING_MODEL} returned {len(vector)} numbers, length {math.sqrt(sum(x * x for x in vector)):.3f}")