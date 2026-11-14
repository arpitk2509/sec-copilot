"""The web API. Run it with: uvicorn sec_copilot.api.app:app --port 8000"""
import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from sec_copilot.api.service import MAX_QUESTION_CHARS, AskService, AssistantUnavailable, ServiceError

logger = logging.getLogger("sec_copilot.api")


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS, examples=["Which company had the highest operating margin?"])


class AskResponse(BaseModel):
    answer: str
    sources: list[dict[str, Any]]
    warnings: list[str]
    tool_calls: list[dict[str, Any]]
    model_calls: int
    seconds: float


def build_default_service() -> AskService:
    """Connect to the database and the model. Called once, on the first request, so importing this file is cheap."""
    import os

    from sqlalchemy import create_engine, text

    from sec_copilot import config
    from sec_copilot.agent.agent import answer_question
    from sec_copilot.agent.toolbox import Toolbox
    from sec_copilot.llm.client import Gemini, LLMUnavailable
    from sec_copilot.ml.forecast import RevenueForecaster
    from sec_copilot.rag.embeddings import GeminiEmbedder

    engine = create_engine(config.DATABASE_URL, pool_pre_ping=True)
    readonly = create_engine(config.READONLY_DATABASE_URL, pool_pre_ping=True)
    gemini, embedder, forecaster = Gemini(), GeminiEmbedder(), RevenueForecaster(readonly)

    def run_agent(question: str):
        try:
            return answer_question(question, gemini, Toolbox(engine, readonly, embedder, forecaster))
        except LLMUnavailable as error:
            raise AssistantUnavailable("The language model is unavailable right now (often a daily quota). "
                                       "Please try again later.") from error

    def data_as_of():
        with readonly.connect() as connection:
            return str(connection.execute(text("SELECT max(period_end) FROM v_quarterly_metrics")).scalar())

    return AskService(run_agent, model_name=gemini.model, data_as_of=data_as_of,
                      daily_limit=int(os.getenv("DAILY_QUESTION_LIMIT", "100")),
                      per_minute=int(os.getenv("RATE_LIMIT_PER_MINUTE", "3")))


def client_id(request: Request) -> str:
    """Who is asking? Behind a proxy the real address is the first entry of X-Forwarded-For."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def create_app(service_factory=build_default_service) -> FastAPI:
    app = FastAPI(title="SEC Copilot", version="1.0",
                  description="Ask questions about 15 US tech companies. Answers cite SQL results, 10-K excerpts or forecasts.")
    holder: dict[str, AskService] = {}

    def service() -> AskService:
        if "service" not in holder:
            holder["service"] = service_factory()
        return holder["service"]

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/docs")                  # visiting the bare address shows the interactive documentation

    @app.post("/ask", response_model=AskResponse)
    def ask(body: AskRequest, request: Request):
        try:
            return service().ask(body.question, client_id(request))
        except ServiceError as error:
            raise HTTPException(status_code=error.status_code, detail=error.message) from error
        except Exception:
            logger.exception("Unexpected error while answering")
            raise HTTPException(status_code=500, detail="Something went wrong on our side. Please try again.") from None

    @app.get("/health")
    def health():
        try:
            return service().health()
        except Exception:
            logger.exception("Health check failed")
            raise HTTPException(status_code=503, detail="The service is not ready.") from None

    return app


app = create_app()