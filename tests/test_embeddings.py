import math
from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors

from sec_copilot.rag.embed_chunks import estimate_tokens
from sec_copilot.rag.embeddings import (
    EMBEDDING_DIMENSIONS,
    MAX_ATTEMPTS,
    GeminiEmbedder,
    QuotaExceeded,
    normalize,
    to_pgvector,
)


def fake_response(count, dims=EMBEDDING_DIMENSIONS):
    return SimpleNamespace(embeddings=[SimpleNamespace(values=[3.0] + [4.0] * (dims - 1)) for _ in range(count)])


class FakeClient:
    """Stands in for the Google client. 'script' is a list of results: an exception to raise, or 'ok'."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.models = self

    def embed_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return fake_response(len(contents))


def api_error(code):
    return errors.APIError(code, {"error": {"message": "x", "status": "X"}})


def test_normalize_makes_length_one_and_handles_zero():
    vector = normalize([3.0, 4.0])
    assert vector == pytest.approx([0.6, 0.8])
    assert normalize([0.0, 0.0]) == [0.0, 0.0]


def test_to_pgvector_format():
    assert to_pgvector([0.5, -1.0, 0.25]) == "[0.5,-1,0.25]"


def test_documents_and_queries_use_different_task_types_and_768_dimensions():
    client = FakeClient(["ok", "ok"])
    embedder = GeminiEmbedder(client=client)
    vectors = embedder.embed_documents(["a", "b"])
    query = embedder.embed_query("question")
    assert len(vectors) == 2 and len(vectors[0]) == EMBEDDING_DIMENSIONS and len(query) == EMBEDDING_DIMENSIONS
    assert client.calls[0]["config"].task_type == "RETRIEVAL_DOCUMENT"
    assert client.calls[1]["config"].task_type == "RETRIEVAL_QUERY"
    assert client.calls[0]["config"].output_dimensionality == 768
    assert math.isclose(math.sqrt(sum(x * x for x in vectors[0])), 1.0)     # normalized


def test_rate_limit_is_retried_with_growing_waits():
    waits = []
    client = FakeClient([api_error(429), api_error(503), "ok"])
    vectors = GeminiEmbedder(client=client, sleep=waits.append).embed_documents(["a"])
    assert len(vectors) == 1 and len(client.calls) == 3
    assert waits == [30, 60]


def test_gives_up_with_quota_exceeded_after_max_attempts():
    client = FakeClient([api_error(429)] * MAX_ATTEMPTS)
    with pytest.raises(QuotaExceeded):
        GeminiEmbedder(client=client, sleep=lambda s: None).embed_documents(["a"])
    assert len(client.calls) == MAX_ATTEMPTS


def test_a_bad_key_is_not_retried():
    client = FakeClient([api_error(400)])
    with pytest.raises(errors.APIError):
        GeminiEmbedder(client=client, sleep=lambda s: None).embed_documents(["a"])
    assert len(client.calls) == 1


def test_wrong_vector_size_is_rejected():
    class Wrong(FakeClient):
        def embed_content(self, *, model, contents, config):
            return fake_response(len(contents), dims=10)

    with pytest.raises(RuntimeError):
        GeminiEmbedder(client=Wrong([])).embed_documents(["a"])


def test_token_estimate_is_pessimistic():
    assert estimate_tokens(["x" * 3200]) == 1000



def test_a_dropped_connection_is_retried_like_a_rate_limit():
    waits = []
    client = FakeClient([httpx.RemoteProtocolError("Server disconnected without sending a response."), "ok"])
    assert len(GeminiEmbedder(client=client, sleep=waits.append).embed_documents(["a"])) == 1
    assert waits == [30]