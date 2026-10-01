# Stage 1 — static CSV financial analysis

Stage 1 reads one CSV of company financials and produces a metrics table, a set
of anomaly flags and a written report. It contains **no language model and no
judgement**: every number comes from a pure function, every sentence comes from
a template, and every threshold is a named constant.

## How to read the numbers

**Ratios are decimal fractions in the data layer** (`0.153` means 15.3%) and are
formatted into percentages only in the report. There is one convention in the
data, and formatting happens at the edge.

**An undefined ratio is `None`, never `NaN` and never a number.** The guards are
the point of the module, not defensive noise:

| Metric | Refuses to compute when | Why |
|---|---|---|
| `return_on_equity`, `debt_to_equity`, `equity_multiplier` | equity ≤ 0 | ROE against negative equity describes accumulated losses, not a return. `-20 / -100 = 0.20` is the most dangerous arithmetic in this codebase. |
| `net_debt_to_ebitda`, `ev_to_ebitda` | EBITDA ≤ 0 | `272 / -90 = -3.0x` is not "low leverage", it is meaningless. |
| `growth_rate`, `cagr` | base ≤ 0 | Growth off a loss has no percentage form. |
| `price_to_earnings` | EPS ≤ 0 | A loss is not a cheap valuation. |
| `price_to_book` | BVPS ≤ 0 | Same reasoning. |
| `effective_tax_rate` | pre-tax income ≤ 0 | No meaningful rate against a loss. |
| `interest_coverage` | interest ≤ 0 | Net interest income needs no coverage. |
| everything | quotient overflows to ±inf | One `inf` poisons every average computed from the table. |

In the report an undefined value renders as `n/a`. Never as `nan`, `None` or a
blank — a blank reads as an oversight, and `nan%` reads as a measurement.

## Definitions

All monetary figures are USD millions. `total_debt = short_term_debt +
long_term_debt`, where short-term debt already includes the current portion of
long-term debt. Leases and trade payables are excluded from that definition; the
choice is stated rather than assumed.

- **Margins** — `gross_margin` is `(revenue - cogs) / revenue`. Others are
  `component / revenue`.
- **ROIC** — `NOPAT / (debt + equity - cash)`. Cash is excluded because it is
  non-operating.
- **NOPAT** — `EBIT × (1 - effective tax rate)`. Where pre-tax income is
  positive the effective rate is known; where it is not, ROIC is left undefined
  rather than assumed from a statutory rate.
- **Free cash flow** — `cash_from_operations - capex`, with capex stored as a
  positive magnitude.
- **Working-capital cycle** — 365-day basis: `DIO = 365 / inventory turnover`,
  `CCC = DSO + DIO - DPO`.
- **Year-over-year growth** is only computed across *consecutive* years. GLOB's
  FY2024 sits two years after its FY2022 row, so its growth is undefined and
  `years_since_prior` records the distance.

## Thresholds

All in `financial_analyst/config.py`, all reported in the output:

| Metric | Threshold | Severity when breached |
|---|---|---|
| Debt / equity | > 2.0x | warning |
| Interest coverage | < 1.0x | critical |
| Net debt / EBITDA | > 4.0x | warning |
| Margin compression | > 5.0pp in one year | warning |

At exactly the threshold is *not* a breach; the comparison is strictly greater
than. This is asserted at the boundary in `tests/test_anomalies.py`.

## The 14 rules

Data quality — `missing_fiscal_year`, `identity_mismatch`, `negative_equity`,
`imputed_column`, `loader_warning`.

Trend — `revenue_decline`, `margin_compression`, `negative_cagr`,
`fcf_negative`, `ebitda_margin_collapse`.

Leverage and distress — `high_debt_to_equity`, `low_interest_coverage`,
`negative_working_capital`, `high_net_debt_to_ebitda`.

A rule that finds nothing emits nothing. The report never says a company is
"clean" — only which rules ran and what they returned. Every rule has both a
firing and a silent test, so adding a rule without covering it fails the suite.

## Sanity check against reality

Unit tests prove the arithmetic matches its formula. Identity tests
(ROE = margin × turnover × multiplier; assets = liabilities + equity) prove the
formulas agree with each other. Neither catches a formula that is *consistently*
wrong.

To check the definitions themselves, take one real filing, compute the ratios by
hand for two or three metrics, and record the comparison in the table below.
Until that is done, treat the definitions as reviewed-but-unverified against
published figures.

| Company | Metric | Our value | Published value | Source | Matches |
|---|---|---|---|---|---|
| _(to be filled in Stage 1 review)_ | | | | | |

This is the one gap in the current test strategy: it needs a human with a filing
in front of them, and it is the only thing standing between "the code is
self-consistent" and "the code is right".
