"""Five ways to forecast next quarter's revenue, from trivial to flexible.

Every model has the same shape: it gets TRAIN rows (the past), TEST rows (the quarters to predict) and each
company's revenue HISTORY up to the cutoff, and returns predicted revenue for each test row.
"""
import warnings

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from statsmodels.tsa.holtwinters import ExponentialSmoothing

from sec_copilot.ml.features import FEATURES


def seasonal_naive(train, test, history):
    """Revenue will equal the same quarter last year. The simplest sensible forecast."""
    return test["y_lag4"]


def yoy_trend(train, test, history):
    """Same quarter last year, grown by the year-over-year growth of the most recent quarter."""
    return test["y_lag4"] * np.exp(test["g1"])


def ets(train, test, history):
    """Exponential smoothing with trend and seasonality, fitted on each company's own history (in logs)."""
    predictions = {}
    for index, row in test.iterrows():
        series = history[history["ticker"] == row["ticker"]].sort_values("period_end")["revenue"]
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                fit = ExponentialSmoothing(np.log(series.to_numpy()), trend="add", damped_trend=True, seasonal="add",
                                           seasonal_periods=4, initialization_method="estimated").fit()
                predictions[index] = float(np.exp(fit.forecast(1)[0]))
        except Exception:  # too little history or a failed fit: fall back to the trend baseline
            predictions[index] = float(row["y_lag4"] * np.exp(row["g1"]))
    return pd.Series(predictions)


def _fit_and_predict(model, train, test):
    rows = train.dropna(subset=FEATURES + ["target"])
    model.fit(rows[FEATURES], rows["target"])
    return test["y_lag4"] * np.exp(model.predict(test[FEATURES]))     # the model predicts growth, not revenue


def ridge(train, test, history):
    """One linear model shared by all companies, predicting year-over-year growth."""
    return _fit_and_predict(make_pipeline(StandardScaler(), Ridge(alpha=3.0)), train, test)


def lightgbm(train, test, history):
    """Gradient-boosted trees shared by all companies. Small and heavily regularised, because the data is small."""
    from lightgbm import LGBMRegressor

    model = LGBMRegressor(n_estimators=200, learning_rate=0.03, num_leaves=7, min_child_samples=15,
                          subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0,
                          random_state=0, n_jobs=1, deterministic=True, force_row_wise=True, verbose=-1)
    return _fit_and_predict(model, train, test)


MODELS = {"seasonal_naive": seasonal_naive, "yoy_trend": yoy_trend, "ets": ets, "ridge": ridge, "lightgbm": lightgbm}