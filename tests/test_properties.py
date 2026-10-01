"""Property-based tests.

The example-based tests elsewhere cover the cases I thought of. Hypothesis
covers the ones I did not: it feeds the guards arbitrary floats, which is the
only reliable way to find the input where a "safe" division divides by zero or
a growth rate returns ``inf``.

The property under test is narrow and absolute: **a metric function either
returns a finite float or returns ``None``.** Never an exception, never NaN,
never an infinity. That holds no matter what the CSV contained, which is exactly
the guarantee a Stage 2 agent needs before it starts calling these on live data.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from financial_analyst.tools import calculations as calc
from financial_analyst.tools import registry

#: Realistic magnitudes: USD millions. Bounded so that powers and products stay
#: in float range and failures are about the logic, not overflow.
FINITE = st.floats(
    allow_nan=False, allow_infinity=False, min_value=-1e9, max_value=1e9
)
POSITIVE = st.floats(min_value=0.01, max_value=1e9)
QUICK = settings(max_examples=100, deadline=None)


def assert_number_or_none(result, label: str = "value") -> None:
    if result is None:
        return
    assert isinstance(result, float), f"{label} returned {type(result).__name__}"
    assert math.isfinite(result), f"{label} returned {result}"


# ---------------------------------------------------------------------------
# The central property
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", registry.names())
@given(data=st.data())
@settings(max_examples=50, deadline=None)
def test_every_metric_returns_a_finite_float_or_none(name, data):
    spec = registry.get(name)
    kwargs = {
        column: data.draw(FINITE, label=column) for column in spec.required_columns
    }
    assert_number_or_none(spec.function(**kwargs), name)


# ---------------------------------------------------------------------------
# Division never explodes
# ---------------------------------------------------------------------------
@given(numerator=FINITE, denominator=FINITE)
@QUICK
def test_safe_div_never_raises_and_never_returns_nan(numerator, denominator):
    assert_number_or_none(calc.safe_div(numerator, denominator), "safe_div")


@given(denominator=st.sampled_from([0, -0.0, 1e-320]))
def test_safe_div_guards_degenerate_denominators(denominator):
    assert calc.safe_div(1.0, denominator) is None


# ---------------------------------------------------------------------------
# Growth is self-consistent
# ---------------------------------------------------------------------------
@given(current=FINITE, prior=POSITIVE)
@QUICK
def test_growth_rate_inverts_to_the_endpoint_ratio(current, prior):
    result = calc.growth_rate(current, prior)
    assert_number_or_none(result, "growth_rate")
    if result is not None:
        assert prior * (1 + result) == pytest.approx(current, rel=1e-9, abs=1e-6)


@given(start=POSITIVE, years=st.integers(1, 25), factor=st.floats(0.25, 4.0))
@QUICK
def test_cagr_inverts_to_the_endpoint_ratio(start, years, factor):
    result = calc.cagr(start, start * factor, years)
    assert_number_or_none(result, "cagr")
    if result is not None:
        assert (1 + result) ** years == pytest.approx(factor, rel=1e-9, abs=1e-9)


@given(values=st.lists(FINITE, min_size=0, max_size=25))
@QUICK
def test_yoy_growth_always_preserves_length(values):
    growth = calc.yoy_growth(values)
    assert len(growth) == len(values)
    for value in growth:
        assert_number_or_none(value, "yoy_growth")


# ---------------------------------------------------------------------------
# Identity checks never raise, whatever they are handed
# ---------------------------------------------------------------------------
@given(
    assets=FINITE, liabilities=FINITE, equity=FINITE, tolerance=st.floats(0.0, 1e6)
)
@QUICK
def test_balance_sheet_helpers_never_raise(assets, liabilities, equity, tolerance):
    assert_number_or_none(
        calc.balance_sheet_residual(assets, liabilities, equity), "residual"
    )
    assert isinstance(calc.is_balanced(assets, liabilities, equity, tolerance), bool)


@given(lines=st.dictionaries(
    st.sampled_from([
        "revenue", "cogs", "gross_profit", "operating_expenses", "operating_income",
        "interest_expense", "pretax_income", "tax_expense", "net_income",
    ]),
    FINITE,
    max_size=9,
))
@QUICK
def test_income_statement_residuals_only_report_real_checks(lines):
    residuals = calc.income_statement_residuals(lines)
    assert set(residuals) <= {
        "gross_profit", "operating_income", "pretax_income", "net_income"
    }
    for line, residual in residuals.items():
        assert_number_or_none(residual, f"residual[{line}]")
