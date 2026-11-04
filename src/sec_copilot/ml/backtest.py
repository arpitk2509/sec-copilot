"""Test the forecasting models honestly: train only on the past, predict the next quarter, repeat over time."""
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from sec_copilot.ml.data import load_revenue_panel
from sec_copilot.ml.features import FEATURES, make_features
from sec_copilot.ml.models import MODELS

FOLDS = 8                                     # eight calendar quarters of out-of-sample tests
REFERENCE = "seasonal_naive"                  # every model must beat this to be worth its complexity
RESULT_FILE = Path("models/forecast_backtest.json")
CHART_FILE = Path("docs/charts/forecast_backtest.png")


def quarter_cutoffs(panel: pd.DataFrame, folds: int = FOLDS) -> list[pd.Timestamp]:
    """Quarter-end dates D. For each one we train on quarters ending on or before D and predict the next quarter."""
    window_end = panel["period_end"].max() + pd.offsets.QuarterEnd(0)      # the quarter-end containing the latest data
    last_cutoff = window_end - pd.offsets.QuarterEnd(1)
    return list(pd.date_range(end=last_cutoff, periods=folds, freq="QE"))


def run_backtest(panel: pd.DataFrame, models: dict | None = None, folds: int = FOLDS) -> pd.DataFrame:
    """One row per (fold, company, model): the prediction, the actual revenue and the percentage error."""
    models = models or MODELS
    features = make_features(panel)
    rows = []
    for cutoff in quarter_cutoffs(panel, folds):
        train = features[features["period_end"] <= cutoff]                         # the past only
        window = features[(features["period_end"] > cutoff) & (features["period_end"] <= cutoff + pd.offsets.QuarterEnd(1))]
        test = window.dropna(subset=FEATURES + ["y_lag4"]).groupby("ticker").head(1)   # one quarter per company
        history = panel[panel["period_end"] <= cutoff]
        if test.empty:
            continue
        for name, model in models.items():
            predicted = model(train, test, history)
            for index, row in test.iterrows():
                rows.append({"cutoff": cutoff, "ticker": row["ticker"], "period_end": row["period_end"], "model": name,
                             "actual": row["revenue"], "predicted": float(predicted.loc[index])})
    results = pd.DataFrame(rows)
    results["ape"] = (results["predicted"] - results["actual"]).abs() / results["actual"]
    results["log_error"] = np.log(results["actual"] / results["predicted"])      # positive = the model was too low
    return results


def summarize(results: pd.DataFrame) -> pd.DataFrame:
    """Accuracy per model, best first. 'beats reference' is the share of forecasts better than the simple baseline."""
    reference = results[results["model"] == REFERENCE].set_index(["cutoff", "ticker"])["ape"]
    rows = []
    for name, group in results.groupby("model"):
        group = group.set_index(["cutoff", "ticker"])
        beats = float((group["ape"] < reference.reindex(group.index)).mean())
        rows.append({"model": name, "mape": group["ape"].mean(), "median_ape": group["ape"].median(),
                     "forecasts": len(group), "beats_reference": beats,
                     "error_q10": group["log_error"].quantile(0.10), "error_q90": group["log_error"].quantile(0.90)})
    return pd.DataFrame(rows).sort_values("mape").reset_index(drop=True)


def save_results(summary: pd.DataFrame, results: pd.DataFrame, path: Path = RESULT_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "best_model": summary.iloc[0]["model"],
        "reference_model": REFERENCE,
        "folds": int(results["cutoff"].nunique()),
        "companies": int(results["ticker"].nunique()),
        "models": summary.to_dict(orient="records"),
        "best_model_error_by_company": results[results["model"] == summary.iloc[0]["model"]]
        .groupby("ticker")["ape"].mean().round(4).to_dict(),     # how hard each company was to forecast
    }
    path.write_text(json.dumps(payload, indent=2, default=str))


def draw_chart(summary: pd.DataFrame, path: Path = CHART_FILE) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = summary.sort_values("mape", ascending=False)
    fig, ax = plt.subplots(figsize=(8, 4))
    colors = ["#c0504d" if m == REFERENCE else "#4f81bd" for m in ordered["model"]]
    ax.barh(ordered["model"], ordered["mape"] * 100, color=colors)
    for y, value in enumerate(ordered["mape"] * 100):
        ax.text(value, y, f" {value:.1f}%", va="center")
    ax.set_xlabel("Mean absolute percentage error of next-quarter revenue (lower is better)")
    ax.set_title("Revenue forecast backtest: 8 out-of-sample quarters, all companies (red = simple baseline)")
    ax.grid(axis="x", alpha=0.3)
    fig.savefig(path, dpi=120, bbox_inches="tight")


def main() -> None:
    from sqlalchemy import create_engine

    from sec_copilot.config import READONLY_DATABASE_URL

    panel = load_revenue_panel(create_engine(READONLY_DATABASE_URL))
    print(f"{panel['ticker'].nunique()} companies, {len(panel)} quarters of history, "
          f"{panel['period_end'].min():%Y-%m-%d} to {panel['period_end'].max():%Y-%m-%d}")
    results = run_backtest(panel)
    summary = summarize(results)

    shown = summary.assign(mape=lambda d: (d["mape"] * 100).round(2), median_ape=lambda d: (d["median_ape"] * 100).round(2),
                           beats_reference=lambda d: (d["beats_reference"] * 100).round(0))
    print("\nNext-quarter revenue, tested on", results["cutoff"].nunique(), "out-of-sample quarters\n")
    print(shown[["model", "mape", "median_ape", "forecasts", "beats_reference"]].rename(columns={
        "mape": "MAPE %", "median_ape": "median %", "beats_reference": f"beats {REFERENCE} %"}).to_string(index=False))

    best = summary.iloc[0]["model"]
    per_company = results[results["model"] == best].groupby("ticker")["ape"].mean().sort_values() * 100
    print(f"\nBest model: {best}. Its error by company (MAPE %):")
    print(per_company.round(1).to_string())
    save_results(summary, results)
    draw_chart(summary)
    print(f"\nSaved {RESULT_FILE} and {CHART_FILE}")


if __name__ == "__main__":
    main()