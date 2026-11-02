"""Load each company's quarterly revenue history from the database, ready for forecasting."""
from datetime import date

import pandas as pd
from sqlalchemy import text

MIN_HISTORY = 16        # a company needs this many consecutive quarters, counting back from its latest one
START_DATE = date(2014, 1, 1)

QUERY = text("""
    SELECT ticker, fiscal_year, fiscal_period, period_end, revenue
    FROM v_quarterly_metrics
    WHERE revenue IS NOT NULL AND period_end >= :start
    ORDER BY ticker, period_end
""")


def latest_unbroken_run(group: pd.DataFrame) -> pd.DataFrame:
    """Keep the most recent stretch of consecutive quarters. A missing quarter ends the usable history."""
    group = group.sort_values("q_index")
    run_id = (group["q_index"].diff().fillna(1) != 1).cumsum()
    return group[run_id == run_id.iloc[-1]]


def clean_panel(df: pd.DataFrame) -> pd.DataFrame:
    """Per company: the latest gap-free stretch of quarters. Companies with too little history are dropped."""
    panel = pd.concat([latest_unbroken_run(g) for _, g in df.groupby("ticker")], ignore_index=True)
    sizes = panel.groupby("ticker").size()
    panel = panel[panel["ticker"].isin(sizes[sizes >= MIN_HISTORY].index)]
    return panel.sort_values(["ticker", "period_end"]).reset_index(drop=True)


def load_revenue_panel(engine, start: date = START_DATE) -> pd.DataFrame:
    df = pd.read_sql(QUERY, engine, params={"start": start}, parse_dates=["period_end"])
    df["revenue"] = df["revenue"].astype(float)
    df["quarter"] = df["fiscal_period"].str[-1].astype(int)
    df["q_index"] = df["fiscal_year"] * 4 + df["quarter"]
    return clean_panel(df[["ticker", "period_end", "quarter", "q_index", "revenue"]])