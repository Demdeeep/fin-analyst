"""Pure financial ratio and growth functions.

Every function in this module obeys the same contract:

* **Pure** -- no I/O, no printing, no logging, no mutation of arguments, and no
  import of pandas or of any other module in this package. This keeps the module
  a leaf, which is what makes both the unit tests and the future agent "tool"
  surface simple.
* **Scalar in, scalar out** (or ``Sequence`` in, ``list`` out for
  :func:`yoy_growth`).
* **Ratios come back as decimal fractions** -- ``0.153`` means 15.3%. There is
  exactly one convention in the data layer; percent strings exist only in the
  report layer.
* **An undefined result is ``None``.** Never ``NaN``, never ``inf``, never an
  exception, never a plausible-looking wrong number.

The ``None`` policy is the important one. ``None`` is falsy and renders as
``"n/a"``; ``NaN`` compares false but prints as ``nan`` and *reads like a
measurement* once it lands in an LLM prompt in Stage 2. When a denominator is
zero or negative we would be producing a number that has no financial meaning
(return on *negative* equity is not a percentage), so we decline to.
"""

from collections.abc import Mapping, Sequence
import math
from typing import Optional

#: Day-count basis for the working-capital cycle metrics. Kept here rather than
#: in :mod:`financial_analyst.config` because this module must not import from
#: the package; ``config`` re-exports it so callers have one obvious home.
DAYS_PER_YEAR = 365.0


def _num(value: object) -> Optional[float]:
    """Coerce ``value`` to a finite float, or return ``None``.

    Treats ``None``, booleans (ints in Python, so they would silently become
    0.0/1.0), non-numeric strings, ``NaN`` and infinities as "no value". Numeric
    strings are accepted so this module can be handed raw CSV cell values.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if result != result:  # NaN
        return None
    if result in (float("inf"), float("-inf")):
        return None
    return result


def safe_div(
    numerator: object,
    denominator: object,
    *,
    default: Optional[float] = None,
    require_positive_denominator: bool = False,
) -> Optional[float]:
    """Divide, returning ``default`` whenever the quotient is undefined.

    Returns ``None`` when either operand is missing/non-finite or the
    denominator is zero. With ``require_positive_denominator=True`` a negative
    denominator is undefined too -- pass this for ratios where a negative base
    yields a meaningless figure (ROE on negative equity, interest coverage on
    negative EBIT, and so on).

    A quotient that overflows to infinity is also undefined. It is reachable
    (``4 / 2.2e-308``) even though it cannot occur with real financial figures,
    and a single ``inf`` in the metrics table silently poisons every average
    computed from it, so the guard is cheap insurance for the property this
    module promises: a finite float, or ``None``.

    >>> safe_div(1, 4)
    0.25
    >>> safe_div(1, 0) is None
    True
    >>> safe_div(4, 1e-310) is None
    True
    >>> safe_div(1, -4, require_positive_denominator=True) is None
    True
    """
    num = _num(numerator)
    den = _num(denominator)
    if num is None or den is None or den == 0.0:
        return default
    if require_positive_denominator and den < 0.0:
        return default
    try:
        result = num / den
    except (OverflowError, ZeroDivisionError):
        return default
    return result if math.isfinite(result) else default


def _ratio(numerator: object, denominator: object) -> Optional[float]:
    """A ratio whose denominator must be strictly positive to be meaningful."""
    return safe_div(numerator, denominator, require_positive_denominator=True)


def _sub(left: object, right: object) -> Optional[float]:
    """Subtract two optional quantities, propagating missingness."""
    a, b = _num(left), _num(right)
    if a is None or b is None:
        return None
    return a - b


def _add(left: object, right: object) -> Optional[float]:
    """Add two optional quantities, propagating missingness."""
    a, b = _num(left), _num(right)
    if a is None or b is None:
        return None
    return a + b


def _mul(left: object, right: object) -> Optional[float]:
    """Multiply two optional quantities, propagating missingness."""
    a, b = _num(left), _num(right)
    if a is None or b is None:
        return None
    return a * b


# ---------------------------------------------------------------------------
# Growth
# ---------------------------------------------------------------------------
def growth_rate(current: object, prior: object) -> Optional[float]:
    """Period-over-period growth as a decimal fraction.

    Undefined (``None``) when ``prior <= 0``: growth off a zero or negative base
    is not expressible as a percentage, and computing one anyway is the single
    most common way financial analysis quietly lies.

    >>> growth_rate(110, 100)
    0.1
    >>> growth_rate(90, -10) is None
    True
    """
    base = _num(prior)
    top = _num(current)
    if base is None or top is None or base <= 0.0:
        return None
    # Routed through safe_div so an overflowing quotient is also declined.
    return safe_div(top - base, base)


def cagr(start: object, end: object, years: object) -> Optional[float]:
    """Compound annual growth rate over ``years`` periods, as a decimal.

    Undefined when ``start <= 0`` (same reason as :func:`growth_rate`) or when
    ``years <= 0``. Fractional ``years`` is allowed, for a partial final period.

    >>> round(cagr(100, 133.1, 3), 10)   # 1.1 ** 3 == 1.331
    0.1
    >>> cagr(100, 200, 0) is None
    True
    """
    start_num, end_num, years_num = _num(start), _num(end), _num(years)
    if start_num is None or end_num is None or years_num is None:
        return None
    if start_num <= 0.0 or years_num <= 0.0:
        return None
    try:
        ratio = end_num / start_num
    except (OverflowError, ZeroDivisionError):
        return None
    if not math.isfinite(ratio):
        return None
    # A negative ratio means start and end have opposite signs, and compounding
    # between them is undefined. Note this cannot be caught by an except clause:
    # Python returns a *complex* number for a negative base with a fractional
    # exponent ((-1.0) ** 0.5 -> complex), so no exception is ever raised.
    if ratio < 0.0:
        return None
    try:
        return ratio ** (1.0 / years_num) - 1.0
    except (OverflowError, ValueError, ZeroDivisionError):
        return None


def yoy_growth(values: Sequence[object]) -> list[Optional[float]]:
    """Year-over-year growth for an ordered series, aligned to the input.

    Element ``i`` is the growth from ``values[i-1]`` to ``values[i]``; element
    ``0`` is always ``None`` because a first observation has nothing to compare
    against. An empty series gives an empty list. This function assumes
    consecutive periods -- the dataframe layer is responsible for refusing to
    compute a "year-over-year" change across a gap in the data.
    """
    if not values:
        return []
    growth: list[Optional[float]] = [None]
    for previous, current in zip(values, values[1:]):
        growth.append(growth_rate(current, previous))
    return growth


# ---------------------------------------------------------------------------
# Margins
# ---------------------------------------------------------------------------
# Every margin is X / revenue with revenue required to be strictly positive.
def gross_margin(revenue: object, cogs: object) -> Optional[float]:
    """(Revenue - COGS) / Revenue.

    >>> round(gross_margin(100, 60), 10)
    0.4
    """
    rev = _num(revenue)
    if rev is None or rev <= 0.0:
        return None
    return _ratio(_sub(rev, cogs), rev)


def operating_margin(revenue: object, operating_income: object) -> Optional[float]:
    """Operating income (EBIT) / Revenue."""
    return _ratio(operating_income, revenue)


def net_margin(revenue: object, net_income: object) -> Optional[float]:
    """Net income / Revenue.

    Sign-carrying: a loss-making company yields a negative margin, which is
    meaningful and must not be suppressed.

    >>> net_margin(100, -5)
    -0.05
    """
    return _ratio(net_income, revenue)


def ebitda_from_components(
    operating_income: object, depreciation_amortization: object
) -> Optional[float]:
    """EBITDA = operating income (EBIT) + depreciation & amortisation.

    Used both to fill a blank ``ebitda`` column and to check a reported one.
    """
    return _add(operating_income, depreciation_amortization)


def ebitda_margin(revenue: object, ebitda: object) -> Optional[float]:
    """EBITDA / Revenue."""
    return _ratio(ebitda, revenue)


def fcf_margin(revenue: object, free_cash_flow: object) -> Optional[float]:
    """Free cash flow / Revenue."""
    return _ratio(free_cash_flow, revenue)


def effective_tax_rate(pretax_income: object, tax_expense: object) -> Optional[float]:
    """Tax expense / Pre-tax income.

    Undefined when pre-tax income is not positive -- with a pre-tax loss there is
    no meaningful effective rate, and dividing by a small positive number yields
    an absurd percentage that looks authoritative.
    """
    return _ratio(tax_expense, pretax_income)


# ---------------------------------------------------------------------------
# Profitability and returns
# ---------------------------------------------------------------------------
def return_on_assets(net_income: object, total_assets: object) -> Optional[float]:
    """Net income / Total assets."""
    return _ratio(net_income, total_assets)


def return_on_equity(net_income: object, total_equity: object) -> Optional[float]:
    """Net income / Total equity.

    Undefined for negative equity. A negative denominator here produces a number
    that will be quoted as "return on equity" while actually describing
    accumulated losses, so the guard is deliberate rather than pedantic.

    >>> return_on_equity(20, 100)
    0.2
    >>> return_on_equity(-20, -100) is None
    True
    """
    return _ratio(net_income, total_equity)


def nopat(operating_income: object, tax_rate: object) -> Optional[float]:
    """Net operating profit after tax: EBIT x (1 - effective tax rate)."""
    ebit, rate = _num(operating_income), _num(tax_rate)
    if ebit is None or rate is None:
        return None
    return ebit * (1.0 - rate)


def nopat_from_pretax(
    operating_income: object, effective_tax_rate_value: object
) -> Optional[float]:
    """NOPAT implied by an effective tax rate derived from pre-tax income."""
    rate = _num(effective_tax_rate_value)
    ebit = _num(operating_income)
    if rate is None or ebit is None:
        return None
    return ebit * (1.0 - rate)


def invested_capital(
    total_debt_value: object, total_equity_value: object, cash: object
) -> Optional[float]:
    """Debt + equity - cash. Cash-free because cash is non-operating."""
    return _sub(_add(total_debt_value, total_equity_value), cash)


def return_on_invested_capital(
    nopat_value: object,
    total_debt_value: object,
    total_equity_value: object,
    cash: object,
) -> Optional[float]:
    """NOPAT / Invested capital."""
    return _ratio(nopat_value, invested_capital(total_debt_value, total_equity_value, cash))


def asset_turnover(revenue: object, total_assets: object) -> Optional[float]:
    """Revenue / Total assets -- how hard the asset base is worked."""
    return _ratio(revenue, total_assets)


def equity_multiplier(total_assets: object, total_equity_value: object) -> Optional[float]:
    """Total assets / Total equity -- how much the asset base is levered."""
    return _ratio(total_assets, total_equity_value)


# ---------------------------------------------------------------------------
# Leverage and solvency
# ---------------------------------------------------------------------------
# Definition note: "total debt" here means interest-bearing borrowings, i.e.
# short-term debt (which already includes the current portion of long-term debt,
# per the schema) plus long-term debt. Lease liabilities and trade payables are
# deliberately excluded -- folding them in is defensible but must be stated,
# and the schema states it.
def total_debt(short_term_debt: object, long_term_debt: object) -> Optional[float]:
    """Interest-bearing debt = short-term + long-term."""
    return _add(short_term_debt, long_term_debt)


def debt_to_assets(
    total_debt_value: object, total_assets: object
) -> Optional[float]:
    """Total debt / Total assets -- the headline leverage ratio.

    Note the argument order mirrors the others in this module: the numerator
    comes first. (The conventional statement presents it the other way round.)

    >>> debt_to_assets(400, 1000)
    0.4
    """
    return _ratio(total_debt_value, total_assets)


def debt_to_equity(
    total_debt_value: object, total_equity_value: object
) -> Optional[float]:
    """Total debt / Total equity. Undefined for negative equity."""
    return _ratio(total_debt_value, total_equity_value)


def liabilities_to_assets(
    total_liabilities: object, total_assets: object
) -> Optional[float]:
    """Total liabilities / Total assets -- broader than debt-to-assets."""
    return _ratio(total_liabilities, total_assets)


def net_debt(total_debt_value: object, cash: object) -> Optional[float]:
    """Total debt - cash and equivalents. May legitimately be negative."""
    return _sub(total_debt_value, cash)


def net_debt_to_ebitda(net_debt_value: object, ebitda: object) -> Optional[float]:
    """Net debt / EBITDA.

    Undefined when EBITDA is not positive. For a company with negative EBITDA
    this ratio is not "very high", it is meaningless, and the alarming number it
    would produce (``-3.0`` from ``net_debt 272 / EBITDA -90``) is exactly the
    kind of value that gets quoted without checking its sign.
    """
    return _ratio(net_debt_value, ebitda)


def interest_coverage(operating_income: object, interest_expense: object) -> Optional[float]:
    """EBIT / interest expense -- how many times operating profit covers debt cost.

    Undefined when interest expense is not positive (a net interest *income*
    position does not need covering).
    """
    return _ratio(operating_income, interest_expense)


# ---------------------------------------------------------------------------
# Liquidity
# ---------------------------------------------------------------------------
def current_ratio(
    current_assets: object, current_liabilities: object
) -> Optional[float]:
    """Current assets / current liabilities. Above 1.0 means positive WC."""
    return _ratio(current_assets, current_liabilities)


def quick_ratio(
    current_assets: object, inventory: object, current_liabilities: object
) -> Optional[float]:
    """(Current assets - inventory) / current liabilities -- the acid test."""
    ca, cl = _num(current_assets), _num(current_liabilities)
    if ca is None or cl is None:
        return None
    return _ratio(_sub(ca, inventory), cl)


def cash_ratio(cash: object, current_liabilities: object) -> Optional[float]:
    """Cash and equivalents / current liabilities -- the most pessimistic view."""
    return _ratio(cash, current_liabilities)


def working_capital(current_assets: object, current_liabilities: object) -> Optional[float]:
    """Current assets - current liabilities, in USD millions (not a ratio)."""
    return _sub(current_assets, current_liabilities)


# ---------------------------------------------------------------------------
# Efficiency / working-capital cycle (365-day basis)
# ---------------------------------------------------------------------------
def inventory_turnover(cogs: object, inventory: object) -> Optional[float]:
    """COGS / inventory -- how many times stock turns over."""
    return _ratio(cogs, inventory)


def days_inventory_outstanding(cogs: object, inventory: object) -> Optional[float]:
    """365 / inventory turnover: days of stock on hand."""
    return safe_div(DAYS_PER_YEAR, inventory_turnover(cogs, inventory))


def receivables_turnover(revenue: object, accounts_receivable: object) -> Optional[float]:
    """Revenue / accounts receivable."""
    return _ratio(revenue, accounts_receivable)


def days_sales_outstanding(
    revenue: object, accounts_receivable: object
) -> Optional[float]:
    """365 / receivables turnover: days of sales uncollected."""
    return safe_div(DAYS_PER_YEAR, receivables_turnover(revenue, accounts_receivable))


def payables_turnover(cogs: object, accounts_payable: object) -> Optional[float]:
    """COGS / accounts payable."""
    return _ratio(cogs, accounts_payable)


def days_payables_outstanding(cogs: object, accounts_payable: object) -> Optional[float]:
    """365 / payables turnover: days of suppliers being financed."""
    return safe_div(DAYS_PER_YEAR, payables_turnover(cogs, accounts_payable))


def cash_conversion_cycle(
    days_sales_outstanding_value: object,
    days_inventory_outstanding_value: object,
    days_payables_outstanding_value: object,
) -> Optional[float]:
    """DSO + DIO - DPO: days of cash tied up in operations.

    Undefined when any component is, so a partial cycle is never reported as a
    real one.
    """
    return _sub(
        _add(days_sales_outstanding_value, days_inventory_outstanding_value),
        days_payables_outstanding_value,
    )


# ---------------------------------------------------------------------------
# Cash flow
# ---------------------------------------------------------------------------
# Sign convention: capex arrives as a positive magnitude (see the schema), so
# free cash flow is a subtraction rather than an addition of two signed numbers.
def free_cash_flow(cash_from_operations: object, capex: object) -> Optional[float]:
    """Free cash flow = CFO - capex.

    >>> free_cash_flow(100, 30)
    70.0
    """
    return _sub(cash_from_operations, capex)


def fcf_conversion(free_cash_flow_value: object, net_income: object) -> Optional[float]:
    """FCF / net income -- accrual quality. Undefined on a net loss."""
    return _ratio(free_cash_flow_value, net_income)


def cfo_to_net_income(
    cash_from_operations: object, net_income: object
) -> Optional[float]:
    """CFO / net income -- how much reported profit became cash."""
    return _ratio(cash_from_operations, net_income)


def capex_intensity(capex: object, revenue: object) -> Optional[float]:
    """Capex / revenue -- reinvestment burden."""
    return _ratio(capex, revenue)


# ---------------------------------------------------------------------------
# Per-share and valuation
# ---------------------------------------------------------------------------
def earnings_per_share(net_income: object, diluted_shares: object) -> Optional[float]:
    """Net income / diluted shares (both in millions -> USD per share)."""
    return safe_div(net_income, diluted_shares)


def book_value_per_share(
    total_equity_value: object, shares_outstanding: object
) -> Optional[float]:
    """Total equity / shares outstanding. Negative when equity is negative."""
    return safe_div(total_equity_value, shares_outstanding)


def market_cap(share_price: object, shares_outstanding: object) -> Optional[float]:
    """Share price x shares outstanding."""
    return _mul(share_price, shares_outstanding)


def enterprise_value(
    market_cap_value: object, total_debt_value: object, cash: object
) -> Optional[float]:
    """EV = market cap + net debt."""
    return _add(market_cap_value, net_debt(total_debt_value, cash))


def price_to_earnings(share_price: object, earnings_per_share_value: object) -> Optional[float]:
    """P/E. Undefined on a loss (negative EPS is not a cheap valuation)."""
    return _ratio(share_price, earnings_per_share_value)


def price_to_book(
    share_price: object, book_value_per_share_value: object
) -> Optional[float]:
    """P/B. Undefined on negative book value."""
    return _ratio(share_price, book_value_per_share_value)


def ev_to_ebitda(enterprise_value_value: object, ebitda: object) -> Optional[float]:
    """EV / EBITDA. Undefined on negative EBITDA."""
    return _ratio(enterprise_value_value, ebitda)


# ---------------------------------------------------------------------------
# Accounting-identity checks (data quality)
# ---------------------------------------------------------------------------
# These do not produce ratios. They answer "is this row internally consistent?",
# and they return the *residual* rather than a bare boolean so that a caller can
# report how far off a figure is instead of only that it is wrong.
def balance_sheet_residual(
    total_assets: object, total_liabilities: object, total_equity: object
) -> Optional[float]:
    """``assets - (liabilities + equity)``. Zero when the balance sheet balances.

    Returns ``None`` when *any* of the three is missing. Substituting zero for an
    absent figure is tempting and wrong: a company that does not tag
    ``Liabilities`` (Beyond Meat does not -- its redeemable noncontrolling
    interests sit in mezzanine equity) would otherwise report a residual equal to
    the entire balance sheet and trip a ``critical`` identity flag on clean data.
    An absent figure means "not checked", exactly as elsewhere in this module.
    """
    assets = _num(total_assets)
    liabilities = _num(total_liabilities)
    equity = _num(total_equity)
    if assets is None or liabilities is None or equity is None:
        return None
    return assets - (liabilities + equity)


def is_balanced(
    total_assets: object,
    total_liabilities: object,
    total_equity: object,
    tolerance: float = 0.01,
) -> bool:
    """Whether ``assets == liabilities + equity`` within ``tolerance``.

    Returns ``True`` when the inputs are insufficient to judge, so that a row
    full of blanks does not masquerade as a *failed* check -- absence of data is
    reported separately by the loader.
    """
    residual = balance_sheet_residual(total_assets, total_liabilities, total_equity)
    if residual is None:
        return True
    return abs(residual) <= tolerance


def income_statement_residuals(lines: Mapping[str, object]) -> dict[str, Optional[float]]:
    """Residuals for the standard income-statement identities.

    ``lines`` is a mapping of column name to value (typically one row of the
    dataframe, or a plain dict). Each returned value is ``reported - expected``:
    ``gross_profit`` is checked against ``revenue - cogs``, ``operating_income``
    against ``gross_profit - operating_expenses``, ``pretax_income`` against
    ``operating_income + other_income``, and ``net_income`` against
    ``pretax_income - tax_expense``.

    A check whose inputs are incomplete yields ``None``, meaning "not checked",
    which is deliberately distinct from ``0.0`` ("checked and consistent").

    Note ``other_income``: it is the *net* non-operating line exactly as a
    company presents it between operating income and pre-tax income, so it
    already contains interest expense. ``pretax == operating_income -
    interest_expense`` was the earlier assumption here, and it is wrong for
    effectively every real company -- it survives only on data whose non-
    operating items other than interest are zero. Sanity-checking against
    Apple's 10-K is what exposed it: FY2022 filed pre-tax income is 119,103
    against 119,437 - 2,931 = 116,506 computed, a residual of 2,597 which is
    precisely Apple's other non-operating income. With the net line, FY2022,
    FY2023 and FY2024 all balance exactly.

    Because a missing ``other_income`` leaves the bridge genuinely unknown, the
    pre-tax check is skipped rather than assumed to be zero.
    """
    residuals: dict[str, Optional[float]] = {}

    revenue = _num(lines.get("revenue"))
    cogs = _num(lines.get("cogs"))
    gross_profit = _num(lines.get("gross_profit"))
    operating_expenses = _num(lines.get("operating_expenses"))
    operating_income = _num(lines.get("operating_income"))
    other_income = _num(lines.get("other_income"))
    pretax_income = _num(lines.get("pretax_income"))
    tax_expense = _num(lines.get("tax_expense"))
    net_income = _num(lines.get("net_income"))

    if None not in (revenue, cogs, gross_profit):
        residuals["gross_profit"] = gross_profit - (revenue - cogs)  # type: ignore[operator]
    if None not in (gross_profit, operating_expenses, operating_income):
        residuals["operating_income"] = operating_income - (  # type: ignore[operator]
            gross_profit - operating_expenses
        )
    if None not in (operating_income, other_income, pretax_income):
        residuals["pretax_income"] = pretax_income - (  # type: ignore[operator]
            operating_income + other_income
        )
    if None not in (pretax_income, tax_expense, net_income):
        residuals["net_income"] = net_income - (pretax_income - tax_expense)  # type: ignore[operator]

    return residuals


def has_missing_required(
    lines: Mapping[str, object], required: Sequence[str]
) -> list[str]:
    """Names of ``required`` columns that are absent or non-numeric in ``lines``.

    Kept beside the identity checks so that "is this row usable?" and "is this
    row consistent?" live together.
    """
    return [name for name in required if _num(lines.get(name)) is None]
