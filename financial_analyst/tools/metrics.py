"""Dataframe layer: apply the pure functions in :mod:`calculations` per row.

The split exists so the ratio maths can be tested in isolation
(:mod:`calculations`) while this module only handles grouping, ordering and
column layout. Two rules it enforces:

* **One company's history never leaks into another's.** Every prior-period
  lookup is keyed by ticker.
* **A "year-over-year" figure is only produced across consecutive years.**
  GLOB's FY2024 sits two years after its FY2022 row; reporting growth across
  that gap as year-over-year would be wrong, so ``revenue_growth`` is ``None``
  and ``years_since_prior`` records the distance instead.

The input frame is never mutated.
"""

from __future__ import annotations

import pandas as pd

from . import calculations as calc
from .loaders import KEY_COLUMNS

IDENTITY_COLUMNS = ("ticker", "company_name", "sector", "fiscal_year", "period_end")

COMPANY_COLUMNS = (
    "first_year", "last_year", "years_covered", "missing_years", "revenue_cagr",
)

PERIOD_COLUMNS = ("years_since_prior", "revenue_growth")

#: Reported figures carried through unchanged, so the metrics table answers
#: "what was earned?" as well as "what were the ratios?" without a join back to
#: the raw CSV. Everything else in the raw file stays in the raw file.
REPORTED_COLUMNS = (
    "revenue", "cogs", "gross_profit", "operating_income", "net_income", "ebitda",
    "free_cash_flow", "total_assets", "total_liabilities", "total_equity",
    "cash_and_equivalents", "cash_from_operations", "capex",
)

METRIC_COLUMNS = (
    # derived quantities
    "total_debt", "net_debt", "nopat", "earnings_per_share", "book_value_per_share",
    "market_cap", "enterprise_value",
    # margins
    "gross_margin", "operating_margin", "net_margin", "ebitda_margin", "fcf_margin",
    "effective_tax_rate",
    # returns
    "return_on_assets", "return_on_equity", "return_on_invested_capital",
    "asset_turnover", "equity_multiplier",
    # leverage
    "debt_to_assets", "debt_to_equity", "liabilities_to_assets",
    "net_debt_to_ebitda", "interest_coverage",
    # liquidity
    "current_ratio", "quick_ratio", "cash_ratio", "working_capital",
    # efficiency
    "inventory_turnover", "days_inventory_outstanding", "receivables_turnover",
    "days_sales_outstanding", "payables_turnover", "days_payables_outstanding",
    "cash_conversion_cycle",
    # cash flow quality
    "fcf_conversion", "cfo_to_net_income", "capex_intensity",
    # valuation
    "price_to_earnings", "price_to_book", "ev_to_ebitda",
)

OUTPUT_COLUMNS = (
    IDENTITY_COLUMNS
    + COMPANY_COLUMNS
    + PERIOD_COLUMNS
    + REPORTED_COLUMNS
    + METRIC_COLUMNS
)


def _row_metrics(row: dict) -> dict:
    """Every ratio for a single company-year, as ``{column: value}``."""
    revenue = row.get("revenue")
    cogs = row.get("cogs")
    net_income = row.get("net_income")
    ebitda = row.get("ebitda")
    fcf = row.get("free_cash_flow")
    cash = row.get("cash_and_equivalents")
    equity = row.get("total_equity")
    assets = row.get("total_assets")
    cur_assets = row.get("total_current_assets")
    current_liabilities = row.get("total_current_liabilities")

    debt = calc.total_debt(row.get("short_term_debt"), row.get("long_term_debt"))
    net_debt = calc.net_debt(debt, cash)
    tax_rate = calc.effective_tax_rate(row.get("pretax_income"), row.get("tax_expense"))
    nopat_value = calc.nopat_from_pretax(row.get("operating_income"), tax_rate)

    eps = calc.earnings_per_share(net_income, row.get("diluted_shares"))
    bvps = calc.book_value_per_share(equity, row.get("shares_outstanding"))
    mcap = calc.market_cap(row.get("share_price"), row.get("shares_outstanding"))
    ev = calc.enterprise_value(mcap, debt, cash)

    dso = calc.days_sales_outstanding(revenue, row.get("accounts_receivable"))
    dio = calc.days_inventory_outstanding(cogs, row.get("inventory"))
    dpo = calc.days_payables_outstanding(cogs, row.get("accounts_payable"))

    return {
        "total_debt": debt,
        "net_debt": net_debt,
        "nopat": nopat_value,
        "earnings_per_share": eps,
        "book_value_per_share": bvps,
        "market_cap": mcap,
        "enterprise_value": ev,
        "gross_margin": calc.gross_margin(revenue, cogs),
        "operating_margin": calc.operating_margin(revenue, row.get("operating_income")),
        "net_margin": calc.net_margin(revenue, net_income),
        "ebitda_margin": calc.ebitda_margin(revenue, ebitda),
        "fcf_margin": calc.fcf_margin(revenue, fcf),
        "effective_tax_rate": tax_rate,
        "return_on_assets": calc.return_on_assets(net_income, assets),
        "return_on_equity": calc.return_on_equity(net_income, equity),
        "return_on_invested_capital": calc.return_on_invested_capital(
            nopat_value, debt, equity, cash
        ),
        "asset_turnover": calc.asset_turnover(revenue, assets),
        "equity_multiplier": calc.equity_multiplier(assets, equity),
        "debt_to_assets": calc.debt_to_assets(debt, assets),
        "debt_to_equity": calc.debt_to_equity(debt, equity),
        "liabilities_to_assets": calc.liabilities_to_assets(
            row.get("total_liabilities"), assets
        ),
        "net_debt_to_ebitda": calc.net_debt_to_ebitda(net_debt, ebitda),
        "interest_coverage": calc.interest_coverage(
            row.get("operating_income"), row.get("interest_expense")
        ),
        "current_ratio": calc.current_ratio(cur_assets, current_liabilities),
        "quick_ratio": calc.quick_ratio(
            cur_assets, row.get("inventory"), current_liabilities
        ),
        "cash_ratio": calc.cash_ratio(cash, current_liabilities),
        "working_capital": calc.working_capital(cur_assets, current_liabilities),
        "inventory_turnover": calc.inventory_turnover(cogs, row.get("inventory")),
        "days_inventory_outstanding": dio,
        "receivables_turnover": calc.receivables_turnover(
            revenue, row.get("accounts_receivable")
        ),
        "days_sales_outstanding": dso,
        "payables_turnover": calc.payables_turnover(cogs, row.get("accounts_payable")),
        "days_payables_outstanding": dpo,
        "cash_conversion_cycle": calc.cash_conversion_cycle(dso, dio, dpo),
        "fcf_conversion": calc.fcf_conversion(fcf, net_income),
        "cfo_to_net_income": calc.cfo_to_net_income(
            row.get("cash_from_operations"), net_income
        ),
        "capex_intensity": calc.capex_intensity(row.get("capex"), revenue),
        "price_to_earnings": calc.price_to_earnings(row.get("share_price"), eps),
        "price_to_book": calc.price_to_book(row.get("share_price"), bvps),
        "ev_to_ebitda": calc.ev_to_ebitda(ev, ebitda),
    }


def _company_summaries(frame: pd.DataFrame) -> dict[str, dict]:
    """Per-company span, gap count and CAGR, keyed by ticker."""
    summaries: dict[str, dict] = {}
    for ticker, group in frame.groupby("ticker", sort=True):
        ordered = group.sort_values("fiscal_year")
        years = [int(year) for year in ordered["fiscal_year"].tolist()]
        first_year, last_year = years[0], years[-1]
        span = last_year - first_year
        revenues = ordered["revenue"].tolist()
        summaries[ticker] = {
            "first_year": first_year,
            "last_year": last_year,
            # Observed years, not span length: a 2020-2024 file missing 2023 has
            # four observations across a five-year span.
            "years_covered": len(years),
            "missing_years": max(span - (len(years) - 1), 0),
            "revenue_cagr": calc.cagr(revenues[0], revenues[-1], span),
        }
    return summaries


def compute_metric_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Return ``frame`` plus every derived metric, one row per company-year."""
    work = frame.sort_values(list(KEY_COLUMNS)).reset_index(drop=True)
    summaries = _company_summaries(work)

    previous: dict[str, tuple[int, object]] = {}
    records: list[dict] = []

    for row in work.to_dict("records"):
        ticker = row["ticker"]
        year = int(row["fiscal_year"])

        record = {name: row.get(name) for name in IDENTITY_COLUMNS}
        record.update({name: row.get(name) for name in REPORTED_COLUMNS})
        record.update(summaries[ticker])

        prior = previous.get(ticker)
        if prior is None:
            record["years_since_prior"] = None
            record["revenue_growth"] = None
        else:
            gap = year - prior[0]
            record["years_since_prior"] = gap
            # Only a true consecutive pair yields a "year-over-year" growth rate.
            record["revenue_growth"] = (
                calc.growth_rate(row.get("revenue"), prior[1]) if gap == 1 else None
            )

        record.update(_row_metrics(row))
        records.append(record)
        previous[ticker] = (year, row.get("revenue"))

    return pd.DataFrame.from_records(records, columns=list(OUTPUT_COLUMNS))
