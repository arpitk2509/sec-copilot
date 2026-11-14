# One image runs both the API and the chat page. docker-compose.override.yml chooses the command for each.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# libgomp1 is the OpenMP runtime that LightGBM needs; libpq5 is the PostgreSQL client library.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1. Dependencies first. This slow layer is rebuilt only when pyproject.toml or uv.lock change.
COPY pyproject.toml uv.lock README* ./
RUN uv sync --frozen --no-dev --no-install-project

# 2. Then the code, and the one data file the forecaster reads at run time.
COPY src ./src
COPY models ./models
RUN uv sync --frozen --no-dev

# Run as an ordinary user, not root.
RUN useradd --create-home app
USER app

EXPOSE 8000 8501
CMD ["uvicorn", "sec_copilot.api.app:app", "--host", "0.0.0.0", "--port", "8000"]