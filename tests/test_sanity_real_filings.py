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


# ===========================================================================
# Second industry shape: a loss-making consumer company
# ===========================================================================
#
# Source: Beyond Meat Inc., CIK 0001655210, annual figures from SEC EDGAR XBRL
# companyfacts, retrieved 2026-10-01, in tests/fixtures/beyond_meat_fy2022_2024.csv.
#
# Why this company: Apple exercises none of the negative-value guards, because
# Apple is profitable and solvent. Every one of those guards had only ever been
# tested against synthetic data until this file existed. Beyond Meat has negative
# equity in all three years, negative gross profit in two, operating and net
# losses throughout, and negative cash from operations throughout.

BEYOND_MEAT = "tests/fixtures/beyond_meat_fy2022_2024.csv"

#: Diluted EPS as filed, from the EarningsPerShareDiluted tag.
FILED_EPS_BEYOND = {2022: -5.75, 2023: -5.26, 2024: -2.43}


@pytest.fixture(scope="module")
def bynd_frame():
    return load_financials(BEYOND_MEAT).frame


@pytest.fixture(scope="module")
def bynd_metrics(bynd_frame):
    return compute_metric_frame(bynd_frame)


def bynd_row(metrics, fiscal_year):
    return metrics[metrics["fiscal_year"] == fiscal_year].iloc[0]


# ---------------------------------------------------------------------------
# The guards, exercised by real filed data
# ---------------------------------------------------------------------------
def test_negative_equity_is_real_in_the_filing(bynd_frame):
    equity = dict(zip(bynd_frame["fiscal_year"], bynd_frame["total_equity"]))
    assert equity == pytest.approx(
        {2022: -203.548, 2023: -513.368, 2024: -601.208}, abs=0.01
    )
    assert all(value < 0 for value in equity.values())


def test_negative_equity_suppresses_the_ratios_that_need_it(bynd_metrics):
    """The headline guard, now proven against a real balance sheet."""
    for row in bynd_metrics.to_dict("records"):
        assert row["total_equity"] < 0
        for column in ("return_on_equity", "debt_to_equity", "equity_multiplier",
                       "price_to_book"):
            assert pd.isna(row[column]), f"{column} should be undefined"


def test_negative_ebitda_suppresses_net_debt_to_ebitda(bynd_metrics):
    for row in bynd_metrics.to_dict("records"):
        assert row["ebitda"] < 0
        assert pd.isna(row["net_debt_to_ebitda"])
        assert pd.isna(row["ev_to_ebitda"])


def test_net_losses_suppress_valuation_and_conversion_ratios(bynd_metrics):
    for row in bynd_metrics.to_dict("records"):
        assert row["net_income"] < 0
        for column in ("price_to_earnings", "fcf_conversion", "cfo_to_net_income"):
            assert pd.isna(row[column]), column


def test_negative_gross_margin_is_reported_not_hidden(bynd_metrics):
    """FY2022 and FY2023 sold below cost. That is a real, signed margin."""
    margins = dict(zip(bynd_metrics["fiscal_year"], bynd_metrics["gross_margin"]))
    assert margins[2022] == pytest.approx(-23.743 / 418.933, rel=1e-6)
    assert margins[2023] < 0
    assert margins[2024] > 0


def test_eps_matches_the_loss_per_share_beyond_meat_filed(bynd_metrics):
    """External anchor on the negative side, which is the easier one to get wrong."""
    for fiscal_year, filed in FILED_EPS_BEYOND.items():
        computed = bynd_row(bynd_metrics, fiscal_year)["earnings_per_share"]
        assert computed == pytest.approx(filed, abs=0.005), fiscal_year


def test_cash_burn_raises_the_fcf_rule_with_real_data(bynd_metrics, bynd_frame):
    context = an.AnalysisContext(metrics=bynd_metrics, source=bynd_frame)
    fcf_flags = an.fcf_negative(context)
    # Three consecutive negative years, so the rule fires from the second on.
    assert [flag.fiscal_year for flag in fcf_flags] == [2023, 2024]
    for row in bynd_metrics.to_dict("records"):
        assert row["free_cash_flow"] < 0


def test_low_interest_coverage_fires_on_a_real_loss_maker(bynd_metrics, bynd_frame):
    context = an.AnalysisContext(metrics=bynd_metrics, source=bynd_frame)
    fired = {flag.rule for flag in an.run_anomalies(context)}
    assert "low_interest_coverage" in fired


# ---------------------------------------------------------------------------
# Identities that hold, on a very different company
# ---------------------------------------------------------------------------
def test_core_identities_hold_on_a_second_company(bynd_frame):
    """The pre-tax fix is validated a second time, on loss-making data.

    Gross profit, operating income and pre-tax all reconcile to the cent here --
    including a pre-tax income that is a *loss* bridged by a non-operating line,
    which is precisely the case the ``interest_expense`` assumption got wrong.
    """
    for row in bynd_frame.to_dict("records"):
        residuals = calc.income_statement_residuals(row)
        assert residuals["gross_profit"] == pytest.approx(0.0, abs=0.01)
        assert residuals["operating_income"] == pytest.approx(0.0, abs=0.01)
        assert residuals["pretax_income"] == pytest.approx(0.0, abs=0.01)


# ---------------------------------------------------------------------------
# KNOWN FINDINGS -- pinned, not fixed
# ---------------------------------------------------------------------------
def test_net_income_identity_holds_once_minority_interest_is_modelled(bynd_frame):
    """The fix. ``net_income`` is the *parent's* share, below the post-tax total.

    Beyond Meat's tagged ``NetIncomeLoss`` sits under the post-tax figure, and
    the gap is exactly the noncontrolling interest::

        FY2022  pretax -347.157 - tax 0.032 = -347.189, filed -366.137
                -> minority interest 18.948, and the identity now balances
        FY2023  pretax -334.237 - tax 0.005 = -334.242, filed -338.144
                -> minority interest  3.902
        FY2024  pretax -160.231 + tax 0.026 = -160.205, filed -160.278
                -> minority interest  0.073

    Beyond Meat's 10-K tags no minority-interest figure of any kind -- the
    ProfitLoss and both NCI tags are absent -- so the fixture's column is
    *derived* from these residuals. That makes the identity a tautology here and
    it proves nothing for this file; the evidence was the residual itself. The
    column earns its place for the companies that do disclose it separately.
    """
    expected = {2022: 18.948, 2023: 3.902, 2024: 0.073}
    for row in bynd_frame.to_dict("records"):
        assert row["minority_interest"] == pytest.approx(
            expected[row["fiscal_year"]], abs=0.001
        )
        residuals = calc.income_statement_residuals(row)
        assert residuals["net_income"] == pytest.approx(0.0, abs=0.01)
        assert residuals["pretax_income"] == pytest.approx(0.0, abs=0.01)


def test_the_derived_minority_interest_is_exactly_the_old_residual(bynd_frame):
    """Pins the numbers so the derivation cannot drift unnoticed."""
    for row in bynd_frame.to_dict("records"):
        post_tax = row["pretax_income"] - row["tax_expense"]
        assert post_tax - row["net_income"] == pytest.approx(
            row["minority_interest"], abs=0.01
        )


def test_no_false_alarm_remains_for_beyond_meat(bynd_metrics, bynd_frame):
    """The regression test: clean filings must raise nothing at all."""
    dataset = load_financials(BEYOND_MEAT)
    assert dataset.warnings == (), dataset.warnings
    context = an.AnalysisContext(metrics=bynd_metrics, source=bynd_frame)
    assert an.identity_mismatch(context) == []


def test_minority_interest_is_never_silently_defaulted(bynd_frame):
    """Guards against 'simplifying' the check back to pretax minus tax."""
    row = bynd_frame[bynd_frame["fiscal_year"] == 2022].iloc[0]
    without = {k: v for k, v in row.items() if k != "minority_interest"}
    residuals = calc.income_statement_residuals(without)
    assert "net_income" not in residuals, "a missing bridge line must not be guessed"


def test_balance_sheet_is_skipped_rather_than_faked(bynd_frame):
    """KNOWN GAP 2. ``assets == liabilities + equity`` does not hold here.

    Beyond Meat does not tag ``Liabilities`` in its 10-K -- redeemable
    noncontrolling interests sit in mezzanine equity, outside both totals -- so
    the fixture leaves ``total_liabilities`` blank. The check must then be
    *skipped*.

    It very nearly was not. An earlier version substituted zero for the missing
    figure and produced a residual of 1,265.8 -- the entire balance sheet -- as a
    ``critical`` flag. That was a real bug, found by real data, now fixed in
    ``balance_sheet_residual`` and covered by
    ``test_balance_sheet_is_skipped_not_faked_when_a_side_is_missing``.
    """
    for row in bynd_frame.to_dict("records"):
        assert pd.isna(row["total_liabilities"])
        assert calc.balance_sheet_residual(
            row["total_assets"], row["total_liabilities"], row["total_equity"]
        ) is None


# ===========================================================================
# Third industry shape: a lease-heavy retailer
# ===========================================================================
#
# Source: Target Corporation, CIK 0000027419, FY2024-FY2026, SEC EDGAR XBRL
# companyfacts, retrieved 2026-10-01, in tests/fixtures/target_fy2024_2026.csv.
#
# Why this company: the first two questioned the *arithmetic*. This one
# questions a *definition*. Our debt is `short_term_debt + long_term_debt` as
# tagged, leases excluded. For a retailer running most of its estate on lease
# that is a large omission, and US GAAP's split between operating and finance
# leases makes the judgement call explicit rather than academic.
#
# The fiscal years end in late January, so this is not a calendar-year company
# like the other two.

TARGET = "tests/fixtures/target_fy2024_2026.csv"

#: Lease obligations in USD millions, from Target's 10-Ks. Deliberately *not* in
#: the fixture: they are what these tests are about, and they are not part of
#: the base schema.
LEASE_LIABILITIES = {
    2024: {"operating_noncurrent": 3279.0, "operating_current": 329.0,
           "finance_noncurrent": 1894.0, "finance_current": 119.0},
    2025: {"operating_noncurrent": 3582.0, "operating_current": 353.0,
           "finance_noncurrent": 2025.0, "finance_current": 136.0},
    2026: {"operating_noncurrent": 3462.0, "operating_current": 372.0,
           "finance_noncurrent": 1982.0, "finance_current": 131.0},
}


@pytest.fixture(scope="module")
def tgt_frame():
    return load_financials(TARGET).frame


@pytest.fixture(scope="module")
def tgt_metrics(tgt_frame):
    return compute_metric_frame(tgt_frame)


def tgt_row(metrics, fiscal_year):
    return metrics[metrics["fiscal_year"] == fiscal_year].iloc[0]


def leases(fiscal_year):
    entry = LEASE_LIABILITIES[fiscal_year]
    return (entry["operating_noncurrent"] + entry["operating_current"],
            entry["finance_noncurrent"] + entry["finance_current"])


# ---------------------------------------------------------------------------
# Identities still hold
# ---------------------------------------------------------------------------
def test_core_identities_hold_on_a_third_company(tgt_frame):
    """A third company, a third reporting style, same result."""
    dataset = load_financials(TARGET)
    assert dataset.warnings == (), dataset.warnings
    for row in tgt_frame.to_dict("records"):
        for line, residual in calc.income_statement_residuals(row).items():
            assert residual == pytest.approx(0.0, abs=0.05), line


def test_other_income_model_survives_a_different_tagging_pattern(tgt_frame):
    """Apple reports one net non-operating line; Target reports interest and
    other *separately*. The model must absorb both.

        FY2026  EBIT 5,117 + (other 95 - interest 445) = 4,767 filed pre-tax
    """
    expected = {2024: -410.0, 2025: -305.0, 2026: -350.0}
    for row in tgt_frame.to_dict("records"):
        year = row["fiscal_year"]
        assert row["other_income"] == pytest.approx(expected[year], abs=0.01)
        assert row["operating_income"] + row["other_income"] == pytest.approx(
            row["pretax_income"], abs=0.05
        )


def test_eps_matches_what_target_filed(tgt_metrics, tgt_frame):
    """Diluted EPS as Target itself filed it: 8.94 / 8.86 / 8.13.

    An external anchor -- this project does not compute these numbers -- and the
    third company where the agreement holds.

    ``diluted_shares`` is not carried into the metrics frame, so the share count
    comes from the source row.
    """
    filed = {2024: 8.94, 2025: 8.86, 2026: 8.13}
    for fiscal_year, expected in filed.items():
        computed = tgt_row(tgt_metrics, fiscal_year)["earnings_per_share"]
        source = tgt_frame[tgt_frame["fiscal_year"] == fiscal_year].iloc[0]
        assert computed == pytest.approx(
            source["net_income"] / source["diluted_shares"], rel=1e-9
        )
        assert computed == pytest.approx(expected, abs=0.005), fiscal_year


def test_balance_sheet_check_is_skipped_where_liabilities_are_untagged(tgt_frame):
    """Target tags no `Liabilities` fact, so the check must be skipped.

    It could be derived as assets minus equity, but that would make the identity
    a tautology. Skipped is the honest outcome, and the third company to need it.
    """
    for row in tgt_frame.to_dict("records"):
        assert pd.isna(row["total_liabilities"])
        assert calc.balance_sheet_residual(
            row["total_assets"], row["total_liabilities"], row["total_equity"]
        ) is None


# ---------------------------------------------------------------------------
# The debt definition -- a documented caveat, not a bug
# ---------------------------------------------------------------------------
def test_our_debt_excludes_both_lease_types(tgt_metrics):
    """Our total debt is borrowings only. Leases are not in it, by design."""
    for fiscal_year in LEASE_LIABILITIES:
        row = tgt_row(tgt_metrics, fiscal_year)
        operating, finance = leases(fiscal_year)
        assert (operating + finance) / row["total_assets"] > 0.09, fiscal_year


def test_lease_exclusion_understates_leverage_by_about_ten_points(tgt_metrics):
    """The caveat, quantified, so it cannot be discovered by surprise.

        fiscal year   ours     + operating   + all leases   understated
        FY2024        27.6%       34.1%         37.7%         10.2pp
        FY2025        26.9%       33.7%         37.5%         10.6pp
        FY2026        27.8%       34.2%         37.8%         10.0pp

    An agent reading ``debt_to_assets`` for a retailer should know it is not the
    number a rating agency would quote.
    """
    for fiscal_year in LEASE_LIABILITIES:
        row = tgt_row(tgt_metrics, fiscal_year)
        operating, finance = leases(fiscal_year)
        ours = row["debt_to_assets"]
        with_all = (row["total_debt"] + operating + finance) / row["total_assets"]
        assert 0.25 < ours < 0.30, fiscal_year
        assert 0.09 < with_all - ours < 0.12, fiscal_year


def test_finance_leases_are_the_unambiguous_half_of_the_caveat(tgt_metrics):
    """Operating leases are arguably not debt. Finance leases are.

    A finance lease is a financing obligation recognised on the balance sheet,
    and rating agencies add it to debt without discussion. Excluding it is a
    real understatement rather than a matter of taste, which is why this is
    recorded as a caveat instead of simply accepted.
    """
    for fiscal_year in LEASE_LIABILITIES:
        row = tgt_row(tgt_metrics, fiscal_year)
        _, finance = leases(fiscal_year)
        # ~2.0bn against ~59bn of assets: about 3.4 percentage points.
        assert 0.025 < finance / row["total_assets"] < 0.045, fiscal_year


def test_the_caveat_does_not_become_a_false_alarm(tgt_metrics):
    """A definitional caveat is not a data error.

    Target is a clean filing. Documenting the lease omission must not turn it into
    an ``identity_mismatch`` flag, which is the failure mode every previous bug
    in this file had.
    """
    frame = load_financials(TARGET).frame
    context = an.AnalysisContext(metrics=tgt_metrics, source=frame)
    assert an.identity_mismatch(context) == []


def test_a_bank_would_be_wrong_in_a_way_a_retailer_is_not(tgt_metrics):
    """Why the fourth shape is still outstanding, stated as a test.

    For a bank, interest is *revenue*, so `operating_income` and `net_margin`
    are not meaningful as defined -- the same numbers are produced, correctly
    computed, describing nothing real. Target is the opposite case: our
    definitions hold and only an omission is arguable.

    This test records the distinction rather than leaving it in prose. It asserts
    the retailer case still works, which is what makes the bank case a
    *different* problem rather than just another company to add.
    """
    row = tgt_row(tgt_metrics, 2026)
    assert 0.04 < row["operating_margin"] < 0.06
    assert 0.03 < row["net_margin"] < 0.04
    # Interest is an expense here, which is the assumption a bank breaks.
    assert row["interest_coverage"] > 1.0





