"""Read the static financials CSV into a validated, typed dataframe.

This is the only module in the tools layer that touches files. It draws a hard
line between two kinds of problem:

* **Errors** -- the file cannot be trusted (a missing column, a non-numeric cell
  in a numeric column, a duplicated company-year, a non-USD currency). These
  raise :class:`FinancialsValidationError`, because continuing would produce
  confidently wrong numbers.
* **Warnings** -- the file is usable but suspect (an accounting identity that
  does not balance, an optional column that had to be imputed). These are
  collected and returned, and the anomaly engine turns them into flags, because
  a later agent should *see* them rather than have the run abort.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

from ..config import BALANCE_TOLERANCE, CURRENCY_REQUIRED, INCOME_STATEMENT_TOLERANCE
from . import calculations as calc

KEY_COLUMNS = ("ticker", "fiscal_year")

TEXT_COLUMNS = (
    "ticker", "company_name", "sector", "period_end", "currency", "source",
)

NUMERIC_COLUMNS = (
    "revenue", "cogs", "gross_profit", "operating_expenses", "operating_income",
    "interest_expense", "pretax_income", "tax_expense", "net_income", "ebitda",
    "depreciation_amortization", "diluted_shares", "cash_and_equivalents",
    "accounts_receivable", "inventory", "total_current_assets", "total_assets",
    "accounts_payable", "short_term_debt", "total_current_liabilities",
    "long_term_debt", "total_liabilities", "total_equity", "cash_from_operations",
    "capex", "dividends_paid", "free_cash_flow", "share_price", "shares_outstanding",
)

REQUIRED_COLUMNS = KEY_COLUMNS + (
    "company_name", "currency", "revenue", "cogs", "gross_profit",
    "operating_expenses", "operating_income", "interest_expense", "pretax_income",
    "tax_expense", "net_income", "diluted_shares", "cash_and_equivalents",
    "inventory", "total_current_assets", "total_assets", "short_term_debt",
    "total_current_liabilities", "long_term_debt", "total_liabilities",
    "total_equity", "cash_from_operations", "capex",
)

#: Optional columns the loader can rebuild from required ones.
DERIVABLE = ("ebitda", "free_cash_flow")


class FinancialsValidationError(ValueError):
    """The source file is structurally untrustworthy."""


@dataclass(frozen=True)
class FinancialsDataset:
    """A validated frame plus everything the caller should know about it."""

    frame: pd.DataFrame
    warnings: tuple[str, ...] = ()
    imputed_columns: tuple[str, ...] = ()
    #: The same rows *before* optional columns were filled in, so a later stage
    #: can still tell which cells were blank in the source file.
    raw_frame: Optional[pd.DataFrame] = None

    @property
    def tickers(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self.frame["ticker"].tolist()))

    @property
    def has_warnings(self) -> bool:
        return bool(self.warnings)


def _check_columns(raw: pd.DataFrame) -> None:
    missing = [name for name in REQUIRED_COLUMNS if name not in raw.columns]
    if missing:
        raise FinancialsValidationError(
            f"missing required column(s): {', '.join(missing)}"
        )


def _coerce_numeric(raw: pd.DataFrame) -> pd.DataFrame:
    """Convert numeric columns, failing loudly on any non-numeric cell.

    An empty cell is legitimate (optional columns may be blank). A cell holding
    text like ``"n/a"`` or ``"1,200"`` is not, and silently becoming ``NaN``
    would hide it -- so we locate those cells and name them in the error.
    """
    frame = raw.copy()
    present = [name for name in NUMERIC_COLUMNS if name in frame.columns]
    original = frame[present]
    coerced = original.apply(pd.to_numeric, errors="coerce")

    broken = original.notna() & coerced.isna()
    offenders: list[str] = []
    for column in present:
        rows = broken.index[broken[column]].tolist()
        offenders.extend(f"{column}@{_label(frame, row)}" for row in rows)
    if offenders:
        raise FinancialsValidationError(
            "non-numeric value(s) in numeric column(s): " + ", ".join(offenders)
        )
    frame[present] = coerced
    return frame


def _label(frame: pd.DataFrame, row: int) -> str:
    ticker = frame.at[row, "ticker"] if "ticker" in frame.columns else "?"
    year = frame.at[row, "fiscal_year"] if "fiscal_year" in frame.columns else "?"
    return f"{ticker}:{year}"


def _check_key(frame: pd.DataFrame) -> None:
    if "fiscal_year" in frame.columns:
        years = pd.to_numeric(frame["fiscal_year"], errors="coerce")
        if years.isna().any():
            raise FinancialsValidationError("fiscal_year must be present on every row")
        whole = years == years.round()
        if not whole.all():
            bad = [_label(frame, idx) for idx in frame.index[~whole].tolist()]
            raise FinancialsValidationError(f"non-integer fiscal_year: {', '.join(bad)}")
        frame["fiscal_year"] = years.astype(int)

    duplicated = frame.duplicated(subset=list(KEY_COLUMNS), keep=False)
    if duplicated.any():
        offenders = [
            f"{frame.at[idx, 'ticker']}:{frame.at[idx, 'fiscal_year']}"
            for idx in frame.index[duplicated].tolist()
        ]
        raise FinancialsValidationError(
            "duplicate (ticker, fiscal_year) row(s): " + ", ".join(offenders)
        )


def _check_currency(frame: pd.DataFrame) -> None:
    if "currency" not in frame.columns:
        return
    currencies = sorted(set(frame["currency"].dropna().astype(str)))
    unexpected = [c for c in currencies if c != CURRENCY_REQUIRED]
    if unexpected:
        raise FinancialsValidationError(
            f"expected all rows in {CURRENCY_REQUIRED}, found: {', '.join(unexpected)}"
        )


def _impute_derivable(frame: pd.DataFrame) -> list[str]:
    """Rebuild optional columns the schema lets us derive. Returns the names.

    Only cells that are actually blank are filled; a value that was supplied is
    left exactly as reported so the identity check can still compare it.
    """
    imputed: list[str] = []
    recipes = (
        (
            "ebitda",
            ("operating_income", "depreciation_amortization"),
            lambda: frame["operating_income"] + frame["depreciation_amortization"],
        ),
        (
            "free_cash_flow",
            ("cash_from_operations", "capex"),
            lambda: frame["cash_from_operations"] - frame["capex"],
        ),
    )
    for name, (left, right), recipe in recipes:
        if name not in frame.columns or not frame[name].isna().any():
            continue
        if {left, right} <= set(frame.columns):
            frame[name] = frame[name].fillna(recipe())
            imputed.append(name)
    return imputed


def _identity_warnings(frame: pd.DataFrame) -> list[str]:
    """Compare stored vs. recomputed figures; report disagreements as warnings."""
    messages: list[str] = []
    for row in frame.to_dict("records"):
        label = f"{row['ticker']}:{row['fiscal_year']}"

        residual = calc.balance_sheet_residual(
            row.get("total_assets"),
            row.get("total_liabilities"),
            row.get("total_equity"),
        )
        if residual is not None and abs(residual) > BALANCE_TOLERANCE:
            messages.append(
                f"{label}: total_assets - (total_liabilities + total_equity) "
                f"= {residual:+.2f}"
            )

        for line, residual in calc.income_statement_residuals(row).items():
            if residual is None:
                continue
            if abs(residual) > INCOME_STATEMENT_TOLERANCE:
                messages.append(f"{label}: {line} differs from recomputed by {residual:+.2f}")

        if row.get("ebitda") is not None and pd.notna(row.get("ebitda")):
            expected = calc.ebitda_from_components(
                row.get("operating_income"), row.get("depreciation_amortization")
            )
            if expected is not None and abs(row["ebitda"] - expected) > INCOME_STATEMENT_TOLERANCE:
                messages.append(f"{label}: ebitda differs from EBIT + D&A by "
                                f"{row['ebitda'] - expected:+.2f}")
    return messages


def find_missing_years(frame: pd.DataFrame) -> list[dict[str, object]]:
    """Years missing from the interior of each company's observed span.

    A gap at the *edges* is invisible here and that is correct -- we cannot know
    that a FY2019 file "should" have started in 2018. Gaps are computed per
    company so that two companies cannot mask each other's missing year.

    >>> find_missing_years(pd.DataFrame(
    ...     {"ticker": ["A", "A", "A"], "fiscal_year": [2020, 2021, 2023]}
    ... ))
    [{'ticker': 'A', 'fiscal_year': 2022}]
    """
    gaps: list[dict[str, object]] = []
    for ticker, group in frame.groupby("ticker", sort=True):
        years = sorted(int(year) for year in group["fiscal_year"])
        if len(years) < 2:
            continue
        for year in range(years[0] + 1, years[-1]):
            if year not in years:
                gaps.append({"ticker": ticker, "fiscal_year": year})
    return gaps


def load_financials(path: str | Path) -> FinancialsDataset:
    """Load, validate and lightly impute the financials CSV at ``path``."""
    source = Path(path)
    if not source.exists():
        raise FinancialsValidationError(f"input file not found: {source}")

    raw = pd.read_csv(source)
    if raw.empty:
        raise FinancialsValidationError(f"input file has no data rows: {source}")

    _check_columns(raw)
    frame = _coerce_numeric(raw)
    _check_key(frame)
    _check_currency(frame)

    pre_imputation = frame.copy()
    imputed = _impute_derivable(frame)
    warnings = _identity_warnings(frame)

    frame = frame.sort_values(list(KEY_COLUMNS)).reset_index(drop=True)
    return FinancialsDataset(
        frame=frame,
        warnings=tuple(warnings),
        imputed_columns=tuple(imputed),
        raw_frame=pre_imputation,
    )
