"""Forecast a company's next-quarter revenue with the best backtested model, plus a range from its past errors.

Computing a forecast needs pandas and statsmodels, which are heavy. So forecasts are computed ahead of time
(`python -m sec_copilot.ml.forecast`) and the web API serves the stored results (see stored.py).
"""
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from sec_copilot.ml.backtest import RESULT_FILE
from sec_copilot.ml.data import load_revenue_panel
from sec_copilot.ml.features import FEATURES, QUARTER_DAYS, make_features, with_next_quarter
from sec_copilot.ml.models import MODELS
from sec_copilot.ml.stored import FORECASTS_FILE, ForecastUnavailable, format_money

MIN_RANGE_SCALE, MAX_RANGE_SCALE = 0.5, 4.0     # how far a company's range may differ from the average one


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
    company_error = backtest.get("best_model_error_by_company", {}).get(ticker)
    # Some companies are far harder to forecast than others (Micron versus Adobe). Widen or narrow the pooled range
    # by how that company's own backtest error compares with the average, within sensible limits.
    scale = 1.0 if company_error is None else min(max(company_error / stats["mape"], MIN_RANGE_SCALE), MAX_RANGE_SCALE)
    low, high = value * np.exp(stats["error_q10"] * scale), value * np.exp(stats["error_q90"] * scale)

    last = panel[panel["ticker"] == ticker].sort_values("period_end").iloc[-1]
    next_end = (last["period_end"] + pd.Timedelta(days=QUARTER_DAYS)).date()
    summary = (f"Statistical forecast for {ticker}'s quarter ending about {next_end}: {format_money(value)}, "
               f"approximate 80% range {format_money(low)} to {format_money(high)}"
               + (f" (scaled to {ticker}'s own backtest error)" if company_error is not None else "")
               + f". The last reported quarter ended "
               f"{last['period_end'].date()} at {format_money(float(last['revenue']))}. Model: {model_name}; "
               f"its average backtest error was {stats['mape'] * 100:.1f}% across all companies"
               + (f" and {company_error * 100:.1f}% for {ticker}." if company_error is not None else "."))
    result = {
        "ticker": ticker, "model": model_name,
        "last_quarter_end": str(last["period_end"].date()), "last_quarter_revenue": float(last["revenue"]),
        "forecast_quarter_end_approx": str(next_end), "forecast_revenue": value,
        "range_low": float(low), "range_high": float(high), "range_scale": round(scale, 2),
        "backtest_mape_percent": round(stats["mape"] * 100, 1), "backtest_forecasts": int(stats["forecasts"]),
        "summary": summary,
        "caveat": "A statistical estimate from past revenue only. It ignores guidance, new products and the economy. "
                  "The range comes from past forecast errors, widened or narrowed by how predictable this company has been.",
    }
    if company_error is not None:
        result["company_backtest_mape_percent"] = round(company_error * 100, 1)
    return result


class RevenueForecaster:
    """Computes a forecast live: loads the revenue history and backtest results once, then answers per ticker."""

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


def forecast_all(panel: pd.DataFrame, backtest: dict) -> dict[str, dict]:
    """One forecast per company, ready to be stored."""
    return {ticker: forecast_next_quarter(panel, ticker, backtest) for ticker in sorted(panel["ticker"].unique())}


def save_forecasts(forecasts: dict[str, dict], data_through, path: Path | str | None = None) -> Path:
    path = Path(path or FORECASTS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at": datetime.now().isoformat(timespec="seconds"), "data_through": str(data_through),
               "forecasts": forecasts}
    path.write_text(json.dumps(payload, indent=2))
    return path


def main() -> None:
    from sqlalchemy import create_engine

    from sec_copilot.config import READONLY_DATABASE_URL

    if not RESULT_FILE.exists():
        raise SystemExit("Run the backtest first: uv run python -m sec_copilot.ml.backtest")
    panel = load_revenue_panel(create_engine(READONLY_DATABASE_URL))
    forecasts = forecast_all(panel, json.loads(RESULT_FILE.read_text()))
    path = save_forecasts(forecasts, panel["period_end"].max().date())
    print(f"Forecasts for {len(forecasts)} companies (data through {panel['period_end'].max().date()}), saved to {path}\n")
    for ticker, f in forecasts.items():
        print(f"  {ticker:<5} quarter ending about {f['forecast_quarter_end_approx']}  {format_money(f['forecast_revenue']):>16}"
              f"   range {format_money(f['range_low'])} to {format_money(f['range_high'])}")


if __name__ == "__main__":
    main()