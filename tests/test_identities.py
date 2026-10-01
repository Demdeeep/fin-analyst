"""Accounting-identity tests.

Unit tests confirm each function matches a formula written on paper. These
confirm the *definitions agree with each other*, which catches a different and
more dangerous class of error: a formula that is internally consistent but
financeably wrong (counting a current portion of long-term debt twice, defining
debt to exclude cash, and so on).

Each identity below must hold for every row of the real sample data, where
several ratios are legitimately ``None`` because of INIT's losses and negative
equity -- those rows are skipped rather than excused.
"""

from __future__ import annotations

import pytest
import pandas as pd

from financial_analyst import config
from financial_analyst.tools import calculations as calc


def rows_where(frame, *columns):
    """Rows where every named column is present and not NaN."""
    mask = frame[list(columns)].notna().all(axis=1)
    return frame[mask]


def undefined(value) -> bool:
    """Is this metric undefined?

    The pure functions return ``None``, but pandas stores that as ``NaN`` once
    the values land in a column. Both spellings mean the same thing, and the
    distinction the project actually cares about is "undefined" versus "a
    number" -- never ``None`` versus ``NaN``.
    """
    return value is None or pd.isna(value)


def with_source_columns(metrics, dataset, *columns):
    """The metrics frame joined back to the raw columns a test needs."""
    return metrics.merge(
        dataset.frame[["ticker", "fiscal_year", *columns]],
        on=["ticker", "fiscal_year"],
        how="left",
    )


def test_balance_sheet_balances_on_every_sample_row(dataset):
    for row in dataset.frame.to_dict("records"):
        label = f"{row['ticker']}:{row['fiscal_year']}"
        residual = calc.balance_sheet_residual(
            row["total_assets"], row["total_liabilities"], row["total_equity"]
        )
        assert residual == pytest.approx(0.0, abs=config.BALANCE_TOLERANCE), label


def test_income_statement_recomputes_on_every_sample_row(dataset):
    for row in dataset.frame.to_dict("records"):
        label = f"{row['ticker']}:{row['fiscal_year']}"
        residuals = calc.income_statement_residuals(row)
        assert residuals, f"{label}: nothing was actually checked"
        for line, residual in residuals.items():
            assert residual == pytest.approx(0.0, abs=config.INCOME_STATEMENT_TOLERANCE), (
                f"{label}: {line}"
            )


def test_ebitda_equals_ebit_plus_depreciation_on_every_row(dataset):
    rows = rows_where(dataset.frame, "ebitda", "operating_income",
                      "depreciation_amortization")
    assert len(rows) > 0
    for row in rows.to_dict("records"):
        expected = calc.ebitda_from_components(
            row["operating_income"], row["depreciation_amortization"]
        )
        assert row["ebitda"] == pytest.approx(expected, abs=config.INCOME_STATEMENT_TOLERANCE)


def test_free_cash_flow_equals_cfo_minus_capex_on_every_row(dataset):
    for row in dataset.frame.to_dict("records"):
        expected = calc.free_cash_flow(row["cash_from_operations"], row["capex"])
        assert row["free_cash_flow"] == pytest.approx(expected)


def test_dupont_identity_holds_for_every_valid_row(metrics):
    """ROE == net margin x asset turnover x equity multiplier."""
    rows = rows_where(metrics, "return_on_equity", "net_margin", "asset_turnover",
                      "equity_multiplier")
    # INIT FY2022 has negative equity and is correctly excluded by the guard.
    assert len(rows) >= len(metrics) - 2
    for row in rows.to_dict("records"):
        rebuilt = row["net_margin"] * row["asset_turnover"] * row["equity_multiplier"]
        assert row["return_on_equity"] == pytest.approx(rebuilt, rel=1e-9)


def test_roa_decomposition_holds(metrics):
    """ROA == net margin x asset turnover."""
    rows = rows_where(metrics, "return_on_assets", "net_margin", "asset_turnover")
    for row in rows.to_dict("records"):
        rebuilt = row["net_margin"] * row["asset_turnover"]
        assert row["return_on_assets"] == pytest.approx(rebuilt, rel=1e-9)


def test_debt_identities_are_consistent(metrics, dataset):
    # cash_and_equivalents is already carried by the metrics frame, so merging
    # it again would produce _x/_y columns.
    joined = with_source_columns(metrics, dataset, "short_term_debt", "long_term_debt")
    rows = rows_where(joined, "total_debt", "net_debt")
    assert len(rows) > 0
    for row in rows.to_dict("records"):
        assert row["total_debt"] == pytest.approx(
            row["short_term_debt"] + row["long_term_debt"]
        )
        assert row["net_debt"] == pytest.approx(row["total_debt"] - row["cash_and_equivalents"])


def test_enterprise_value_equals_market_cap_plus_net_debt(metrics):
    rows = rows_where(metrics, "enterprise_value", "market_cap", "net_debt")
    for row in rows.to_dict("records"):
        assert row["enterprise_value"] == pytest.approx(
            row["market_cap"] + row["net_debt"], rel=1e-9
        )


def test_per_share_identities(metrics, dataset):
    joined = with_source_columns(metrics, dataset, "diluted_shares")
    rows = rows_where(joined, "earnings_per_share", "net_income", "diluted_shares")
    for row in rows.to_dict("records"):
        assert row["earnings_per_share"] == pytest.approx(
            row["net_income"] / row["diluted_shares"], rel=1e-9
        )


def test_cagr_agrees_with_the_endpoint_ratio(metrics):
    """(1 + CAGR) ** span must reproduce the first/last revenue ratio."""
    for ticker, group in metrics.groupby("ticker"):
        row = group.iloc[0]
        span = row["last_year"] - row["first_year"]
        if span == 0:
            continue
        first = group["revenue"].iloc[0]
        last = group["revenue"].iloc[-1]
        assert (1 + row["revenue_cagr"]) ** span == pytest.approx(
            last / first, rel=1e-9
        ), ticker


def test_negative_equity_rows_suppress_the_ratios_that_need_it(metrics):
    """The guards are doing real work, not just being defensive."""
    negative = metrics[metrics["total_equity"] < 0]
    assert len(negative) > 0, "sample data should contain a negative-equity row"
    for row in negative.to_dict("records"):
        for column in ("return_on_equity", "debt_to_equity", "price_to_book",
                       "equity_multiplier"):
            assert undefined(row[column]), f"{column} should be undefined"


def test_loss_making_rows_suppress_valuation_multiples(metrics):
    losses = metrics[metrics["net_income"] < 0]
    assert len(losses) > 0
    for row in losses.to_dict("records"):
        assert undefined(row["price_to_earnings"])
        assert undefined(row["fcf_conversion"])


def test_negative_ebitda_rows_suppress_net_debt_to_ebitda(metrics):
    negative = metrics[metrics["ebitda"] < 0]
    assert len(negative) > 0
    for row in negative.to_dict("records"):
        assert undefined(row["net_debt_to_ebitda"])


def test_no_metric_is_ever_infinite(metrics):
    """A single ``inf`` in the table would poison every downstream average."""
    numeric = metrics.select_dtypes("number")
    assert not numeric.isin([float("inf"), float("-inf")]).any().any()
