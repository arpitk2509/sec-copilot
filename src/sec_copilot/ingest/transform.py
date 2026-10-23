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
        "SalesRevenueGoodsNet",
    ],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
}

# How long a period is, in days (end date minus start date).
QUARTER_DAYS = (70, 105)   # about 3 months
YEAR_DAYS = (350, 380)     # about 12 months
DAYS_PER_QUARTER = 91.3125

# A derived Q4 outside this multiple of the Q1-Q3 average is suspect (see _derive_q4).
Q4_RATIO_RANGE = (0.3, 4.0)

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


def fiscal_year_of(end: pd.Timestamp) -> int:
    """A fiscal year is named after the calendar year it ends in.

    A year ending in the first week of January (52/53-week calendars) counts as the previous year.
    """
    if end.month == 1 and end.day <= 7:
        return end.year - 1
    return end.year


def _add_fiscal_labels(df: pd.DataFrame, anchor_end: pd.Timestamp) -> pd.DataFrame:
    """Give every row our own fiscal year and quarter number.

    We count 13-week steps back from the latest fiscal year end, because the SEC's own
    fy/fp labels are sometimes wrong. A row ending exactly on a fiscal year end gets quarter 4.
    """
    anchor_index = fiscal_year_of(anchor_end) * 4        # position of that year's Q4
    quarters_back = ((anchor_end - df["end"]).dt.days / DAYS_PER_QUARTER).round().astype(int)
    index = anchor_index - quarters_back
    return df.assign(
        fiscal_year=(index - 1) // 4 + 1,
        fiscal_quarter=(index - 1) % 4 + 1,
    )


def _derive_q4(df: pd.DataFrame, label: str = "") -> pd.DataFrame:
    """Q4 is never reported on its own, so: Q4 = full year - (Q1 + Q2 + Q3).

    Safety check: after a spin-off or restatement, the full year can be on a different
    basis than the quarters filed earlier. If revenue's Q4 is absurdly small or large next to
    that year's other quarters, we skip the whole derived Q4 rather than store a wrong number.
    """
    new_rows = []
    suspect_years = set()
    for (metric, fiscal_year), group in df.groupby(["metric", "fiscal_year"]):
        by_period = group.set_index("fiscal_period")
        if not {"Q1", "Q2", "Q3", "FY"} <= set(by_period.index):
            continue  # a piece is missing, so we can't derive Q4

        year_row = by_period.loc["FY"]
        q3_row = by_period.loc["Q3"]
        q1_to_q3 = by_period.loc[["Q1", "Q2", "Q3"], "value"].sum()
        q4_value = year_row["value"] - q1_to_q3

        if metric == "revenue":
            ratio = q4_value / (q1_to_q3 / 3) if q1_to_q3 > 0 else 0
            if not Q4_RATIO_RANGE[0] <= ratio <= Q4_RATIO_RANGE[1]:
                suspect_years.add(fiscal_year)

        new_rows.append({
            "metric": metric,
            "period_start": q3_row["period_end"] + pd.Timedelta(days=1),
            "period_end": year_row["period_end"],
            "fiscal_year": fiscal_year,
            "fiscal_period": "Q4",
            "value": q4_value,
            "unit": "USD",
            "accession_no": year_row["accession_no"],
            "is_derived": True,
        })

    result = pd.DataFrame(new_rows, columns=OUTPUT_COLUMNS)
    if suspect_years:
        print(f"  {label}: skipped derived Q4 for fiscal years {sorted(suspect_years)} "
              "(full-year and quarterly numbers look to be on different bases)")
        result = result[~result["fiscal_year"].isin(suspect_years)]
    return result


def facts_to_frame(raw: dict, label: str = "") -> pd.DataFrame:
    """Raw SEC JSON in, clean table out: one row per metric per fiscal quarter (and year)."""
    empty = pd.DataFrame(columns=OUTPUT_COLUMNS)
    df = raw_facts(raw)
    if df.empty:
        return empty

    quarters = _current_period_rows(df, "10-Q", QUARTER_DAYS)
    years = _current_period_rows(df, "10-K", YEAR_DAYS)
    if years.empty:
        return empty  # without a 10-K we have no fiscal year end to count from

    anchor_end = years["end"].max()
    quarters = _add_fiscal_labels(quarters, anchor_end)
    years = _add_fiscal_labels(years, anchor_end)

    # 10-Q rows must land on Q1-Q3, and 10-K rows on the end of a year (Q4 position).
    quarters = quarters[quarters["fiscal_quarter"].isin([1, 2, 3])]
    quarters = quarters.assign(fiscal_period="Q" + quarters["fiscal_quarter"].astype(str))
    years = years[years["fiscal_quarter"] == 4].assign(fiscal_period="FY")

    clean = pd.concat([quarters, years], ignore_index=True)
    clean = clean.rename(columns={
        "start": "period_start", "end": "period_end", "val": "value", "accn": "accession_no",
    })
    clean["unit"] = "USD"
    clean["is_derived"] = False
    clean = clean[OUTPUT_COLUMNS]

    # If a period somehow appears twice, keep the most recent one.
    clean = clean.sort_values("period_end").drop_duplicates(
        ["metric", "fiscal_year", "fiscal_period"], keep="last"
    )

    q4 = _derive_q4(clean, label)
    result = pd.concat([clean, q4], ignore_index=True)
    return result.sort_values(["metric", "period_end"]).reset_index(drop=True)