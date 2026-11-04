import json

import numpy as np
import pytest

from sec_copilot.agent.agent import check_citations, render_sources, summarize_outcome
from sec_copilot.agent.toolbox import TOOL_DECLARATIONS, Toolbox
from sec_copilot.agent.verify import unsupported_numbers
from sec_copilot.ml.forecast import ForecastUnavailable, RevenueForecaster, forecast_next_quarter, format_money
from tests.test_ml import make_panel

BACKTEST = {
    "best_model": "seasonal_naive",
    "models": [{"model": "seasonal_naive", "mape": 0.10, "error_q10": -0.1, "error_q90": 0.2, "forecasts": 50}],
    "best_model_error_by_company": {"AAA": 0.05},
}


# ---------- the forecast itself ----------

def test_the_forecast_uses_the_chosen_model_and_the_backtest_errors_for_its_range():
    panel = make_panel()
    result = forecast_next_quarter(panel, "AAA", BACKTEST)
    history = panel[panel["ticker"] == "AAA"].sort_values("period_end")
    expected = history["revenue"].iloc[-4]                      # seasonal naive: the same quarter a year before the next one
    assert result["forecast_revenue"] == pytest.approx(expected)
    assert result["range_low"] == pytest.approx(expected * np.exp(-0.1))
    assert result["range_high"] == pytest.approx(expected * np.exp(0.2))
    assert result["last_quarter_revenue"] == pytest.approx(history["revenue"].iloc[-1])
    assert result["backtest_mape_percent"] == 10.0 and result["company_backtest_mape_percent"] == 5.0
    assert result["forecast_quarter_end_approx"] > result["last_quarter_end"]
    assert "80% range" in result["summary"] and "10.0%" in result["summary"] and "5.0% for AAA" in result["summary"]


@pytest.mark.parametrize("model", ["ets", "yoy_trend", "ridge", "lightgbm"])
def test_every_model_can_produce_the_final_forecast(model):
    backtest = {**BACKTEST, "best_model": model, "models": [{**BACKTEST["models"][0], "model": model}]}
    result = forecast_next_quarter(make_panel(), "BBB", backtest)
    assert np.isfinite(result["forecast_revenue"]) and result["forecast_revenue"] > 0
    assert result["range_low"] < result["forecast_revenue"] < result["range_high"]


def test_an_unknown_company_gets_a_helpful_message():
    with pytest.raises(ForecastUnavailable) as caught:
        forecast_next_quarter(make_panel(), "TSLA", BACKTEST)
    assert "TSLA" in str(caught.value) and "AAA" in str(caught.value)


def test_money_is_formatted_in_billions_or_millions():
    assert format_money(117_432_000_000) == "$117.4 billion"
    assert format_money(850_000_000) == "$850 million"


def test_the_forecaster_loads_its_data_once_and_explains_a_missing_backtest(tmp_path, monkeypatch):
    from sec_copilot.ml import forecast

    calls = []
    monkeypatch.setattr(forecast, "load_revenue_panel", lambda engine: calls.append(1) or make_panel())
    missing = RevenueForecaster(engine=None, backtest_path=tmp_path / "nope.json")
    with pytest.raises(ForecastUnavailable) as caught:
        missing("AAA")
    assert "backtest" in str(caught.value) and calls == []

    path = tmp_path / "backtest.json"
    path.write_text(json.dumps(BACKTEST))
    forecaster = RevenueForecaster(engine=None, backtest_path=path)
    forecaster("aaa")
    forecaster("BBB")
    assert calls == [1]                                           # the revenue history was loaded a single time


# ---------- the tool, its citations and its number check ----------

RESULT = {"ticker": "AAPL", "model": "ets", "forecast_revenue": 117.4e9, "range_low": 112.0e9, "range_high": 123.1e9,
          "last_quarter_revenue": 109.4e9, "backtest_mape_percent": 4.5, "company_backtest_mape_percent": 5.5,
          "backtest_forecasts": 114,
          "summary": "Statistical forecast for AAPL's quarter ending about 2026-12-31: $117.4 billion, 80% range "
                     "$112.0 billion to $123.1 billion. Model: ets; its average backtest error was 4.5% across all "
                     "companies and 5.5% for AAPL."}


def make_toolbox(forecaster):
    return Toolbox(engine=None, readonly_engine=None, embedder=None, forecaster=forecaster)


def test_the_tool_is_declared_and_returns_a_tagged_result():
    assert "forecast_revenue" in [d.name for d in TOOL_DECLARATIONS.function_declarations]
    toolbox = make_toolbox(lambda ticker: {**RESULT, "ticker": ticker})
    out = toolbox.run("forecast_revenue", {"ticker": " aapl "})
    assert out["citation"] == "F1" and out["ticker"] == "AAPL"
    assert toolbox.sources["F1"]["type"] == "forecast" and toolbox.sources["F1"]["summary"].startswith("Statistical forecast")
    assert summarize_outcome(out) == "forecast for AAPL (ets)"


def test_unavailable_forecasts_are_ordinary_errors_not_crashes():
    def refuse(ticker):
        raise ForecastUnavailable(f"No usable revenue history for {ticker}.")

    toolbox = make_toolbox(refuse)
    out = toolbox.run("forecast_revenue", {"ticker": "TSLA"})
    assert out == {"error": "No usable revenue history for TSLA."}
    assert "The tool failed" not in out["error"] and toolbox.sources == {}
    assert make_toolbox(refuse).run("forecast_revenue", {"ticker": ""}) == {"error": "ticker is required."}


def test_a_genuine_crash_is_still_reported_as_a_tool_failure():
    def crash(ticker):
        raise RuntimeError("boom")

    assert "The tool failed: boom" in make_toolbox(crash).run("forecast_revenue", {"ticker": "AAPL"})["error"]


def test_forecast_tags_are_recognised_and_rendered():
    sources = {"F1": {"type": "forecast", **RESULT}}
    cited, invented = check_citations("Revenue should be near $117.4 billion [F1][F2].", sources)
    assert list(cited) == ["F1"] and invented == ["F2"]
    assert render_sources(sources).startswith("[F1] Statistical forecast for AAPL")


def test_numbers_quoted_from_a_forecast_pass_and_invented_ones_are_flagged():
    sources = {"F1": {"type": "forecast", **RESULT}}
    honest = ("Apple's next quarter is forecast at $117.4 billion, with an 80% range of $112.0 billion to $123.1 billion [F1]. "
              "The model's backtest error was 4.5% overall and 5.5% for Apple.")
    assert unsupported_numbers(honest, sources) == []
    assert unsupported_numbers("I expect $130 billion, with 2% error.", sources) == ["$130 billion", "2%"]