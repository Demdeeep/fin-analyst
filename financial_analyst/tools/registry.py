"""A self-describing catalogue of every metric the tools layer can produce.

Stage 1 uses this to check that its output columns and the catalogue stay in
step. Stage 2 will use it to give an agent a tool list: *name*, *what it
needs*, and *whether up is good* -- so the agent never has to guess whether a
lower ``debt_to_assets`` is an improvement.

``required_columns`` is derived from each function's signature rather than
written out by hand, so the catalogue cannot drift away from the code it
describes.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Callable, Optional

from . import calculations as calc

HIGHER_IS_BETTER = "higher_is_better"
LOWER_IS_BETTER = "lower_is_better"
NEUTRAL = "neutral"


@dataclass(frozen=True)
class MetricSpec:
    """One catalogued metric."""

    name: str
    function: Callable[..., Optional[float]]
    required_columns: tuple[str, ...]
    direction: str
    description: str

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "required_columns": list(self.required_columns),
            "direction": self.direction,
            "description": self.description,
        }


def _required_columns(function: Callable) -> tuple[str, ...]:
    """Parameter names of ``function``, minus the plumbing keyword arguments."""
    ignored = {"default", "require_positive_denominator"}
    return tuple(name for name in inspect.signature(function).parameters if name not in ignored)


# name -> (function, direction, description)
_CATALOGUE: dict[str, tuple[Callable, str, str]] = {
    "revenue_growth": (calc.growth_rate, HIGHER_IS_BETTER, "Period-over-period revenue change"),
    "cagr": (calc.cagr, HIGHER_IS_BETTER, "Compound annual growth rate over a span"),
    "gross_margin": (calc.gross_margin, HIGHER_IS_BETTER, "Gross profit over revenue"),
    "operating_margin": (calc.operating_margin, HIGHER_IS_BETTER, "EBIT over revenue"),
    "net_margin": (calc.net_margin, HIGHER_IS_BETTER, "Net income over revenue"),
    "ebitda_margin": (calc.ebitda_margin, HIGHER_IS_BETTER, "EBITDA over revenue"),
    "fcf_margin": (calc.fcf_margin, HIGHER_IS_BETTER, "Free cash flow over revenue"),
    "effective_tax_rate": (calc.effective_tax_rate, NEUTRAL, "Tax expense over pre-tax income"),
    "return_on_assets": (calc.return_on_assets, HIGHER_IS_BETTER, "Net income over total assets"),
    "return_on_equity": (calc.return_on_equity, HIGHER_IS_BETTER, "Net income over total equity"),
    "return_on_invested_capital": (
        calc.return_on_invested_capital, HIGHER_IS_BETTER, "NOPAT over invested capital",
    ),
    "asset_turnover": (calc.asset_turnover, HIGHER_IS_BETTER, "Revenue over total assets"),
    "equity_multiplier": (calc.equity_multiplier, LOWER_IS_BETTER, "Total assets over total equity"),
    "debt_to_assets": (calc.debt_to_assets, LOWER_IS_BETTER, "Total debt over total assets"),
    "debt_to_equity": (calc.debt_to_equity, LOWER_IS_BETTER, "Total debt over total equity"),
    "liabilities_to_assets": (
        calc.liabilities_to_assets, LOWER_IS_BETTER, "Total liabilities over total assets",
    ),
    "net_debt_to_ebitda": (calc.net_debt_to_ebitda, LOWER_IS_BETTER, "Net debt over EBITDA"),
    "interest_coverage": (calc.interest_coverage, HIGHER_IS_BETTER, "EBIT over interest expense"),
    "current_ratio": (calc.current_ratio, HIGHER_IS_BETTER, "Current assets over current liabilities"),
    "quick_ratio": (calc.quick_ratio, HIGHER_IS_BETTER, "Current assets less inventory over current liabilities"),
    "cash_ratio": (calc.cash_ratio, HIGHER_IS_BETTER, "Cash over current liabilities"),
    "working_capital": (calc.working_capital, HIGHER_IS_BETTER, "Current assets less current liabilities"),
    "inventory_turnover": (calc.inventory_turnover, HIGHER_IS_BETTER, "COGS over inventory"),
    "receivables_turnover": (
        calc.receivables_turnover, HIGHER_IS_BETTER, "Revenue over accounts receivable",
    ),
    "payables_turnover": (
        calc.payables_turnover, NEUTRAL, "COGS over accounts payable",
    ),
    "days_inventory_outstanding": (
        calc.days_inventory_outstanding, LOWER_IS_BETTER, "Days of stock on hand",
    ),
    "days_sales_outstanding": (
        calc.days_sales_outstanding, LOWER_IS_BETTER, "Days of sales uncollected",
    ),
    "days_payables_outstanding": (
        calc.days_payables_outstanding, NEUTRAL, "Days of suppliers being financed",
    ),
    "cash_conversion_cycle": (
        calc.cash_conversion_cycle, LOWER_IS_BETTER, "DSO plus DIO less DPO",
    ),
    "fcf_conversion": (calc.fcf_conversion, HIGHER_IS_BETTER, "Free cash flow over net income"),
    "cfo_to_net_income": (
        calc.cfo_to_net_income, HIGHER_IS_BETTER, "Cash from operations over net income",
    ),
    "capex_intensity": (calc.capex_intensity, NEUTRAL, "Capex over revenue"),
    "earnings_per_share": (calc.earnings_per_share, HIGHER_IS_BETTER, "Diluted earnings per share"),
    "book_value_per_share": (calc.book_value_per_share, HIGHER_IS_BETTER, "Book value per share"),
    "price_to_earnings": (calc.price_to_earnings, LOWER_IS_BETTER, "Share price over EPS"),
    "price_to_book": (calc.price_to_book, LOWER_IS_BETTER, "Share price over book value per share"),
    "ev_to_ebitda": (calc.ev_to_ebitda, LOWER_IS_BETTER, "Enterprise value over EBITDA"),
}

METRICS: dict[str, MetricSpec] = {
    name: MetricSpec(
        name=name,
        function=function,
        required_columns=_required_columns(function),
        direction=direction,
        description=description,
    )
    for name, (function, direction, description) in _CATALOGUE.items()
}


def names() -> tuple[str, ...]:
    """Every catalogued metric name, sorted."""
    return tuple(sorted(METRICS))


def get(name: str) -> MetricSpec:
    """Look up one metric, with an error that lists the valid alternatives."""
    try:
        return METRICS[name]
    except KeyError:
        raise KeyError(
            f"unknown metric {name!r}; known metrics: {', '.join(names())}"
        ) from None


def describe() -> list[dict]:
    """The whole catalogue as plain dicts (for a prompt or a docs table)."""
    return [METRICS[name].to_dict() for name in names()]
