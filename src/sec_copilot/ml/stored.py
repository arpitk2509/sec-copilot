"""Serve forecasts that were computed ahead of time.

This module imports nothing heavy (no pandas, scikit-learn or statsmodels), so the web API stays small enough for a
512 MB host. The forecasts are produced by `python -m sec_copilot.ml.forecast` after each data refresh and backtest.
"""
import json
from pathlib import Path

from sqlalchemy import text

FORECASTS_FILE = Path("models/forecasts.json")
LATEST_QUARTER = text("SELECT MAX(period_end) FROM v_quarterly_metrics WHERE ticker = :ticker AND revenue IS NOT NULL")


class ForecastUnavailable(ValueError):
    """The question can't be answered (unknown company, stale or missing forecasts). The message is meant to be read."""


def format_money(value: float) -> str:
    return f"${value / 1e9:.1f} billion" if value >= 1e9 else f"${value / 1e6:.0f} million"


class StoredForecaster:
    """Callable used by the agent: looks up a stored forecast, and refuses one that the data has outgrown."""

    def __init__(self, engine, path: Path | str | None = None):
        self._engine = engine
        self._path = Path(path or FORECASTS_FILE)
        self._data = None

    def _forecasts(self) -> dict:
        if self._data is None:
            if not self._path.exists():
                raise ForecastUnavailable("Forecasts have not been generated yet. Run: python -m sec_copilot.ml.forecast")
            self._data = json.loads(self._path.read_text())["forecasts"]
        return self._data

    def __call__(self, ticker: str) -> dict:
        ticker = ticker.upper()
        forecasts = self._forecasts()
        if ticker not in forecasts:
            raise ForecastUnavailable(f"No usable revenue history for {ticker}. Forecasts exist for: "
                                      + ", ".join(sorted(forecasts)) + ".")
        entry = forecasts[ticker]
        with self._engine.connect() as connection:
            latest = connection.execute(LATEST_QUARTER, {"ticker": ticker}).scalar()
        if latest is not None and str(latest) != entry["last_quarter_end"]:
            raise ForecastUnavailable(f"The stored forecast for {ticker} is out of date (the database now has a quarter "
                                      f"ending {latest}). Regenerate it with: python -m sec_copilot.ml.forecast")
        return entry