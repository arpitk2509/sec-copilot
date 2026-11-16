import json
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, text

from sec_copilot.agent.toolbox import Toolbox
from sec_copilot.ml import stored
from sec_copilot.ml.forecast import forecast_all, forecast_next_quarter, save_forecasts
from sec_copilot.ml.stored import ForecastUnavailable, StoredForecaster
from tests.test_ml import make_panel

BACKTEST = {
    "best_model": "seasonal_naive",
    "models": [{"model": "seasonal_naive", "mape": 0.10, "error_q10": -0.1, "error_q90": 0.2, "forecasts": 50}],
    "best_model_error_by_company": {"AAA": 0.05, "BBB": 0.10},
}


def engine_with_quarters(rows):
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE v_quarterly_metrics (ticker TEXT, period_end TEXT, revenue REAL)"))
        for ticker, period_end in rows:
            connection.execute(text("INSERT INTO v_quarterly_metrics VALUES (:t, :p, 1.0)"), {"t": ticker, "p": period_end})
    return engine


def write_forecasts(path, **entries):
    path.write_text(json.dumps({"generated_at": "2026-11-15T10:00:00", "data_through": "2026-09-30", "forecasts": entries}))
    return path


ENTRY = {"ticker": "AAA", "last_quarter_end": "2026-09-26", "forecast_revenue": 1.25e10, "summary": "stored summary"}


# ---------- serving ----------

def test_a_fresh_stored_forecast_is_served_whatever_the_case_of_the_ticker(tmp_path):
    forecaster = StoredForecaster(engine_with_quarters([("AAA", "2026-06-27"), ("AAA", "2026-09-26")]),
                                  write_forecasts(tmp_path / "f.json", AAA=ENTRY))
    assert forecaster("aaa") == ENTRY and forecaster("AAA") == ENTRY


def test_a_forecast_older_than_the_data_is_refused_with_the_way_to_fix_it(tmp_path):
    engine = engine_with_quarters([("AAA", "2026-09-26"), ("AAA", "2026-12-26")])       # the database moved on
    with pytest.raises(ForecastUnavailable) as caught:
        StoredForecaster(engine, write_forecasts(tmp_path / "f.json", AAA=ENTRY))("AAA")
    message = str(caught.value)
    assert "out of date" in message and "2026-12-26" in message and "python -m sec_copilot.ml.forecast" in message


def test_unknown_companies_and_a_missing_file_get_readable_messages(tmp_path):
    engine = engine_with_quarters([("AAA", "2026-09-26")])
    with pytest.raises(ForecastUnavailable) as caught:
        StoredForecaster(engine, write_forecasts(tmp_path / "f.json", AAA=ENTRY))("TSLA")
    assert "TSLA" in str(caught.value) and "AAA" in str(caught.value)
    with pytest.raises(ForecastUnavailable) as caught:
        StoredForecaster(engine, tmp_path / "missing.json")("AAA")
    assert "have not been generated" in str(caught.value)


def test_the_file_is_read_once(tmp_path):
    path = write_forecasts(tmp_path / "f.json", AAA=ENTRY)
    forecaster = StoredForecaster(engine_with_quarters([("AAA", "2026-09-26")]), path)
    forecaster("AAA")
    path.unlink()                                             # a second read would fail
    assert forecaster("AAA") == ENTRY


# ---------- generating, then serving: the whole loop ----------

def test_forecasts_computed_ahead_of_time_are_the_same_as_live_ones_and_are_served_while_fresh(tmp_path):
    panel = make_panel()
    forecasts = forecast_all(panel, BACKTEST)
    assert sorted(forecasts) == ["AAA", "BBB", "CCC", "DDD"]
    assert forecasts["AAA"]["forecast_revenue"] == forecast_next_quarter(panel, "AAA", BACKTEST)["forecast_revenue"]

    path = save_forecasts(forecasts, panel["period_end"].max().date(), tmp_path / "forecasts.json")
    saved = json.loads(path.read_text())
    assert set(saved) == {"generated_at", "data_through", "forecasts"} and saved["data_through"] == str(panel["period_end"].max().date())

    latest = {t: str(g["period_end"].max().date()) for t, g in panel.groupby("ticker")}
    engine = engine_with_quarters(latest.items())
    served = StoredForecaster(engine, path)("BBB")
    assert served == forecasts["BBB"] and "stored summary" not in served["summary"]

    with engine.begin() as connection:                        # a new quarter arrives for BBB: its forecast is now stale
        connection.execute(text("INSERT INTO v_quarterly_metrics VALUES ('BBB', '2099-01-01', 1.0)"))
    with pytest.raises(ForecastUnavailable):
        StoredForecaster(engine, path)("BBB")
    assert StoredForecaster(engine, path)("AAA")["ticker"] == "AAA"       # other companies are unaffected


# ---------- the agent's tool uses the stored forecasts by default ----------

def test_the_toolbox_serves_stored_forecasts_when_no_forecaster_is_given(tmp_path, monkeypatch):
    monkeypatch.setattr(stored, "FORECASTS_FILE", write_forecasts(tmp_path / "f.json", AAA={**ENTRY, "model": "ets"}))
    toolbox = Toolbox(engine=None, readonly_engine=engine_with_quarters([("AAA", "2026-09-26")]), embedder=None)
    out = toolbox.run("forecast_revenue", {"ticker": "aaa"})
    assert out["citation"] == "F1" and toolbox.sources["F1"]["type"] == "forecast"
    stale = Toolbox(engine=None, readonly_engine=engine_with_quarters([("AAA", "2027-03-26")]), embedder=None)
    refused = stale.run("forecast_revenue", {"ticker": "AAA"})
    assert "out of date" in refused["error"] and "The tool failed" not in refused["error"]


# ---------- the point of all this: a small web process ----------

SERVING_CHECK = """
import json, sys
from pathlib import Path
from sqlalchemy import create_engine, text
import sec_copilot.api.app
from sec_copilot.agent.toolbox import Toolbox
from sec_copilot.ml import stored

stored.FORECASTS_FILE = Path(sys.argv[1])
engine = create_engine("sqlite://")
with engine.begin() as connection:
    connection.execute(text("CREATE TABLE v_quarterly_metrics (ticker TEXT, period_end TEXT, revenue REAL)"))
    connection.execute(text("INSERT INTO v_quarterly_metrics VALUES ('AAA', '2026-09-26', 1.0)"))
result = Toolbox(None, engine, None).run("forecast_revenue", {"ticker": "aaa"})
assert result.get("citation") == "F1", result
heavy = ("pandas", "scipy", "statsmodels", "sklearn", "lightgbm", "matplotlib")
print(json.dumps(sorted(name for name in heavy if name in sys.modules)))
"""


def test_serving_a_forecast_never_loads_the_machine_learning_libraries(tmp_path):
    """Importing the API and answering a forecast question must not pull in pandas, statsmodels and friends.
    They add about 160 MB, which a 512 MB free host cannot spare."""
    path = write_forecasts(tmp_path / "f.json", AAA=ENTRY)
    run = subprocess.run([sys.executable, "-c", SERVING_CHECK, str(path)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout.strip().splitlines()[-1]) == []