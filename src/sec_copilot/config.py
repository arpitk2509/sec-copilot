import os

from dotenv import load_dotenv

load_dotenv()  # reads your private .env file from the project root

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5433/sec_copilot",
)


def require_user_agent() -> str:
    """Return the SEC identity string, or stop with a clear message if it isn't set."""
    ua = os.getenv("SEC_USER_AGENT", "").strip()
    if not ua or "example.com" in ua:
        raise RuntimeError("Set SEC_USER_AGENT in .env to: Your Name your.real.email@domain.com")
    return ua