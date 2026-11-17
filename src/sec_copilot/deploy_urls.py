"""Turn the cloud database's connection string into the two URLs the application needs, and test them.

The secrets come from environment variables, never from the command line (which shell history would keep):

    NEON_URL='postgresql://...' RO_PASSWORD='...' uv run python -m sec_copilot.deploy_urls database
    NEON_URL='postgresql://...' RO_PASSWORD='...' uv run python -m sec_copilot.deploy_urls readonly
    NEON_URL='postgresql://...' RO_PASSWORD='...' uv run python -m sec_copilot.deploy_urls check

`database` and `readonly` print one URL each (pipe it to `pbcopy` to paste it into the hosting dashboard).
`check` connects with both and verifies the data and the read-only role's limits.
"""
import os
import sys
from urllib.parse import quote, urlsplit, urlunsplit

READONLY_USER = "copilot_readonly"
SCHEME = "postgresql+psycopg"          # the form SQLAlchemy needs; Neon hands out plain postgresql://


def _parse(url: str):
    parts = urlsplit(url.strip())
    if parts.scheme not in ("postgresql", "postgres", SCHEME) or "@" not in parts.netloc or len(parts.path) < 2:
        raise ValueError("Expected a connection string like postgresql://user:password@host/database?sslmode=require")
    return parts


def database_url(neon_url: str) -> str:
    """The owner's URL, in the form SQLAlchemy needs."""
    parts = _parse(neon_url)
    return urlunsplit((SCHEME, parts.netloc, parts.path, parts.query, ""))


def readonly_url(neon_url: str, password: str) -> str:
    """The same database and options, but as the read-only role."""
    parts = _parse(neon_url)
    host = parts.netloc.rsplit("@", 1)[-1]
    return urlunsplit((SCHEME, f"{READONLY_USER}:{quote(password, safe='')}@{host}", parts.path, parts.query, ""))


def check(neon_url: str, password: str, log=print) -> bool:
    """Connect as the owner and as the read-only role, and verify what each may and may not do."""
    from sqlalchemy import create_engine, text

    options = {"connect_timeout": 30, "prepare_threshold": None}
    owner = create_engine(database_url(neon_url), connect_args=options)
    readonly = create_engine(readonly_url(neon_url, password), connect_args=options)
    problems = 0

    def report(ok: bool, label: str, detail: str = "") -> None:
        nonlocal problems
        problems += 0 if ok else 1
        log(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f": {detail}" if detail else ""))

    def scalar(engine, sql):
        with engine.connect() as connection:
            return connection.execute(text(sql)).scalar()

    def is_refused(engine, sql) -> bool:
        try:
            with engine.begin() as connection:
                connection.execute(text(sql))
        except Exception:
            return True
        return False

    def short(error: Exception) -> str:
        return f"{type(error).__name__}: {str(error).strip().splitlines()[0][:160]}"

    log("As the owner:")
    try:
        scalar(owner, "SELECT 1")
    except Exception as error:
        report(False, "could not connect as the owner", short(error))
        log("Check NEON_URL (copy it again from Neon's Connect dialog) and your internet connection.")
        return False
    for table in ("companies", "filings", "financial_facts", "doc_chunks"):
        count = scalar(owner, f"SELECT COUNT(*) FROM {table}")
        report(count > 0, f"{table} has rows", str(count))
    report(scalar(owner, "SELECT COUNT(*) FROM doc_chunks WHERE embedding IS NULL") == 0, "every chunk has an embedding")

    log("As the read-only role:")
    try:
        scalar(readonly, "SELECT 1")
    except Exception as error:
        report(False, "could not log in as copilot_readonly", short(error))
        log("This is usually the wrong RO_PASSWORD: it must be the one you gave to scripts/init_remote_db.sh.")
        return False
    report(scalar(readonly, "SELECT COUNT(*) FROM v_quarterly_metrics") > 0, "can read the metrics view",
           f"latest quarter {scalar(readonly, 'SELECT MAX(period_end) FROM v_quarterly_metrics')}")
    report(scalar(readonly, "SELECT COUNT(*) FROM companies") > 0, "can read companies")
    report(is_refused(readonly, "SELECT COUNT(*) FROM doc_chunks"), "is refused the filing text table")
    report(is_refused(readonly, "INSERT INTO companies (cik, ticker, name) VALUES (-1, 'X', 'X')"), "is refused writes")
    report(is_refused(readonly, "SELECT pg_sleep(8)"), "is stopped by the 5 second time limit")
    log("All checks passed." if not problems else f"{problems} check(s) failed.")
    return not problems


def main() -> None:
    commands = ("database", "readonly", "check")
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        raise SystemExit(f"Usage: python -m sec_copilot.deploy_urls {'|'.join(commands)}")
    neon_url, password = os.environ.get("NEON_URL", ""), os.environ.get("RO_PASSWORD", "")
    if not neon_url or (sys.argv[1] != "database" and not password):
        raise SystemExit("Set NEON_URL (and, for readonly and check, RO_PASSWORD) as environment variables first.")
    if sys.argv[1] == "database":
        print(database_url(neon_url))
    elif sys.argv[1] == "readonly":
        print(readonly_url(neon_url, password))
    else:
        raise SystemExit(0 if check(neon_url, password) else 1)


if __name__ == "__main__":
    main()