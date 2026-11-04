"""Forecast a company's next-quarter revenue with the best backtested model, plus a range from its past errors."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from sec_copilot.ml.backtest import RESULT_FILE
from sec_copilot.ml.data import load_revenue_panel
from sec_copilot.ml.features import FEATURES, QUARTER_DAYS, make_features, with_next_quarter
from sec_copilot.ml.models import MODELS


class ForecastUnavailable(ValueError):
    """The question can't be answered (unknown company, too little history). The message is meant to be read."""


def format_money(value: float) -> str:
    return f"${value / 1e9:.1f} billion" if value >= 1e9 else f"${value / 1e6:.0f} million"


def forecast_next_quarter(panel: pd.DataFrame, ticker: str, backtest: dict) -> dict:
    """Train the chosen model on everything known so far and predict the quarter after the company's latest."""
    if ticker not in set(panel["ticker"]):
        raise ForecastUnavailable(f"No usable revenue history for {ticker}. Forecasts exist for: "
                                  + ", ".join(sorted(panel["ticker"].unique())) + ".")
    model_name = backtest["best_model"]
    stats = next(m for m in backtest["models"] if m["model"] == model_name)

    features = make_features(with_next_quarter(panel))
    train = features.dropna(subset=FEATURES + ["target"])                       # every quarter that has happened
    test = features[(features["ticker"] == ticker) & features["revenue"].isna()]  # the empty next-quarter row
    value = float(MODELS[model_name](train, test, panel).iloc[0])
    low, high = value * np.exp(stats["error_q10"]), value * np.exp(stats["error_q90"])

    last = panel[panel["ticker"] == ticker].sort_values("period_end").iloc[-1]
    next_end = (last["period_end"] + pd.Timedelta(days=QUARTER_DAYS)).date()
    company_error = backtest.get("best_model_error_by_company", {}).get(ticker)
    summary = (f"Statistical forecast for {ticker}'s quarter ending about {next_end}: {format_money(value)}, "
               f"80% range {format_money(low)} to {format_money(high)}. The last reported quarter ended "
               f"{last['period_end'].date()} at {format_money(float(last['revenue']))}. Model: {model_name}; "
               f"its average backtest error was {stats['mape'] * 100:.1f}% across all companies"
               + (f" and {company_error * 100:.1f}% for {ticker}." if company_error is not None else "."))
    result = {
        "ticker": ticker, "model": model_name,
        "last_quarter_end": str(last["period_end"].date()), "last_quarter_revenue": float(last["revenue"]),
        "forecast_quarter_end_approx": str(next_end), "forecast_revenue": value,
        "range_low": float(low), "range_high": float(high),
        "backtest_mape_percent": round(stats["mape"] * 100, 1), "backtest_forecasts": int(stats["forecasts"]),
        "summary": summary,
        "caveat": "A statistical estimate from past revenue only. It ignores guidance, new products and the economy, "
                  "and the range is the same relative width for every company.",
    }
    if company_error is not None:
        result["company_backtest_mape_percent"] = round(company_error * 100, 1)
    return result


class RevenueForecaster:
    """Callable used by the agent: loads the revenue history and the backtest results once, then answers per ticker."""

    def __init__(self, engine, backtest_path: Path = RESULT_FILE):
        self._engine = engine
        self._backtest_path = Path(backtest_path)
        self._panel = None
        self._backtest = None

    def __call__(self, ticker: str) -> dict:
        if self._backtest is None:
            if not self._backtest_path.exists():
                raise ForecastUnavailable("The forecast model has not been evaluated yet. Run: python -m sec_copilot.ml.backtest")
            self._backtest = json.loads(self._backtest_path.read_text())
        if self._panel is None:
            self._panel = load_revenue_panel(self._engine)
        return forecast_next_quarter(self._panel, ticker.upper(), self._backtest)