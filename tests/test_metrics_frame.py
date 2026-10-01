"""Tests for the dataframe layer.

The pure maths is covered in ``test_calculations.py``. What is tested here is
the plumbing: grouping, ordering, column layout, and the three promises the
module makes -- it does not mutate its input, companies do not bleed into each
other, and a year-over-year figure is only ever produced across consecutive
years.
"""

from __future__ import annotations

import pandas as pd
import pytest

from financial_analyst.tools.metrics import OUTPUT_COLUMNS, compute_metric_frame

from conftest import make_row


def test_columns_match_the_declared_layout(dataset):
    frame = compute_metric_frame(dataset.frame)
    assert list(frame.columns) == list(OUTPUT_COLUMNS)
    assert len(frame) == len(dataset.frame)


def test_input_frame_is_not_mutated(dataset):
    before = dataset.frame.copy(deep=True)
    compute_metric_frame(dataset.frame)
    pd.testing.assert_frame_equal(before, dataset.frame)


def test_row_order_does_not_change_results(dataset):
    ordered = compute_metric_frame(dataset.frame)
    shuffled = compute_metric_frame(
        dataset.frame.sample(frac=1.0, random_state=7).reset_index(drop=True)
    )
    pd.testing.assert_frame_equal(ordered, shuffled)


def test_first_year_of_each_company_has_no_growth(metrics):
    firsts = metrics[metrics["years_since_prior"].isna()]
    assert len(firsts) == metrics["ticker"].nunique()
    assert firsts["revenue_growth"].isna().all()


def test_cagr_and_span_are_constant_within_a_company(metrics):
    for ticker, group in metrics.groupby("ticker"):
        for column in ("revenue_cagr", "first_year", "last_year", "years_covered",
                       "missing_years"):
            assert group[column].nunique() == 1, f"{ticker}.{column} varies by row"


def test_gapped_year_reports_the_distance_and_no_growth_rate(metrics):
    """GLOB skips FY2023, so its FY2024 row is two years after the previous one."""
    glob = metrics[metrics["ticker"] == "GLOB"].sort_values("fiscal_year")
    last = glob.iloc[-1]
    assert last["fiscal_year"] == 2024
    assert last["years_since_prior"] == 2
    assert pd.isna(last["revenue_growth"]), "a 2-year gap is not year-over-year"


def test_missing_years_and_coverage_are_counted_from_observations(metrics):
    glob = metrics[metrics["ticker"] == "GLOB"].iloc[0]
    assert glob["years_covered"] == 4
    assert glob["missing_years"] == 1
    acme = metrics[metrics["ticker"] == "ACME"].iloc[0]
    assert acme["years_covered"] == 5
    assert acme["missing_years"] == 0


def test_consecutive_years_do_get_a_growth_rate(metrics):
    acme = metrics[metrics["ticker"] == "ACME"].sort_values("fiscal_year")
    assert acme.iloc[0]["years_since_prior"] is None or pd.isna(
        acme.iloc[0]["years_since_prior"]
    )
    assert acme.iloc[1]["years_since_prior"] == 1
    assert acme.iloc[1]["revenue_growth"] == pytest.approx(1320 / 1200 - 1)


def test_companies_do_not_bleed_into_each_other():
    """A's history must not be used as B's prior year."""
    rows = [
        make_row(ticker="AAA", fiscal_year=2020, revenue=1000.0, cogs=600.0,
                 gross_profit=400.0),
        make_row(ticker="AAA", fiscal_year=2021, revenue=1100.0, cogs=660.0,
                 gross_profit=440.0),
        make_row(ticker="BBB", fiscal_year=2020, revenue=5000.0, cogs=3000.0,
                 gross_profit=2000.0),
    ]
    frame = compute_metric_frame(pd.DataFrame(rows))
    bbb = frame[frame["ticker"] == "BBB"].iloc[0]
    assert pd.isna(bbb["years_since_prior"])
    assert pd.isna(bbb["revenue_growth"])
    assert bbb["first_year"] == 2020 and bbb["last_year"] == 2020
    assert pd.isna(bbb["revenue_cagr"]), "a single year cannot have a CAGR"


def test_undefined_metrics_are_blank_not_nan_strings(healthy_context):
    """Undefined values must survive as nulls, never as text."""
    metrics = healthy_context.metrics
    text_columns = metrics.select_dtypes(include=["object", "string"])
    for column in text_columns.columns:
        rendered = text_columns[column].astype(str).str.strip().str.lower()
        assert not rendered.isin({"nan", "none", "null"}).any(), column


def test_healthy_fixture_really_is_healthy(healthy_context):
    """Guards the fixture itself: if this breaks, the negative tests lie."""
    from financial_analyst.tools import anomalies as an

    assert an.run_anomalies(healthy_context) == []
    metrics = healthy_context.metrics
    assert metrics["revenue_growth"].dropna().gt(0).all()
    assert metrics["gross_margin"].nunique() == 1
