"""Rule-based anomaly detection over the Stage 1 metrics.

Design rules, all of which exist to keep the output honest:

* **Every rule is registered and every registered rule is tested.** The sample
  data was built so that all of them fire on it, which the suite asserts.
* **Thresholds live in :mod:`financial_analyst.config`**, never as literals
  here, so a Stage 2 agent can be told exactly which cut-offs produced a flag.
* **A rule that finds nothing emits nothing.** The report never says "no issues"
  for a rule that did not run.
* **Messages state values, they do not editorialize.** This module says
  "net margin fell 6.1pp in FY2022"; judging that is Stage 2's job, and letting
  Stage 1 pre-judge it would bias every agent that later reads this output.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import pandas as pd

from .. import config
from . import calculations as calc
from .loaders import find_missing_years


@dataclass(frozen=True)
class Flag:
    """One thing worth a human's attention, with enough context to act on it."""

    ticker: str
    rule: str
    severity: str
    message: str
    fiscal_year: Optional[int] = None
    value: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "fiscal_year": self.fiscal_year,
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
            "value": self.value,
        }


@dataclass(frozen=True)
class AnalysisContext:
    """Everything a rule may look at."""

    metrics: pd.DataFrame
    source: pd.DataFrame
    raw: Optional[pd.DataFrame] = None
    imputed_columns: tuple[str, ...] = ()
    loader_warnings: tuple[str, ...] = ()

    def ordered(self) -> pd.DataFrame:
        """Metrics sorted by company then year, with prior-year columns added."""
        frame = self.metrics.sort_values(["ticker", "fiscal_year"]).reset_index(drop=True)
        for column in ("revenue", "net_margin", "gross_margin", "ebitda_margin",
                       "free_cash_flow"):
            if column in frame.columns:
                frame[f"prior_{column}"] = frame.groupby("ticker")[column].shift(1)
        return frame

    def rows(self) -> list[dict]:
        return self.metrics.to_dict("records")


def _number(value: object) -> Optional[float]:
    """``float`` or ``None`` for anything missing -- so rules can skip cleanly."""
    if value is None or value is pd.NaT:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if result != result else result


def _pp(current: Optional[float], prior: Optional[float]) -> Optional[float]:
    """Difference between two decimal ratios, expressed in percentage points."""
    if current is None or prior is None:
        return None
    return (current - prior) * 100.0


def _ordinal(number: int) -> str:
    """``2`` -> ``"2nd"``. Messages read like prose, so "the 2th year" must not."""
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


# ---------------------------------------------------------------------------
# Family A -- data quality
# ---------------------------------------------------------------------------
def missing_fiscal_year(ctx: AnalysisContext) -> list[Flag]:
    flags = []
    for gap in find_missing_years(ctx.source):
        flags.append(
            Flag(
                ticker=str(gap["ticker"]),
                fiscal_year=int(gap["fiscal_year"]),
                rule="missing_fiscal_year",
                severity="warning",
                message=(
                    f"FY{gap['fiscal_year']} has no row between this company's "
                    "first and last reported year"
                ),
            )
        )
    return flags


def identity_mismatch(ctx: AnalysisContext) -> list[Flag]:
    flags = []
    for row in ctx.source.to_dict("records"):
        residual = calc.balance_sheet_residual(
            row.get("total_assets"), row.get("total_liabilities"), row.get("total_equity")
        )
        if residual is not None and abs(residual) > config.BALANCE_TOLERANCE:
            flags.append(
                Flag(
                    ticker=str(row.get("ticker")),
                    fiscal_year=int(row["fiscal_year"]),
                    rule="identity_mismatch",
                    severity="critical",
                    message=(
                        f"assets - (liabilities + equity) = {residual:+.2f}; the "
                        "balance sheet does not balance"
                    ),
                    value=residual,
                )
            )
        for line, delta in calc.income_statement_residuals(row).items():
            if delta is None or abs(delta) <= config.INCOME_STATEMENT_TOLERANCE:
                continue
            flags.append(
                Flag(
                    ticker=str(row.get("ticker")),
                    fiscal_year=int(row["fiscal_year"]),
                    rule="identity_mismatch",
                    severity="critical",
                    message=f"reported {line} differs from recomputed by {delta:+.2f}",
                    value=delta,
                )
            )
    return flags


def negative_equity(ctx: AnalysisContext) -> list[Flag]:
    flags = []
    for row in ctx.rows():
        equity = _number(row.get("total_equity"))
        if equity is None or equity >= 0:
            continue
        flags.append(
            Flag(
                ticker=str(row["ticker"]),
                fiscal_year=int(row["fiscal_year"]),
                rule="negative_equity",
                severity="critical",
                message=(
                    f"shareholders' equity is {equity:,.1f}; ROE and debt-to-equity "
                    "are undefined this year"
                ),
                value=equity,
            )
        )
    return flags


def imputed_column(ctx: AnalysisContext) -> list[Flag]:
    flags = []
    if ctx.raw is None:
        return flags
    for column in ctx.imputed_columns:
        if column not in ctx.raw.columns:
            continue
        for ticker in dict.fromkeys(ctx.raw.loc[ctx.raw[column].isna(), "ticker"]):
            flags.append(
                Flag(
                    ticker=str(ticker),
                    rule="imputed_column",
                    severity="info",
                    message=f"{column} was blank in the source and has been derived",
                )
            )
    return flags


def loader_warning(ctx: AnalysisContext) -> list[Flag]:
    flags = []
    for message in ctx.loader_warnings:
        ticker = str(message).split(":", 1)[0] if ":" in str(message) else "?"
        flags.append(
            Flag(
                ticker=ticker,
                rule="loader_warning",
                severity="warning",
                message=str(message),
            )
        )
    return flags


# ---------------------------------------------------------------------------
# Family B -- trend deterioration
# ---------------------------------------------------------------------------
def revenue_decline(ctx: AnalysisContext) -> list[Flag]:
    flags = []
    for row in ctx.ordered().to_dict("records"):
        growth = _number(row.get("revenue_growth"))
        if growth is None or growth >= 0:
            continue
        flags.append(
            Flag(
                ticker=str(row["ticker"]),
                fiscal_year=int(row["fiscal_year"]),
                rule="revenue_decline",
                severity="warning",
                message=f"revenue fell {abs(growth) * 100:.1f}% year over year",
                value=growth,
            )
        )
    return flags


def margin_compression(ctx: AnalysisContext) -> list[Flag]:
    flags = []
    threshold = config.MARGIN_COMPRESSION_THRESHOLD_PP
    for row in ctx.ordered().to_dict("records"):
        for column, label in (("net_margin", "net margin"), ("gross_margin", "gross margin")):
            change = _pp(_number(row.get(column)), _number(row.get(f"prior_{column}")))
            if change is None or change > -threshold:
                continue
            flags.append(
                Flag(
                    ticker=str(row["ticker"]),
                    fiscal_year=int(row["fiscal_year"]),
                    rule="margin_compression",
                    severity="warning",
                    message=f"{label} fell {abs(change):.1f}pp year over year",
                    value=change,
                )
            )
    return flags


def negative_cagr(ctx: AnalysisContext) -> list[Flag]:
    flags = []
    for row in ctx.rows():
        cagr = _number(row.get("revenue_cagr"))
        if cagr is None or cagr >= 0:
            continue
        flags.append(
            Flag(
                ticker=str(row["ticker"]),
                fiscal_year=int(row["fiscal_year"]),
                rule="negative_cagr",
                severity="warning",
                message=(
                    f"revenue compounded at {cagr * 100:.1f}% a year from "
                    f"FY{int(row['first_year'])} to FY{int(row['last_year'])}"
                ),
                value=cagr,
            )
        )
    return flags


def fcf_negative(ctx: AnalysisContext) -> list[Flag]:
    """Flags every year from the second consecutive negative-FCF year onward."""
    flags = []
    streak: dict[str, int] = {}
    for row in ctx.ordered().to_dict("records"):
        ticker = str(row["ticker"])
        fcf = _number(row.get("free_cash_flow"))
        if fcf is None or fcf >= 0:
            streak[ticker] = 0
            continue
        streak[ticker] = streak.get(ticker, 0) + 1
        if streak[ticker] >= 2:
            flags.append(
                Flag(
                    ticker=ticker,
                    fiscal_year=int(row["fiscal_year"]),
                    rule="fcf_negative",
                    severity="warning",
                    message=(
                        f"free cash flow {fcf:,.1f}, the {_ordinal(streak[ticker])} "
                        "consecutive negative year"
                    ),
                    value=fcf,
                )
            )
    return flags


def ebitda_margin_collapse(ctx: AnalysisContext) -> list[Flag]:
    flags = []
    threshold = config.MARGIN_COMPRESSION_THRESHOLD_PP
    for row in ctx.ordered().to_dict("records"):
        change = _pp(
            _number(row.get("ebitda_margin")), _number(row.get("prior_ebitda_margin"))
        )
        if change is None or change > -threshold:
            continue
        flags.append(
            Flag(
                ticker=str(row["ticker"]),
                fiscal_year=int(row["fiscal_year"]),
                rule="ebitda_margin_collapse",
                severity="warning",
                message=f"EBITDA margin fell {abs(change):.1f}pp year over year",
                value=change,
            )
        )
    return flags


# ---------------------------------------------------------------------------
# Family C -- leverage and distress
# ---------------------------------------------------------------------------
def high_debt_to_equity(ctx: AnalysisContext) -> list[Flag]:
    flags = []
    for row in ctx.rows():
        ratio = _number(row.get("debt_to_equity"))
        if ratio is None or ratio <= config.DEBT_TO_EQUITY_THRESHOLD:
            continue
        flags.append(
            Flag(
                ticker=str(row["ticker"]),
                fiscal_year=int(row["fiscal_year"]),
                rule="high_debt_to_equity",
                severity="warning",
                message=(
                    f"debt/equity {ratio:.2f}x, above the "
                    f"{config.DEBT_TO_EQUITY_THRESHOLD:.1f}x threshold"
                ),
                value=ratio,
            )
        )
    return flags


def low_interest_coverage(ctx: AnalysisContext) -> list[Flag]:
    flags = []
    for row in ctx.rows():
        coverage = _number(row.get("interest_coverage"))
        if coverage is None or coverage >= config.INTEREST_COVERAGE_THRESHOLD:
            continue
        flags.append(
            Flag(
                ticker=str(row["ticker"]),
                fiscal_year=int(row["fiscal_year"]),
                rule="low_interest_coverage",
                severity="critical",
                message=(
                    f"operating income covers interest {coverage:.2f}x, below the "
                    f"{config.INTEREST_COVERAGE_THRESHOLD:.1f}x threshold"
                ),
                value=coverage,
            )
        )
    return flags


def negative_working_capital(ctx: AnalysisContext) -> list[Flag]:
    flags = []
    for row in ctx.rows():
        working_capital = _number(row.get("working_capital"))
        if working_capital is None or working_capital >= 0:
            continue
        flags.append(
            Flag(
                ticker=str(row["ticker"]),
                fiscal_year=int(row["fiscal_year"]),
                rule="negative_working_capital",
                severity="warning",
                message=f"working capital {working_capital:,.1f}",
                value=working_capital,
            )
        )
    return flags


def high_net_debt_to_ebitda(ctx: AnalysisContext) -> list[Flag]:
    flags = []
    for row in ctx.rows():
        ratio = _number(row.get("net_debt_to_ebitda"))
        if ratio is None or ratio <= config.NET_DEBT_TO_EBITDA_THRESHOLD:
            continue
        flags.append(
            Flag(
                ticker=str(row["ticker"]),
                fiscal_year=int(row["fiscal_year"]),
                rule="high_net_debt_to_ebitda",
                severity="warning",
                message=(
                    f"net debt/EBITDA {ratio:.2f}x, above the "
                    f"{config.NET_DEBT_TO_EBITDA_THRESHOLD:.1f}x threshold"
                ),
                value=ratio,
            )
        )
    return flags


# ---------------------------------------------------------------------------
# Rule registry and runner
# ---------------------------------------------------------------------------
#: rule id -> (function, default severity, what it looks for)
RULES: dict[str, tuple[Callable[[AnalysisContext], list[Flag]], str, str]] = {
    "missing_fiscal_year": (missing_fiscal_year, "warning", "a year absent inside a company's span"),
    "identity_mismatch": (identity_mismatch, "critical", "reported figures that do not recompute"),
    "negative_equity": (negative_equity, "critical", "shareholders' equity below zero"),
    "imputed_column": (imputed_column, "info", "an optional column that had to be derived"),
    "loader_warning": (loader_warning, "warning", "a validation warning raised while loading"),
    "revenue_decline": (revenue_decline, "warning", "revenue down on the prior year"),
    "margin_compression": (
        margin_compression, "warning", "net or gross margin down over the threshold",
    ),
    "negative_cagr": (negative_cagr, "warning", "revenue shrinking across the full span"),
    "fcf_negative": (
        fcf_negative, "warning", "two or more consecutive years of negative free cash flow",
    ),
    "ebitda_margin_collapse": (
        ebitda_margin_collapse, "warning", "EBITDA margin down over the threshold",
    ),
    "high_debt_to_equity": (high_debt_to_equity, "warning", "debt/equity over the threshold"),
    "low_interest_coverage": (
        low_interest_coverage, "critical", "EBIT below the interest-coverage threshold",
    ),
    "negative_working_capital": (
        negative_working_capital, "warning", "current liabilities exceed current assets",
    ),
    "high_net_debt_to_ebitda": (
        high_net_debt_to_ebitda, "warning", "net debt/EBITDA over the threshold",
    ),
}

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


def rule_names() -> tuple[str, ...]:
    """Every registered rule id, sorted."""
    return tuple(sorted(RULES))


def describe_rules() -> list[dict]:
    """The rule catalogue, for a prompt or the docs."""
    return [
        {"rule": name, "severity": severity, "looks_for": looks_for}
        for name, (_, severity, looks_for) in sorted(RULES.items())
    ]


def run_anomalies(ctx: AnalysisContext) -> list[Flag]:
    """Run every registered rule and return the flags, most severe first.

    Output order is deterministic (severity, ticker, year, rule) so the golden
    file in the test suite stays stable.
    """
    flags: list[Flag] = []
    for function, _, _ in RULES.values():
        flags.extend(function(ctx))
    flags.sort(
        key=lambda flag: (
            SEVERITY_ORDER.get(flag.severity, 99),
            flag.ticker,
            flag.fiscal_year if flag.fiscal_year is not None else -1,
            flag.rule,
        )
    )
    return flags


def flags_to_frame(flags: list[Flag]) -> pd.DataFrame:
    """Flags as a dataframe, with a fixed column order even when empty.

    ``fiscal_year`` is nullable-integer: some flags (a derived column, a loader
    warning) apply to a company as a whole rather than to one year, and a float
    column would render those as ``2020.0``.
    """
    columns = ("ticker", "fiscal_year", "rule", "severity", "message", "value")
    frame = pd.DataFrame([flag.to_dict() for flag in flags], columns=list(columns))
    frame["fiscal_year"] = frame["fiscal_year"].astype("Int64")
    return frame
