"""Turn a revenue history into model inputs. Every feature looks only at quarters BEFORE the one being predicted."""
import numpy as np
import pandas as pd

FEATURES = ["g1", "g2", "g3", "g4", "qoq1", "seasonal_qoq", "q1", "q2", "q3", "q4"]
QUARTER_DAYS = 91


def make_features(panel: pd.DataFrame) -> pd.DataFrame:
    """One row per company-quarter.

    target        = log growth versus the same quarter last year (what the models predict)
    g1..g4        = that same year-over-year growth, for the 1..4 quarters before this one
    qoq1          = growth from two quarters ago to last quarter
    seasonal_qoq  = the same quarter-to-quarter step one year ago (captures the seasonal shape)
    q1..q4        = which fiscal quarter this is
    """
    frames = []
    for ticker, group in panel.groupby("ticker", sort=False):
        group = group.sort_values("period_end").reset_index(drop=True)
        log_revenue = np.log(group["revenue"])
        out = pd.DataFrame({"ticker": ticker, "period_end": group["period_end"],
                            "quarter": group["quarter"], "revenue": group["revenue"]})
        out["y_lag4"] = group["revenue"].shift(4)
        out["target"] = log_revenue - np.log(out["y_lag4"])
        for k in range(1, 5):
            out[f"g{k}"] = log_revenue.shift(k) - log_revenue.shift(k + 4)
        out["qoq1"] = log_revenue.shift(1) - log_revenue.shift(2)
        out["seasonal_qoq"] = log_revenue.shift(4) - log_revenue.shift(5)
        for q in range(1, 5):
            out[f"q{q}"] = (out["quarter"] == q).astype(float)
        frames.append(out)
    return pd.concat(frames, ignore_index=True)


def with_next_quarter(panel: pd.DataFrame) -> pd.DataFrame:
    """Add one empty row per company for the quarter after its last one, so its features can be built."""
    extra = []
    for ticker, group in panel.groupby("ticker"):
        last = group.sort_values("period_end").iloc[-1]
        extra.append({"ticker": ticker, "period_end": last["period_end"] + pd.Timedelta(days=QUARTER_DAYS),
                      "quarter": int(last["quarter"]) % 4 + 1, "q_index": last["q_index"] + 1, "revenue": np.nan})
    return pd.concat([panel, pd.DataFrame(extra)], ignore_index=True).sort_values(["ticker", "period_end"])