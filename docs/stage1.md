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

Unit tests prove the arithmetic matches its formula. Identity tests prove the
formulas agree with each other. Neither catches a formula that is *consistently*
wrong. To check the definitions themselves, the pipeline was run over a real
filing and the output compared with the figures a finance professional would
quote.

**Source:** Apple Inc., CIK 0000320193, annual figures from SEC EDGAR XBRL
`companyfacts`, retrieved 2026-10-01. Accessions FY2022 `0000320193-24-000123`,
FY2023/FY2024 `0000320193-25-000079`. Reproduced in
`tests/fixtures/apple_fy2022_2024.csv` and pinned by
`tests/test_sanity_real_filings.py` (22 tests).

### FY2024 (year ended 2024-09-28), USD millions

| Metric | Ours | From the filing | Matches |
|---|---|---|---|
| Gross margin | 46.2% | 180,683 / 391,035 = 46.2% | ✅ |
| Operating margin | 31.5% | 123,216 / 391,035 = 31.5% | ✅ |
| Net margin | 24.0% | 93,736 / 391,035 = 24.0% | ✅ |
| Return on equity | 164.6% | 93,736 / 56,950 = 164.6% | ✅ |
| Return on assets | 25.7% | 93,736 / 364,980 = 25.7% | ✅ |
| Debt / assets | 26.5% | 96,662 / 364,980 = 26.5% | ✅ |
| Debt / equity | 1.70x | 96,662 / 56,950 = 1.70x | ✅ |
| Current ratio | 0.87x | 152,987 / 176,392 = 0.87x | ✅ |
| Free cash flow | $108,807M | 118,254 − 9,447 = 108,807 | ✅ |
| **Diluted EPS** | **$6.08** | **$6.08 as filed** | ✅ |

EPS is the strongest line in the table: Apple publishes it, we do not, so
agreement validates both the EPS definition and the share-count scaling
(millions of shares, not units) at once.

The 164.6% ROE is correct and looks absurd. It is not a bug: Apple returns so
much capital that equity is a small base, giving an equity multiplier of only
6.4x. A tool that could not reproduce a surprising-but-right figure would not be
worth trusting.

### What real data found: one definition was wrong — and is now fixed

The identities that held on synthetic data **did not all hold on real filings.**

| Identity | Synthetic sample | Apple FY2022 | Apple FY2023 | Now |
|---|---|---|---|---|
| assets = liabilities + equity | ✅ | ✅ exact | ✅ exact | ✅ |
| gross profit = revenue − COGS | ✅ | ✅ exact | ✅ exact | ✅ |
| operating income = gross profit − opex | ✅ | ✅ exact | ✅ exact | ✅ |
| net income = pre-tax − tax | ✅ | ✅ exact | ✅ exact | ✅ |
| **pre-tax = operating income − interest** | ✅ | ❌ **+2,597** | ❌ **+3,368** | **fixed** |

We assumed the only item between EBIT and pre-tax income is interest expense.
That was true of the synthetic sample, where I set other income to zero — and
false of essentially every real company. The residual was exactly Apple's other
non-operating income.

**The fix.** Model the line the way companies actually present it: a single net
non-operating figure with interest already inside it.

```
pretax_income == operating_income + other_income
```

| | EBIT | Other income/(expense), net | = filed pre-tax |
|---|---|---|---|
| FY2022 | 119,437 | −334 | **119,103** ✅ |
| FY2023 | 114,301 | −565 | **113,736** ✅ |
| FY2024 | 123,216 | +269 | **123,485** ✅ |

All three balance exactly, and the loader and the `identity_mismatch` rule are
now silent on Apple's filings.

`interest_expense` is kept for `interest_coverage` only — it is **not**
subtracted in the bridge as well, since that would double-count it. And a
missing `other_income` leaves the bridge genuinely unknown, so the pre-tax check
is skipped rather than defaulted to zero; defaulting would have reinstated the
same error, silently, and only on real data.

Guarded by `test_no_false_alarm_reaches_the_report`, and against regression by
`test_the_old_assumption_is_not_silently_reinstated`.

### Extending this

Add a company by adding its annual 10-K figures to a CSV of the same shape and
writing the expectations a reader would check against. One real company per
industry shape is worth more than four synthetic ones. A business with leases, a
financial with a different revenue/interest tagging, and a bank would each break
a different assumption.

