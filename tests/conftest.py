"""Shared fixtures.

Two sources of test data, deliberately different in character:

* ``dataset`` / ``metrics`` / ``flags`` -- the real sample CSV, which contains
  deliberate wrinkles (a missing year, a loss-making company with negative
  equity, blank optional columns). Tests using it are integration tests that
  check the pipeline agrees with itself.
* ``make_row`` / ``healthy_frame`` -- hand-built rows on round numbers, used for
  unit tests where the expected value can be worked out on paper. Testing the
  formula against the formula is worthless; testing ``100 -> 110 is 0.10`` is
  not.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from financial_analyst import config
from financial_analyst.tools import anomalies as an
from financial_analyst.tools.loaders import load_financials
from financial_analyst.tools.metrics import compute_metric_frame


def make_row(**overrides) -> dict:
    """One internally consistent company-year row on round numbers.

    Defaults satisfy every accounting identity exactly, so a test only has to
    name the fields it wants to change.
    """
    row = {
        "ticker": "AAA", "company_name": "Alpha Industries", "sector": "Industrials",
        "fiscal_year": 2020, "period_end": "2020-12-31", "currency": "USD",
        "revenue": 1000.0, "cogs": 600.0, "gross_profit": 400.0,
        "operating_expenses": 300.0, "operating_income": 100.0,
        "interest_expense": 10.0, "pretax_income": 90.0, "tax_expense": 20.0,
        "net_income": 70.0, "ebitda": 130.0, "depreciation_amortization": 30.0,
        "diluted_shares": 100.0,
        "cash_and_equivalents": 100.0, "accounts_receivable": 80.0, "inventory": 100.0,
        "total_current_assets": 280.0, "total_assets": 1000.0,
        "accounts_payable": 90.0, "short_term_debt": 50.0,
        "total_current_liabilities": 150.0, "long_term_debt": 250.0,
        "total_liabilities": 400.0, "total_equity": 600.0,
        "cash_from_operations": 120.0, "capex": 40.0, "dividends_paid": 20.0,
        "free_cash_flow": 80.0, "share_price": 30.0, "shares_outstanding": 100.0,
        "source": "fixture",
    }
    row.update(overrides)
    return row


def scale(row: dict, factor: float, **overrides) -> dict:
    """Grow every money field of ``row`` by ``factor``, keeping it consistent.

    Scales a *copy*, so identifying fields (ticker, year, share counts) are
    preserved rather than dropped -- share counts and prices must not compound
    away along with the money.
    """
    money_fields = (
        "revenue", "cogs", "gross_profit", "operating_expenses", "operating_income",
        "interest_expense", "pretax_income", "tax_expense", "net_income", "ebitda",
        "depreciation_amortization", "cash_and_equivalents", "accounts_receivable",
        "inventory", "total_current_assets", "total_assets", "accounts_payable",
        "short_term_debt", "total_current_liabilities", "long_term_debt",
        "total_liabilities", "total_equity", "cash_from_operations", "capex",
        "dividends_paid", "free_cash_flow",
    )
    grown = dict(row)
    for name in money_fields:
        value = row.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            grown[name] = round(value * factor, 6)
    grown.update(overrides)
    return grown


@pytest.fixture(scope="session")
def sample_csv() -> Path:
    path = config.DEFAULT_INPUT_CSV
    if not path.exists():
        pytest.skip(f"sample data missing: {path}")
    return path


@pytest.fixture(scope="session")
def dataset(sample_csv: Path):
    return load_financials(sample_csv)


@pytest.fixture(scope="session")
def metrics(dataset):
    return compute_metric_frame(dataset.frame)


@pytest.fixture(scope="session")
def context(dataset, metrics):
    return an.AnalysisContext(
        metrics=metrics,
        source=dataset.frame,
        raw=dataset.raw_frame,
        imputed_columns=dataset.imputed_columns,
        loader_warnings=dataset.warnings,
    )


@pytest.fixture(scope="session")
def flags(context):
    return an.run_anomalies(context)


@pytest.fixture
def healthy_frame() -> pd.DataFrame:
    """Two companies, three years, nothing any rule should complain about."""
    rows = []
    for ticker, name in (("AAA", "Alpha Industries"), ("BBB", "Beta Traders")):
        for offset, year in enumerate((2020, 2021, 2022)):
            base = make_row(ticker=ticker, company_name=name, fiscal_year=year,
                            period_end=f"{year}-12-31")
            # 5% a year: positive growth, constant margins, so no trend rule fires.
            rows.append(scale(base, 1.05 ** offset))
    frame = pd.DataFrame(rows)
    return frame.sort_values(["ticker", "fiscal_year"]).reset_index(drop=True)


@pytest.fixture
def healthy_context(healthy_frame: pd.DataFrame) -> an.AnalysisContext:
    metrics = compute_metric_frame(healthy_frame)
    return an.AnalysisContext(metrics=metrics, source=healthy_frame, raw=healthy_frame)
