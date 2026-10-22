"""Turn the SEC's raw company-facts JSON into a clean table of quarterly and annual values."""
import pandas as pd

# Our metric name -> the SEC (us-gaap) tags that can hold it, best choice first.
# Companies pick different tags, and some switch tags over the years.
METRIC_TAGS = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
}

# How long a period is, in days (end date minus start date).
QUARTER_DAYS = (70, 105)   # about 3 months
YEAR_DAYS = (350, 380)     # about 12 months

OUTPUT_COLUMNS = [
    "metric", "period_start", "period_end", "fiscal_year",
    "fiscal_period", "value", "unit", "accession_no", "is_derived",
]


def raw_facts(raw: dict) -> pd.DataFrame:
    """Flatten the JSON into one row per reported fact, for the metrics we care about."""
    gaap = raw.get("facts", {}).get("us-gaap", {})
    pieces = []
    for metric, tags in METRIC_TAGS.items():
        for rank, tag in enumerate(tags):
            rows = gaap.get(tag, {}).get("units", {}).get("USD", [])
            if not rows:
                continue
            piece = pd.DataFrame(rows)
            piece["metric"] = metric
            piece["tag_rank"] = rank   # 0 = our first choice of tag
            pieces.append(piece)

    if not pieces:
        return pd.DataFrame()

    df = pd.concat(pieces, ignore_index=True)
    df = df[df["form"].isin(["10-K", "10-Q"])].copy()
    df["start"] = pd.to_datetime(df["start"])
    df["end"] = pd.to_datetime(df["end"])
    df["days"] = (df["end"] - df["start"]).dt.days
    return df


def _current_period_rows(df: pd.DataFrame, form: str, day_range: tuple) -> pd.DataFrame:
    """Keep only the period each filing is actually about.

    A filing also repeats last year's numbers for comparison. Within one filing,
    the row with the newest end date is the current period, and the rest are comparisons.
    If two tags report the same period, the higher-priority tag wins.
    """
    low, high = day_range
    sub = df[(df["form"] == form) & df["days"].between(low, high)]
    sub = sub.sort_values(["end", "tag_rank"], ascending=[False, True])
    return sub.drop_duplicates(["metric", "accn"], keep="first")


def _derive_q4(df: pd.DataFrame) -> pd.DataFrame:
    """Q4 is never reported on its own, so: Q4 = full year - (Q1 + Q2 + Q3)."""
    new_rows = []
    for (metric, fiscal_year), group in df.groupby(["metric", "fiscal_year"]):
        by_period = group.set_index("fiscal_period")
        if not {"Q1", "Q2", "Q3", "FY"} <= set(by_period.index):
            continue  # a piece is missing, so we can't derive Q4

        year_row = by_period.loc["FY"]
        q3_row = by_period.loc["Q3"]
        q1_to_q3 = by_period.loc[["Q1", "Q2", "Q3"], "value"].sum()

        new_rows.append({
            "metric": metric,
            "period_start": q3_row["period_end"] + pd.Timedelta(days=1),
            "period_end": year_row["period_end"],
            "fiscal_year": fiscal_year,
            "fiscal_period": "Q4",
            "value": year_row["value"] - q1_to_q3,
            "unit": "USD",
            "accession_no": year_row["accession_no"],
            "is_derived": True,
        })
    return pd.DataFrame(new_rows, columns=OUTPUT_COLUMNS)


def facts_to_frame(raw: dict) -> pd.DataFrame:
    """Raw SEC JSON in, clean table out: one row per metric per fiscal quarter (and year)."""
    df = raw_facts(raw)
    if df.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    quarters = _current_period_rows(df, "10-Q", QUARTER_DAYS)
    years = _current_period_rows(df, "10-K", YEAR_DAYS)
    clean = pd.concat([quarters, years], ignore_index=True)

    clean = clean.rename(columns={
        "start": "period_start", "end": "period_end", "fy": "fiscal_year",
        "fp": "fiscal_period", "val": "value", "accn": "accession_no",
    })
    clean["unit"] = "USD"
    clean["is_derived"] = False
    clean = clean[OUTPUT_COLUMNS]

    # Rows without a fiscal label can't be placed in a quarter, so drop them.
    clean = clean.dropna(subset=["fiscal_year", "fiscal_period"])
    clean["fiscal_year"] = clean["fiscal_year"].astype(int)

    # If a period somehow appears twice, keep the most recent one.
    clean = clean.sort_values("period_end").drop_duplicates(
        ["metric", "fiscal_year", "fiscal_period"], keep="last"
    )

    q4 = _derive_q4(clean)
    result = pd.concat([clean, q4], ignore_index=True)
    return result.sort_values(["metric", "period_end"]).reset_index(drop=True)