import json

import numpy as np
import pandas as pd
import pytest

from sec_copilot.ml import data
from sec_copilot.ml.backtest import REFERENCE, quarter_cutoffs, run_backtest, save_results, summarize
from sec_copilot.ml.features import FEATURES, make_features, with_next_quarter
from sec_copilot.ml.models import MODELS


def make_panel(tickers=("AAA", "BBB", "CCC", "DDD"), quarters=40, seed=1):
    """Companies with growth, a seasonal shape and a little noise, each on its own fiscal calendar."""
    rng = np.random.default_rng(seed)
    rows = []
    for i, ticker in enumerate(tickers):
        season = np.array([0.9, 1.0, 1.05, 1.15]) if i % 2 == 0 else np.array([1.2, 0.95, 0.9, 0.95])
        ends = pd.date_range("2016-03-31", periods=quarters, freq="QE") + pd.Timedelta(days=7 * i)
        for k, end in enumerate(ends):
            revenue = (10 + 5 * i) * (1.015 + 0.01 * i) ** k * season[k % 4] * np.exp(rng.normal(0, 0.02)) * 1e9
            rows.append({"ticker": ticker, "period_end": end, "quarter": k % 4 + 1, "q_index": 2016 * 4 + k, "revenue": revenue})
    return pd.DataFrame(rows)


# ---------- features ----------

def single_company(revenues):
    ends = pd.date_range("2020-03-31", periods=len(revenues), freq="QE")
    return pd.DataFrame({"ticker": "X", "period_end": ends, "quarter": [k % 4 + 1 for k in range(len(revenues))],
                         "q_index": range(len(revenues)), "revenue": revenues})


def test_features_match_hand_calculation():
    revenue = [100, 120, 90, 150, 110, 132, 99, 165, 121, 145, 109, 181]
    f = make_features(single_company(revenue))
    row, y = f.iloc[10], np.log(revenue)
    assert row["target"] == pytest.approx(y[10] - y[6])
    assert [row[f"g{k}"] for k in (1, 2, 3, 4)] == pytest.approx([y[9] - y[5], y[8] - y[4], y[7] - y[3], y[6] - y[2]])
    assert row["qoq1"] == pytest.approx(y[9] - y[8])
    assert row["seasonal_qoq"] == pytest.approx(y[6] - y[5])
    assert row["y_lag4"] == 99 and [row[f"q{q}"] for q in (1, 2, 3, 4)] == [0, 0, 1, 0]


def test_a_feature_never_looks_at_the_future():
    revenue = [100, 120, 90, 150, 110, 132, 99, 165, 121, 145, 109, 181]
    changed = revenue[:9] + [1e9, 1e9, 1e9]                      # rewrite quarters 9, 10, 11
    a, b = make_features(single_company(revenue)), make_features(single_company(changed))
    assert a.loc[8, FEATURES + ["target", "y_lag4"]].tolist() == b.loc[8, FEATURES + ["target", "y_lag4"]].tolist()


def test_the_next_quarter_row_has_features_but_no_target():
    panel = single_company([100, 120, 90, 150, 110, 132, 99, 165, 121, 145])
    extended = with_next_quarter(panel)
    last = make_features(extended).iloc[-1]
    y = np.log(panel["revenue"].to_numpy())
    assert last["quarter"] == 3 and np.isnan(last["revenue"]) and np.isnan(last["target"])
    assert last["g1"] == pytest.approx(y[9] - y[5]) and last["y_lag4"] == 99
    assert last["period_end"] > panel["period_end"].max()


# ---------- cleaning ----------

def test_a_missing_quarter_ends_the_usable_history_and_short_companies_are_dropped():
    long = make_panel(("LONG",), quarters=30)
    gap = make_panel(("GAP",), quarters=30)
    gap = gap[gap["q_index"] != gap["q_index"].iloc[9]]          # one quarter missing: only the later run is kept
    short = make_panel(("SHORT",), quarters=data.MIN_HISTORY - 1)
    cleaned = data.clean_panel(pd.concat([long, gap, short], ignore_index=True))
    sizes = cleaned.groupby("ticker").size().to_dict()
    assert sizes == {"GAP": 20, "LONG": 30}
    assert cleaned[cleaned["ticker"] == "GAP"]["q_index"].diff().dropna().eq(1).all()


# ---------- the backtest ----------

def test_cutoffs_are_consecutive_quarter_ends_ending_before_the_latest_quarter():
    for latest in ("2026-09-30", "2026-09-03"):
        panel = pd.DataFrame({"period_end": pd.to_datetime(["2026-06-27", latest])})
        cutoffs = quarter_cutoffs(panel, folds=8)
        assert cutoffs[-1] == pd.Timestamp("2026-06-30") and cutoffs[0] == pd.Timestamp("2024-09-30")
        assert len(cutoffs) == 8


def test_predictions_never_depend_on_data_after_the_cutoff():
    """The most important test: rewrite the future with absurd values and the earliest fold's predictions must not move."""
    panel = make_panel()
    honest = run_backtest(panel, folds=3)
    first = honest["cutoff"].min()
    poisoned = panel.copy()
    poisoned.loc[poisoned["period_end"] > first, "revenue"] = 1e15
    after = run_backtest(poisoned, folds=3)

    key = ["cutoff", "ticker", "model"]
    a = honest[honest["cutoff"] == first].sort_values(key)["predicted"].to_numpy()
    b = after[after["cutoff"] == first].sort_values(key)["predicted"].to_numpy()
    assert len(a) == len(b) > 0 and np.allclose(a, b)
    assert set(honest["model"]) == set(MODELS)


def test_every_model_gives_finite_sensible_forecasts_and_trend_models_beat_the_baseline():
    results = run_backtest(make_panel(), folds=4)
    assert np.isfinite(results["predicted"]).all() and (results["predicted"] > 0).all()
    summary = summarize(results).set_index("model")
    assert summary.loc["seasonal_naive", "mape"] > summary.loc["yoy_trend", "mape"]      # growing companies
    assert summary["mape"].max() < 0.5


def test_the_backtest_is_repeatable():
    panel = make_panel()
    one, two = run_backtest(panel, folds=2), run_backtest(panel, folds=2)
    assert np.allclose(one["predicted"], two["predicted"])


def test_summary_numbers_and_saved_file(tmp_path):
    cutoff = pd.Timestamp("2026-06-30")
    rows = []
    for ticker, actual, baseline, better in (("A", 100.0, 80.0, 95.0), ("B", 200.0, 220.0, 210.0)):
        rows += [{"cutoff": cutoff, "ticker": ticker, "model": REFERENCE, "actual": actual, "predicted": baseline},
                 {"cutoff": cutoff, "ticker": ticker, "model": "good", "actual": actual, "predicted": better}]
    results = pd.DataFrame(rows)
    results["ape"] = (results["predicted"] - results["actual"]).abs() / results["actual"]
    results["log_error"] = np.log(results["actual"] / results["predicted"])
    summary = summarize(results)
    assert list(summary["model"]) == ["good", REFERENCE]
    assert summary.loc[0, "mape"] == pytest.approx((0.05 + 0.05) / 2) and summary.loc[0, "beats_reference"] == 1.0
    assert summary.loc[1, "mape"] == pytest.approx((0.20 + 0.10) / 2)

    save_results(summary, results, tmp_path / "out.json")
    saved = json.loads((tmp_path / "out.json").read_text())
    assert saved["best_model"] == "good" and saved["companies"] == 2 and len(saved["models"]) == 2
    assert saved["best_model_error_by_company"] == {"A": 0.05, "B": 0.05}