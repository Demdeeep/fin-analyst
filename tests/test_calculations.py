"""Table-driven unit tests for the pure functions in ``tools.calculations``.

Every expected value here is a number worked out by hand on round inputs, not
a value recomputed by the same formula the function uses. A test that computes
its expectation with the production code proves nothing beyond "the code agrees
with itself".

The cases are grouped as: the ordinary path, then the guards. The guards are
the interesting half -- an agent in Stage 2 will call these with whatever the
CSV contained, and the only acceptable outcomes are a correct number or
``None``, never an exception and never a plausible-looking wrong number.
"""

from __future__ import annotations

import math

import pytest

from financial_analyst.tools import calculations as calc


# ---------------------------------------------------------------------------
# Safe division and coercion
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "numerator, denominator, expected",
    [
        (1, 4, 0.25),
        (10, 4, 2.5),
        (0, 4, 0.0),
        (1, 2, 0.5),
        (1, -4, -0.25),          # a negative denominator is fine here
        (-1, 4, -0.25),
        (0, 0, None),            # undefined
        (1, 0, None),
        (None, 4, None),
        (1, None, None),
        ("abc", 4, None),
        (True, 4, None),         # a bool is not a measurement
        (float("nan"), 4, None),
        (4, float("nan"), None),
        (float("inf"), 4, None),
        (4, float("inf"), None),
    ],
)
def test_safe_div(numerator, denominator, expected):
    assert calc.safe_div(numerator, denominator) == expected


def test_safe_div_default_is_used_when_undefined():
    assert calc.safe_div(1, 0, default=0.0) == 0.0
    assert calc.safe_div(None, None, default=-1.0) == -1.0


@pytest.mark.parametrize("denominator", [0, -5, None, "x"])
def test_safe_div_positive_denominator_guard(denominator):
    assert calc.safe_div(10, denominator, require_positive_denominator=True) is None


def test_safe_div_accepts_numeric_strings():
    # The loader hands over values straight out of CSV cells.
    assert calc.safe_div("10", "4") == 2.5


# ---------------------------------------------------------------------------
# Growth
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "current, prior, expected",
    [
        (110, 100, 0.10),        # the case the docstring promises
        (100, 100, 0.0),
        (90, 100, -0.10),
        (150, 100, 0.50),
        (110, 0, None),          # growth off zero is undefined
        (110, -100, None),       # growth off a loss is undefined
        (None, 100, None),
        (110, None, None),
    ],
)
def test_growth_rate(current, prior, expected):
    result = calc.growth_rate(current, prior)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


@pytest.mark.parametrize(
    "start, end, years, expected",
    [
        (100, 133.1, 3, 0.10),   # 1.1 ** 3 == 1.331 exactly
        (100, 100, 5, 0.0),
        (100, 200, 1, 1.0),
        (100, 50, 1, -0.5),
        (100, 200, 2, math.sqrt(2) - 1),
        (100, 200, 2.5, 2 ** 0.4 - 1),
        (0, 100, 5, None),       # undefined base
        (-100, 100, 5, None),
        (100, -1, 2, None),      # start and end of opposite signs. Note this
                                 # cannot be caught by an except clause:
                                 # (-1.0) ** 0.5 returns a *complex* number
                                 # in Python, it does not raise.
        (100, 0, 2, -1.0),       # revenue fell to exactly zero: -100% a year
        (100, 200, 0, None),     # no time elapsed
        (100, 200, -1, None),
        (100, 200, None, None),
        (None, 200, 3, None),
        # A denormal period makes 1/years overflow to inf, so ratio ** inf is
        # inf rather than an exception. Found by hypothesis.
        (0.5, 1.0, 2.225073858507e-311, None),
    ],
)
def test_cagr(start, end, years, expected):
    result = calc.cagr(start, end, years)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_yoy_growth_is_aligned_to_input():
    assert calc.yoy_growth([100, 110, 121]) == pytest.approx([None, 0.10, 0.10])


def test_yoy_growth_first_element_is_always_none():
    assert calc.yoy_growth([]) == []
    assert calc.yoy_growth([500]) == [None]


def test_yoy_growth_stops_at_an_unusable_base():
    # A gap or a loss in the series must not produce a number.
    assert calc.yoy_growth([100, 110, None, 132]) == pytest.approx([None, 0.10, None, None])


# ---------------------------------------------------------------------------
# Margins
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "revenue, cogs, expected",
    [
        (100, 60, 0.40),
        (100, 100, 0.0),
        (100, 0, 1.0),
        (100, 150, -0.50),       # a negative gross margin is a real outcome
        (0, 60, None),           # no revenue, no margin
        (-100, 60, None),
        (None, 60, None),
        (100, None, None),
    ],
)
def test_gross_margin(revenue, cogs, expected):
    result = calc.gross_margin(revenue, cogs)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


@pytest.mark.parametrize(
    "function, revenue, component, expected",
    [
        (calc.operating_margin, 1000, 155, 0.155),
        (calc.net_margin, 1000, 105, 0.105),
        (calc.ebitda_margin, 1000, 205, 0.205),
        (calc.fcf_margin, 1000, 80, 0.08),
    ],
)
def test_simple_margins(function, revenue, component, expected):
    assert function(revenue, component) == pytest.approx(expected)
    assert function(revenue, -component) == pytest.approx(-expected)
    assert function(0, component) is None


def test_net_margin_of_a_loss_maker_is_negative():
    assert calc.net_margin(430, -15.4) == pytest.approx(-15.4 / 430)


@pytest.mark.parametrize(
    "pretax, tax, expected",
    [
        (100, 25, 0.25),
        (100, 0, 0.0),
        (-100, 0, None),          # no meaningful rate against a loss
        (-100, -10, None),
        (0, 0, None),
        (1, 500, 500.0),          # real, if absurd -- we do not second-guess
    ],
)
def test_effective_tax_rate(pretax, tax, expected):
    result = calc.effective_tax_rate(pretax, tax)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Returns
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "net_income, equity, expected",
    [
        (20, 100, 0.20),
        (-20, 100, -0.20),
        (20, -100, None),         # negative equity: not a percentage
        (-20, -100, None),
        (20, 0, None),
        (None, 100, None),
    ],
)
def test_return_on_equity(net_income, equity, expected):
    result = calc.return_on_equity(net_income, equity)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_return_on_assets_guards_the_denominator():
    assert calc.return_on_assets(70, 1000) == pytest.approx(0.07)
    assert calc.return_on_assets(70, -1000) is None
    assert calc.return_on_assets(70, 0) is None


def test_nopat_and_invested_capital():
    assert calc.nopat(100, 0.21) == pytest.approx(79.0)
    assert calc.nopat(100, None) is None
    assert calc.nopat_from_pretax(100, 0.25) == pytest.approx(75.0)
    assert calc.invested_capital(300, 600, 100) == pytest.approx(800.0)
    assert calc.invested_capital(None, 600, 100) is None
    assert calc.return_on_invested_capital(79, 300, 600, 100) == pytest.approx(79 / 800)


def test_asset_turnover_and_equity_multiplier():
    assert calc.asset_turnover(1000, 1000) == pytest.approx(1.0)
    assert calc.asset_turnover(1000, 0) is None
    assert calc.equity_multiplier(1000, 500) == pytest.approx(2.0)
    assert calc.equity_multiplier(1000, -500) is None


# ---------------------------------------------------------------------------
# Leverage and liquidity
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "short, long, expected",
    [(100, 400, 500), (0, 400, 400), (None, 400, None), (100, None, None)],
)
def test_total_debt(short, long, expected):
    assert calc.total_debt(short, long) == expected


@pytest.mark.parametrize(
    "debt, assets, expected",
    [(400, 1000, 0.4), (0, 1000, 0.0), (400, 0, None), (400, -1000, None)],
)
def test_debt_to_assets(debt, assets, expected):
    result = calc.debt_to_assets(debt, assets)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_debt_to_equity_is_undefined_on_negative_equity():
    assert calc.debt_to_equity(300, 600) == pytest.approx(0.5)
    assert calc.debt_to_equity(290, -20) is None


def test_liabilities_to_assets():
    assert calc.liabilities_to_assets(400, 1000) == pytest.approx(0.4)
    assert calc.liabilities_to_assets(400, 0) is None


@pytest.mark.parametrize(
    "debt, cash, expected",
    [(500, 100, 400), (100, 300, -200), (None, 100, None), (500, None, None)],
)
def test_net_debt(debt, cash, expected):
    assert calc.net_debt(debt, cash) == expected


def test_net_debt_to_ebitda_refuses_negative_ebitda():
    # -3.02x would be the "answer" and is meaningless; None is the honest one.
    assert calc.net_debt_to_ebitda(200, 100) == pytest.approx(2.0)
    assert calc.net_debt_to_ebitda(272, -90) is None
    assert calc.net_debt_to_ebitda(272, 0) is None
    assert calc.net_debt_to_ebitda(-50, 100) == pytest.approx(-0.5)  # net cash


@pytest.mark.parametrize(
    "ebit, interest, expected",
    [(100, 10, 10.0), (5, 10, 0.5), (-50, 10, -5.0), (100, 0, None), (100, -10, None)],
)
def test_interest_coverage(ebit, interest, expected):
    result = calc.interest_coverage(ebit, interest)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_liquidity_ratios():
    assert calc.current_ratio(200, 100) == pytest.approx(2.0)
    assert calc.current_ratio(200, 0) is None
    assert calc.quick_ratio(200, 50, 100) == pytest.approx(1.5)
    assert calc.quick_ratio(200, None, 100) is None
    assert calc.cash_ratio(100, 200) == pytest.approx(0.5)
    assert calc.working_capital(200, 100) == pytest.approx(100)
    assert calc.working_capital(200, None) is None


# ---------------------------------------------------------------------------
# Efficiency
# ---------------------------------------------------------------------------
def test_turnover_and_days_are_365_based():
    # COGS 600 / inventory 120 == 5 turns, so 73 days on hand.
    assert calc.inventory_turnover(600, 120) == pytest.approx(5.0)
    assert calc.days_inventory_outstanding(600, 120) == pytest.approx(73.0)
    # Revenue 1000 / AR 100 == 10 turns, so 36.5 days uncollected.
    assert calc.receivables_turnover(1000, 100) == pytest.approx(10.0)
    assert calc.days_sales_outstanding(1000, 100) == pytest.approx(36.5)
    # COGS 600 / AP 200 == 3 turns, so ~121.7 days of supplier financing.
    assert calc.payables_turnover(600, 200) == pytest.approx(3.0)
    assert calc.days_payables_outstanding(600, 200) == pytest.approx(365 / 3)


def test_efficiency_is_undefined_without_a_stock_or_a_flow():
    assert calc.inventory_turnover(600, 0) is None
    assert calc.days_inventory_outstanding(600, 0) is None
    assert calc.days_sales_outstanding(0, 100) is None


def test_cash_conversion_cycle():
    assert calc.cash_conversion_cycle(36.5, 73.0, 121.6666666) == pytest.approx(-12.1666666)
    # A partially known cycle is not a cycle.
    assert calc.cash_conversion_cycle(None, 73.0, 121.67) is None
    assert calc.cash_conversion_cycle(36.5, None, 121.67) is None


# ---------------------------------------------------------------------------
# Cash flow
# ---------------------------------------------------------------------------
def test_free_cash_flow_subtracts_capex():
    assert calc.free_cash_flow(100, 30) == pytest.approx(70.0)
    assert calc.free_cash_flow(20, 30) == pytest.approx(-10.0)  # cash burn
    assert calc.free_cash_flow(None, 30) is None
    assert calc.free_cash_flow(100, None) is None


def test_cash_conversion_ratios():
    assert calc.fcf_conversion(80, 70) == pytest.approx(8 / 7)
    assert calc.fcf_conversion(80, -70) is None    # a loss is not a base
    assert calc.cfo_to_net_income(120, 70) == pytest.approx(12 / 7)
    assert calc.cfo_to_net_income(120, 0) is None
    assert calc.capex_intensity(40, 1000) == pytest.approx(0.04)


# ---------------------------------------------------------------------------
# Per-share and valuation
# ---------------------------------------------------------------------------
def test_per_share_measures():
    assert calc.earnings_per_share(70, 100) == pytest.approx(0.7)
    assert calc.earnings_per_share(70, 0) is None
    assert calc.book_value_per_share(600, 100) == pytest.approx(6.0)
    assert calc.book_value_per_share(-20, 80) == pytest.approx(-0.25)  # kept, not hidden


def test_market_cap_and_enterprise_value():
    assert calc.market_cap(30, 100) == pytest.approx(3000.0)
    assert calc.market_cap(None, 100) is None
    # 3000 market cap + 500 debt - 100 cash
    assert calc.enterprise_value(3000, 500, 100) == pytest.approx(3400.0)
    assert calc.enterprise_value(None, 500, 100) is None


def test_valuation_multiples_refuse_nonsensical_denominators():
    assert calc.price_to_earnings(30, 0.7) == pytest.approx(30 / 0.7)
    assert calc.price_to_earnings(30, -0.7) is None    # a loss is not cheap
    assert calc.price_to_book(30, 6.0) == pytest.approx(5.0)
    assert calc.price_to_book(30, -0.25) is None
    assert calc.ev_to_ebitda(3400, 130) == pytest.approx(3400 / 130)
    assert calc.ev_to_ebitda(3400, -130) is None


# ---------------------------------------------------------------------------
# Derived quantities and identity checks
# ---------------------------------------------------------------------------
def test_ebitda_from_components():
    assert calc.ebitda_from_components(100, 30) == pytest.approx(130.0)
    assert calc.ebitda_from_components(100, None) is None


def test_balance_sheet_residual_and_is_balanced():
    assert calc.balance_sheet_residual(1000, 400, 600) == pytest.approx(0.0)
    assert calc.balance_sheet_residual(1000, 400, 500) == pytest.approx(100.0)
    assert calc.is_balanced(1000, 400, 600)
    assert not calc.is_balanced(1000, 400, 500)
    # Within tolerance still counts as balanced.
    assert calc.is_balanced(1000, 400, 599.995, tolerance=0.01)


@pytest.mark.parametrize("missing", ["total_assets", "total_liabilities", "total_equity"])
def test_balance_sheet_is_skipped_not_faked_when_a_side_is_missing(missing):
    """An absent figure is "not checked", never silently read as zero.

    Found by running the pipeline over Beyond Meat's 10-K, which does not tag
    ``Liabilities``. Treating the gap as 0 produced a residual equal to the whole
    balance sheet -- 1,265.8 -- and a ``critical`` flag on perfectly clean data.
    """
    inputs = {"total_assets": 1062.224, "total_liabilities": 1265.772,
              "total_equity": -203.548}
    inputs[missing] = None
    assert calc.balance_sheet_residual(**inputs) is None
    # And the boolean check must not claim failure either.
    assert calc.is_balanced(**inputs)


def test_income_statement_residuals_all_zero_when_consistent():
    residuals = calc.income_statement_residuals({
        "revenue": 1000, "cogs": 600, "gross_profit": 400,
        "operating_expenses": 300, "operating_income": 100,
        "other_income": -10, "pretax_income": 90,
        "tax_expense": 20, "minority_interest": 0.0, "net_income": 70,
    })
    assert residuals == {
        "gross_profit": 0.0, "operating_income": 0.0,
        "pretax_income": 0.0, "net_income": 0.0,
    }


def test_net_income_residual_excludes_the_minority_interest():
    """net_income is the *parent's* share, below the post-tax total.

    Beyond Meat exposed this: filed net loss -366.137 against a post-tax total of
    -347.189, a difference of 18.948 that is exactly the minority interest.
    """
    residuals = calc.income_statement_residuals({
        "revenue": 1000, "cogs": 600, "gross_profit": 400,
        "operating_expenses": 300, "operating_income": 100,
        "other_income": -10, "pretax_income": 90,
        "tax_expense": 20, "minority_interest": 25, "net_income": 45,
    })
    assert residuals["net_income"] == pytest.approx(0.0)


def test_income_statement_residuals_catch_a_wrong_figure():
    residuals = calc.income_statement_residuals({
        "revenue": 1000, "cogs": 600, "gross_profit": 450,   # should be 400
        "operating_expenses": 300, "operating_income": 100,
        "other_income": -10, "pretax_income": 90,
        "tax_expense": 20, "minority_interest": 0.0, "net_income": 70,
    })
    assert residuals["gross_profit"] == pytest.approx(50.0)
    assert residuals["operating_income"] == pytest.approx(-50.0)


@pytest.mark.parametrize("absent", ["other_income", "minority_interest"])
def test_a_missing_bridge_line_skips_only_its_own_check(absent):
    """A missing line means "not checked", never assumed to be zero.

    Defaulting either to zero would reinstate a real false alarm -- and both
    were found by running the pipeline over actual 10-K filings, where they
    would fail only on real data.
    """
    lines = {
        "revenue": 1000, "cogs": 600, "gross_profit": 400,
        "operating_expenses": 300, "operating_income": 100,
        "other_income": -10, "pretax_income": 90,
        "tax_expense": 20, "minority_interest": 0.0, "net_income": 70,
    }
    del lines[absent]
    residuals = calc.income_statement_residuals(lines)
    expected_missing = {"other_income": "pretax_income",
                        "minority_interest": "net_income"}[absent]
    assert expected_missing not in residuals
    # The other lines are still checked.
    assert residuals["gross_profit"] == pytest.approx(0.0)
    assert residuals["operating_income"] == pytest.approx(0.0)
    other = "net_income" if absent == "other_income" else "pretax_income"
    assert residuals[other] == pytest.approx(0.0)


def test_incomplete_lines_are_skipped_not_failed():
    # Missing inputs mean "not checked", which must not read as "checked, 0.0".
    residuals = calc.income_statement_residuals({"revenue": 1000, "cogs": 600})
    assert residuals == {}
    partial = calc.income_statement_residuals({
        "revenue": 1000, "cogs": 600, "gross_profit": 400
    })
    assert partial == {"gross_profit": 0.0}


def test_has_missing_required():
    lines = {"revenue": 1000, "cogs": None, "gross_profit": "", "net_income": 0}
    # "" is missing, 0 is not -- a zero measurement is still a measurement.
    assert calc.has_missing_required(lines, ["revenue", "cogs", "gross_profit"]) == [
        "cogs", "gross_profit"
    ]
    assert calc.has_missing_required(lines, ["revenue", "net_income"]) == []




