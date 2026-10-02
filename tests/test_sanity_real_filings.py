"""Sanity check against real filings.

Every other test in this suite proves the code agrees with itself: each function
matches a formula, and the formulas agree with each other. That cannot catch a
definition that is *consistently* wrong. This module closes that gap by running
the pipeline over figures taken from a real 10-K and comparing the results with
what a finance professional would recognise.

Source
------
`tests/fixtures/apple_fy2022_2024.csv`, built from SEC EDGAR XBRL companyfacts
for Apple Inc. (CIK 0000320193), annual 10-K values, retrieved 2026-10-01.
Accessions: FY2022 0000320193-24-000123, FY2023 and FY2024 0000320193-25-000079.
All figures are USD millions except share counts (millions of shares) and EPS
(USD per share).

The expected values below are worked out from those filed figures by hand and
written as the round numbers an analyst would quote. They are deliberately not
recomputed with the code under test.

HOW TO EXTEND
-------------
Add a company by adding its annual 10-K figures to a CSV of the same shape and
writing the expectations a reader would check against. One real company per
industry shape is worth more than four synthetic ones.
"""

from __future__ import annotations

import pandas as pd
import pytest

from financial_analyst import config
from financial_analyst.tools import anomalies as an
from financial_analyst.tools import calculations as calc
from financial_analyst.tools.loaders import load_financials
from financial_analyst.tools.metrics import compute_metric_frame

APPLE = "tests/fixtures/apple_fy2022_2024.csv"

#: Diluted EPS as Apple itself reported it, from the EarningsPerShareDiluted tag
#: in the same filings. An external anchor: no part of this project computes
#: this number, so agreement validates the EPS definition *and* the share-count
#: scaling (millions, not units) at the same time.
FILED_EPS = {2022: 6.11, 2023: 6.13, 2024: 6.08}


@pytest.fixture(scope="module")
def apple_frame():
    return load_financials(APPLE).frame


@pytest.fixture(scope="module")
def apple_metrics(apple_frame):
    return compute_metric_frame(apple_frame)


def row_for(metrics, fiscal_year):
    return metrics[metrics["fiscal_year"] == fiscal_year].iloc[0]


# ---------------------------------------------------------------------------
# The identities that must hold on real data
# ---------------------------------------------------------------------------
def test_balance_sheet_balances_on_real_filings(apple_frame):
    """A = L + E held exactly on all three filed years."""
    for row in apple_frame.to_dict("records"):
        residual = calc.balance_sheet_residual(
            row["total_assets"], row["total_liabilities"], row["total_equity"]
        )
        assert residual == pytest.approx(0.0, abs=config.BALANCE_TOLERANCE), row[
            "fiscal_year"
        ]


def test_reported_gross_profit_equals_revenue_less_cogs(apple_frame):
    for row in apple_frame.to_dict("records"):
        assert row["gross_profit"] == pytest.approx(row["revenue"] - row["cogs"], abs=0.1)


def test_reported_operating_income_equals_gross_profit_less_opex(apple_frame):
    for row in apple_frame.to_dict("records"):
        assert row["operating_income"] == pytest.approx(
            row["gross_profit"] - row["operating_expenses"], abs=0.1
        )


def test_reported_net_income_equals_pretax_less_tax(apple_frame):
    for row in apple_frame.to_dict("records"):
        assert row["net_income"] == pytest.approx(
            row["pretax_income"] - row["tax_expense"], abs=0.1
        )


def test_free_cash_flow_and_ebitda_derive_correctly_on_real_filings(apple_frame):
    for row in apple_frame.to_dict("records"):
        assert row["free_cash_flow"] == pytest.approx(
            row["cash_from_operations"] - row["capex"], abs=0.1
        )
        assert row["ebitda"] == pytest.approx(
            row["operating_income"] + row["depreciation_amortization"], abs=0.1
        )


# ---------------------------------------------------------------------------
# The ratios, as an analyst would quote them
# ---------------------------------------------------------------------------
#: FY2024 expectations, worked out from the filed figures:
#:   gross margin     180,683 / 391,035 = 46.2%
#:   operating margin 123,216 / 391,035 = 31.5%
#:   net margin        93,736 / 391,035 = 24.0%
#:   ROE               93,736 /  56,950 = 164.6%
#:   ROA               93,736 / 364,980 = 25.7%
#:   debt/assets    96,662 / 364,980 = 26.5%   (10,912 + 85,750 total debt)
#:   debt/equity    96,662 /  56,950 = 1.70x
#:   current ratio  152,987 / 176,392 = 0.87x
#:   FCF              118,254 -   9,447 = 108,807
@pytest.mark.parametrize(
    "column, expected, tolerance",
    [
        ("gross_margin", 0.462, 0.0005),
        ("operating_margin", 0.315, 0.0005),
        ("net_margin", 0.240, 0.0005),
        ("return_on_equity", 1.646, 0.001),
        ("return_on_assets", 0.257, 0.0005),
        ("debt_to_assets", 0.265, 0.0005),
        ("debt_to_equity", 1.70, 0.005),
        ("current_ratio", 0.867, 0.001),
        ("free_cash_flow", 108807.0, 0.5),
    ],
)
def test_fy2024_ratios_match_hand_worked_values(apple_metrics, column, expected, tolerance):
    actual = row_for(apple_metrics, 2024)[column]
    assert actual == pytest.approx(expected, abs=tolerance)


def test_apple_roe_above_100_percent_is_reproducible(apple_metrics):
    """A ROE of ~165% looks wrong until you see the cause.

    Apple buys back so much stock that equity is a small base: $56.9bn of equity
    against $364.98bn of assets, an equity multiplier of only 6.4x. The number
    is correct, and it is the kind of result a naive analyst would discard.

    This test exists so the sanity check demonstrates the tool *reproduces a
    surprising-but-right figure*, not only tidy ones.
    """
    row = row_for(apple_metrics, 2024)
    assert row["return_on_equity"] == pytest.approx(1.646, abs=0.001)
    assert row["equity_multiplier"] == pytest.approx(364980 / 56950, rel=1e-9)
    assert row["equity_multiplier"] < 10


def test_eps_matches_the_figure_apple_filed(apple_metrics):
    """External anchor: this number is not produced by this project."""
    for fiscal_year, filed in FILED_EPS.items():
        computed = row_for(apple_metrics, fiscal_year)["earnings_per_share"]
        assert computed == pytest.approx(filed, abs=0.005), fiscal_year


def test_valuation_metrics_are_undefined_without_market_data(apple_metrics):
    """The filings carry no share price, so P/E must be n/a, not invented."""
    for row in apple_metrics.to_dict("records"):
        assert pd.isna(row["price_to_earnings"])
        assert pd.isna(row["market_cap"])


def test_interest_coverage_undefined_for_fy2024_where_interest_is_untagged(apple_metrics):
    """Missing source data must produce n/a, never a fabricated coverage ratio."""
    assert pd.isna(row_for(apple_metrics, 2024)["interest_coverage"])
    # ...while the years that do report interest still compute it.
    assert row_for(apple_metrics, 2022)["interest_coverage"] == pytest.approx(
        119437 / 2931, rel=1e-9
    )


def test_revenue_decline_in_fy2023_is_detected(apple_metrics):
    """Apple's revenue fell in FY2023; a correct pipeline must show that."""
    growth = row_for(apple_metrics, 2023)["revenue_growth"]
    assert growth == pytest.approx(383285 / 394328 - 1)
    assert growth < 0


# ---------------------------------------------------------------------------
# The identity that real data once broke -- now fixed
# ---------------------------------------------------------------------------
def test_pretax_identity_holds_on_real_filings(apple_frame):
    """The EBIT -> pre-tax bridge, using the line Apple actually reports.

    This is the check that used to fail. We had assumed
    ``pretax == operating_income - interest_expense``, which left residuals of
    +2,597 (FY2022) and +3,368 (FY2023). The real bridge is the single net
    non-operating line as a company presents it, interest included:

        FY2022  119,437 + (-334) = 119,103
        FY2023  114,301 + (-565) = 113,736
        FY2024  123,216 + ( 269) = 123,485

    All three now balance exactly. See docs/stage1.md.
    """
    expected_other_income = {2022: -334.0, 2023: -565.0, 2024: 269.0}
    for fiscal_year, other_income in expected_other_income.items():
        row = apple_frame[apple_frame["fiscal_year"] == fiscal_year].iloc[0]
        assert row["other_income"] == pytest.approx(other_income, abs=0.1)
        assert row["operating_income"] + row["other_income"] == pytest.approx(
            row["pretax_income"], abs=0.1
        )
        residuals = calc.income_statement_residuals(row)
        assert residuals["pretax_income"] == pytest.approx(0.0, abs=0.1)
        # All four identities, every year.
        for line, residual in residuals.items():
            assert residual == pytest.approx(0.0, abs=0.1), line


def test_no_false_alarm_reaches_the_report(apple_metrics, apple_frame):
    """The regression test for the bug: a clean filing must raise nothing."""
    dataset = load_financials(APPLE)
    assert dataset.warnings == (), dataset.warnings
    context = an.AnalysisContext(metrics=apple_metrics, source=apple_frame)
    assert an.identity_mismatch(context) == []


def test_the_old_assumption_is_not_silently_reinstated(apple_frame):
    """Guards against someone 'simplifying' the identity back.

    If ``other_income`` were defaulted to zero, this file would start producing
    critical flags again -- quietly, and only on real data.
    """
    row = apple_frame[apple_frame["fiscal_year"] == 2022].iloc[0]
    without_other_income = {k: v for k, v in row.items() if k != "other_income"}
    residuals = calc.income_statement_residuals(without_other_income)
    assert "pretax_income" not in residuals, "a missing bridge must not be guessed"


