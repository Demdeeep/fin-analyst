"""Tests for CSV loading and validation.

The loader's job is to decide what is fatal and what is merely noteworthy.
These tests pin both halves of that decision, because the failure modes are
opposite: a raised error on good data stops the pipeline, and a swallowed error
on bad data produces confidently wrong numbers.
"""

from __future__ import annotations

import pandas as pd
import pytest

from financial_analyst.tools.loaders import (
    FinancialsValidationError,
    find_missing_years,
    load_financials,
)

from conftest import make_row


def write_csv(tmp_path, rows, name="financials.csv"):
    path = tmp_path / name
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


# ---------------------------------------------------------------------------
# The happy path, on the real sample file
# ---------------------------------------------------------------------------
def test_sample_loads(dataset):
    assert dataset.tickers == ("ACME", "GLOB", "INIT", "VCTR")
    assert len(dataset.frame) == 19
    assert not dataset.has_warnings, dataset.warnings


def test_optional_columns_are_derived_where_blank(dataset):
    # The sample file omits ebitda for GLOB and free_cash_flow for VCTR.
    assert set(dataset.imputed_columns) == {"ebitda", "free_cash_flow"}
    glob = dataset.frame[dataset.frame["ticker"] == "GLOB"].iloc[0]
    assert glob["ebitda"] == pytest.approx(
        glob["operating_income"] + glob["depreciation_amortization"]
    )
    vctr = dataset.frame[dataset.frame["ticker"] == "VCTR"].iloc[0]
    assert vctr["free_cash_flow"] == pytest.approx(
        vctr["cash_from_operations"] - vctr["capex"]
    )


def test_raw_frame_still_shows_the_blanks(dataset):
    """A later stage must be able to see which cells were derived."""
    assert dataset.raw_frame["ebitda"].isna().sum() > 0
    assert dataset.frame["ebitda"].isna().sum() == 0


def test_frame_is_sorted_by_ticker_then_year(dataset):
    ordered = dataset.frame[["ticker", "fiscal_year"]].values.tolist()
    assert ordered == sorted(ordered)


# ---------------------------------------------------------------------------
# Structural errors
# ---------------------------------------------------------------------------
def test_missing_required_column_is_fatal(tmp_path):
    row = make_row()
    row.pop("total_equity")
    path = write_csv(tmp_path, [row])
    with pytest.raises(FinancialsValidationError) as error:
        load_financials(path)
    assert "total_equity" in str(error.value)


def test_missing_file_is_fatal(tmp_path):
    with pytest.raises(FinancialsValidationError, match="not found"):
        load_financials(tmp_path / "nope.csv")


def test_header_only_file_is_fatal(tmp_path):
    path = tmp_path / "empty.csv"
    pd.DataFrame(columns=[*make_row().keys()]).to_csv(path, index=False)
    with pytest.raises(FinancialsValidationError, match="no data rows"):
        load_financials(path)


def test_duplicate_company_year_is_fatal(tmp_path):
    path = write_csv(tmp_path, [make_row(), make_row()])
    with pytest.raises(FinancialsValidationError, match="duplicate"):
        load_financials(path)


def test_non_numeric_cell_is_fatal_and_is_named(tmp_path):
    row = make_row()
    row["revenue"] = "not a number"
    path = write_csv(tmp_path, [row])
    with pytest.raises(FinancialsValidationError) as error:
        load_financials(path)
    message = str(error.value)
    assert "revenue" in message
    assert "AAA:2020" in message, "the error should name the offending cell"


def test_non_usd_currency_is_fatal_rather_than_converted(tmp_path):
    row = make_row()
    row["currency"] = "EUR"
    path = write_csv(tmp_path, [row])
    with pytest.raises(FinancialsValidationError, match="EUR"):
        load_financials(path)


def test_mixed_currencies_are_fatal(tmp_path):
    # Different fiscal_year, otherwise the duplicate-key check would fire first
    # and mask the currency problem this test is about.
    path = write_csv(tmp_path, [make_row(), make_row(currency="GBP", fiscal_year=2021)])
    with pytest.raises(FinancialsValidationError, match="GBP"):
        load_financials(path)


def test_fractional_fiscal_year_is_fatal(tmp_path):
    path = write_csv(tmp_path, [make_row(fiscal_year=2020.5)])
    with pytest.raises(FinancialsValidationError, match="fiscal_year"):
        load_financials(path)


def test_blank_optional_cell_is_fine(tmp_path):
    row = make_row()
    row["ebitda"] = None
    row["free_cash_flow"] = None
    dataset = load_financials(write_csv(tmp_path, [row]))
    assert dataset.frame.iloc[0]["ebitda"] == pytest.approx(130.0)


# ---------------------------------------------------------------------------
# Non-fatal problems surface as warnings
# ---------------------------------------------------------------------------
def test_identity_break_is_a_warning_not_an_exception(tmp_path):
    row = make_row()
    row["total_equity"] = 500.0          # assets 1000 != liabilities 400 + 500
    dataset = load_financials(write_csv(tmp_path, [row]))
    assert dataset.has_warnings
    # 1000 - (400 + 500) = +100
    assert any(
        "total_assets - (total_liabilities + total_equity) = +100.00" in w
        for w in dataset.warnings
    ), dataset.warnings


def test_inconsistent_ebitda_is_a_warning(tmp_path):
    row = make_row()
    row["ebitda"] = 999.0               # EBIT + D&A is 130
    dataset = load_financials(write_csv(tmp_path, [row]))
    assert any("ebitda" in w for w in dataset.warnings)


# ---------------------------------------------------------------------------
# Gap detection
# ---------------------------------------------------------------------------
def test_missing_years_are_found_per_company(dataset):
    assert find_missing_years(dataset.frame) == [
        {"ticker": "GLOB", "fiscal_year": 2023}
    ]


def test_gaps_at_the_edges_are_not_invented():
    # We cannot know a file "should" have started earlier or ended later.
    frame = pd.DataFrame({
        "ticker": ["A", "A", "B", "B"],
        "fiscal_year": [2020, 2022, 2020, 2021],
    })
    assert find_missing_years(frame) == [{"ticker": "A", "fiscal_year": 2021}]


def test_a_single_company_with_one_year_has_no_gaps():
    frame = pd.DataFrame({"ticker": ["A"], "fiscal_year": [2020]})
    assert find_missing_years(frame) == []


def test_companies_cannot_mask_each_others_gaps():
    # A has every year, B is missing one; they must not cancel out.
    frame = pd.DataFrame({
        "ticker": ["A", "A", "A", "B", "B"],
        "fiscal_year": [2020, 2021, 2022, 2020, 2022],
    })
    assert find_missing_years(frame) == [{"ticker": "B", "fiscal_year": 2021}]
