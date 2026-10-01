"""Assemble the Stage 1 markdown report from templates and metrics.

Strictness is the theme here, because generated prose fails quietly:

* A missing template key **raises** rather than rendering an empty gap, via
  :class:`_StrictContext`.
* After formatting, any surviving ``{...}`` raises as well -- so a typo in a
  placeholder can never reach the reader as ``{net_margn}``.
* Comparative claims require a real comparison: with fewer than two companies
  the peer sentence is omitted rather than padded.
* ``n/a`` is a legitimate value; it is produced by the formatters, not by
  string surgery here.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from .. import config
from ..tools import anomalies as an
from ..utils import formatting as fmt
from . import templates as tpl

SEVERITY_ICON = {"critical": "🔴", "warning": "🟠", "info": "🔵"}


class _StrictContext(dict):
    """A mapping that refuses to invent values for missing template keys."""

    def __missing__(self, key: str):
        raise KeyError(f"template placeholder {{{key}}} has no value")


def _num(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number


def _flag_icon(flags: list[an.Flag]) -> str:
    severities = {flag.severity for flag in flags}
    for level in ("critical", "warning", "info"):
        if level in severities:
            return SEVERITY_ICON[level]
    return SEVERITY_ICON["info"]


def _company_context(rows: list[dict], peer_margins: list[float]) -> _StrictContext:
    first, last = rows[0], rows[-1]
    first_margin = _num(first.get("net_margin"))
    last_margin = _num(last.get("net_margin"))
    change = (
        None if first_margin is None or last_margin is None
        else last_margin - first_margin
    )
    return _StrictContext(
        ticker=last.get("ticker", "?"),
        company_name=last.get("company_name", "?"),
        sector=last.get("sector") or "Sector not stated",
        span=fmt.span(last.get("first_year"), last.get("last_year")),
        years_covered=int(last.get("years_covered") or len(rows)),
        first_year=int(last["first_year"]),
        last_year=int(last["last_year"]),
        revenue_first=fmt.money(first.get("revenue")),
        revenue_last=fmt.money(last.get("revenue")),
        revenue_cagr=fmt.pct(last.get("revenue_cagr")),
        net_margin_first=fmt.pct(first_margin),
        net_margin_last=fmt.pct(last_margin),
        margin_direction=fmt.direction(last_margin, first_margin),
        margin_change=fmt.pp(change),
        gross_margin=fmt.pct(last.get("gross_margin")),
        operating_margin=fmt.pct(last.get("operating_margin")),
        ebitda_margin=fmt.pct(last.get("ebitda_margin")),
        return_on_equity=fmt.pct(last.get("return_on_equity")),
        free_cash_flow=fmt.money(last.get("free_cash_flow")),
        debt_to_assets=fmt.pct(last.get("debt_to_assets")),
        debt_to_equity=fmt.multiple(last.get("debt_to_equity")),
        interest_coverage=fmt.multiple(last.get("interest_coverage")),
        net_debt_to_ebitda=fmt.multiple(last.get("net_debt_to_ebitda")),
        current_ratio=fmt.multiple(last.get("current_ratio"), digits=2),
        working_capital=fmt.money(last.get("working_capital")),
        peer_rank=fmt.peer_rank(last.get("gross_margin"), peer_margins) or "",
    )


def _company_section(
    rows: list[dict], flags: list[an.Flag], peer_margins: list[float]
) -> str:
    context = _company_context(rows, peer_margins)
    lines = [
        tpl.COMPANY_HEADER.format_map(context),
        "",
        tpl.COMPANY_META.format_map(context),
        "",
        tpl.COMPANY_SCALE.format_map(context),
        "",
        tpl.COMPANY_MARGIN.format_map(context),
        "",
        tpl.COMPANY_LATEST.format_map(context),
        "",
        tpl.COMPANY_LEVERAGE.format_map(context),
        "",
        tpl.COMPANY_LIQUIDITY.format_map(context),
    ]

    # A comparison needs something to compare against.
    if len(peer_margins) > 1 and context["peer_rank"]:
        lines += ["", tpl.COMPANY_PEER.format_map(context)]

    lines += [""]
    if flags:
        lines.append(tpl.COMPANY_FLAGS_SOME.format_map(_StrictContext(
            flag_icon=_flag_icon(flags), flag_count=len(flags)
        )))
        for flag in flags:
            # Without the year, five consecutive low_interest_coverage flags read
            # as five identical lines.
            lines.append(tpl.COMPANY_FLAG_ITEM.format_map(_StrictContext(
                severity=flag.severity,
                rule=flag.rule,
                year_label=f", FY{flag.fiscal_year}" if flag.fiscal_year else "",
                message=flag.message,
            )))
    else:
        lines.append(tpl.COMPANY_FLAGS_NONE.format_map(
            _StrictContext(rule_count=len(an.RULES))
        ))
    return "\n".join(lines)


def _flag_summary(flags: list[an.Flag]) -> list[str]:
    if not flags:
        return [tpl.FLAG_SUMMARY_HEADER, "", tpl.FLAG_SUMMARY_EMPTY]
    lines = [
        tpl.FLAG_SUMMARY_HEADER,
        "",
        tpl.FLAG_SUMMARY_ROW,
        tpl.FLAG_SUMMARY_DIVIDER,
    ]
    for flag in flags:
        year = f"FY{flag.fiscal_year}" if flag.fiscal_year else "--"
        lines.append(
            f"| {flag.ticker} | {flag.severity} | `{flag.rule}` | {year} | {flag.message} |"
        )
    return lines


def render_report(
    metrics: pd.DataFrame,
    flags: list[an.Flag],
    *,
    source_name: str = "company_financials.csv",
) -> str:
    """Render the whole Stage 1 report as markdown.

    ``metrics`` is the frame from
    :func:`financial_analyst.tools.metrics.compute_metric_frame` and ``flags``
    the output of
    :func:`financial_analyst.tools.anomalies.run_anomalies`.
    """
    ordered = metrics.sort_values(["ticker", "fiscal_year"])
    tickers = sorted(set(ordered["ticker"]))
    years = sorted(int(year) for year in ordered["fiscal_year"])

    latest_rows = {
        ticker: group.sort_values("fiscal_year").to_dict("records")
        for ticker, group in ordered.groupby("ticker", sort=True)
    }
    peer_margins = [
        _num(rows[-1].get("gross_margin")) for rows in latest_rows.values()
    ]
    peer_margins = [value for value in peer_margins if value is not None]

    flags_by_ticker: dict[str, list[an.Flag]] = {ticker: [] for ticker in tickers}
    for flag in flags:
        flags_by_ticker.setdefault(flag.ticker, []).append(flag)

    lines: list[str] = [
        tpl.REPORT_TITLE,
        "",
        tpl.REPORT_INTRO.format_map(_StrictContext(
            source_name=source_name,
            company_count=len(tickers),
            row_count=len(ordered),
            span=fmt.span(years[0], years[-1]) if years else fmt.NOT_AVAILABLE,
        )),
        "",
        tpl.METHODOLOGY,
        tpl.THRESHOLD_HEADER,
        "",
        tpl.THRESHOLD_ROWS.format_map(_StrictContext(
            debt_to_equity=fmt.multiple(config.DEBT_TO_EQUITY_THRESHOLD),
            interest_coverage=fmt.multiple(config.INTEREST_COVERAGE_THRESHOLD),
            net_debt_to_ebitda=fmt.multiple(config.NET_DEBT_TO_EBITDA_THRESHOLD),
            margin_compression=fmt.number(config.MARGIN_COMPRESSION_THRESHOLD_PP) + "pp",
        )),
        "",
    ]
    lines += _flag_summary(flags)
    lines.append("")

    for ticker in tickers:
        lines.append(_company_section(
            latest_rows[ticker], flags_by_ticker.get(ticker, []), peer_margins
        ))
        lines.append("")

    lines.append(tpl.FOOTER)
    report = "\n".join(lines).rstrip() + "\n"

    leftover = [token for token in ("{", "}") if token in report]
    if leftover:
        raise ValueError(
            f"unsubstituted template placeholder left in the report: {leftover}"
        )
    return report
