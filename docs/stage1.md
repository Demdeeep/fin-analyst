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

## Second industry shape: a loss-making consumer company

Apple is the easy case. It is profitable, solvent and unremarkable in sign, so it
exercises **none** of the negative-value guards. Those had only ever been tested
against synthetic data until a second real filing was added.

**Source:** Beyond Meat Inc., CIK 0001655210, FY2022–FY2024, SEC EDGAR XBRL
`companyfacts`, retrieved 2026-10-01, in
`tests/fixtures/beyond_meat_fy2022_2024.csv`.

| Property | FY2022 | FY2023 | FY2024 |
|---|---|---|---|
| Shareholders' equity | **−203.5** | **−513.4** | **−601.2** |
| Gross profit | **−23.7** | **−82.7** | 41.7 |
| Operating income | −342.8 | −341.9 | −156.1 |
| Net income | −366.1 | −338.1 | −160.3 |
| Cash from operations | −320.2 | −107.8 | −98.8 |
| Diluted EPS (ours) | **−5.76** | **−5.26** | **−2.43** |
| Diluted EPS (as filed) | −5.75 | −5.26 | −2.43 |

**What this proves.** The guards now face a real balance sheet. Negative equity
suppresses ROE, debt/equity, the equity multiplier and P/B; negative EBITDA
suppresses net debt/EBITDA and EV/EBITDA; net losses suppress P/E, FCF
conversion and CFO/net income — every one of them `n/a`, never a
plausible-looking number. The `fcf_negative` and `low_interest_coverage` rules
fire on real filings. Filed EPS matches again, this time on the negative side.

It also **re-validated the pre-tax fix from the previous section**: gross
profit, operating income and pre-tax all reconcile to **0.000** on a company
that bears no resemblance to Apple, including pre-tax *losses* bridged by a
non-operating line.

### Two more gaps — both now fixed

**1. `net_income == pretax_income − tax_expense` broke on minority interests.**

| | pretax − tax | filed net income | gap |
|---|---|---|---|
| FY2022 | −347.189 | −366.137 | **−18.948** |
| FY2023 | −334.242 | −338.144 | **−3.902** |
| FY2024 | −160.205 | −160.278 | **−0.073** |

Beyond Meat's tagged `NetIncomeLoss` is the loss *attributable to Beyond Meat*,
which sits below the post-tax total. The residual is exactly the noncontrolling
interest, so the identity now reads

```
net_income == pretax_income - tax_expense - minority_interest
```

**2. `assets == liabilities + equity` did not hold here.** Beyond Meat does not
tag `Liabilities` at all — redeemable noncontrolling interests sit in mezzanine
equity, outside both totals — so the fixture leaves `total_liabilities` blank
and the check is *skipped*.

This one nearly produced a **third false alarm**: the code substituted zero for
the missing figure, yielding a residual of 1,265.8 — the entire balance sheet —
as a `critical` flag on clean data. That contradicted the project's own rule
that a missing value is "not checked", never a number. Fixed in
`balance_sheet_residual` and covered by
`test_balance_sheet_is_skipped_not_faked_when_a_side_is_missing`.

Beyond Meat's 10-K tags no minority-interest figure either, so that fixture's
`minority_interest` is *derived* from the residual — which makes the net-income
identity a tautology for that file. The column earns its keep for companies that
disclose it separately, and the evidence here was the residual itself.

### The pattern

Three of the four identities that looked verified on Apple turned out to assume
the simplest possible capital structure: no minority interests, no mezzanine
equity, no non-interest non-operating items. **One happy-path company cannot
validate a schema meant for messy ones** — it can only confirm the arithmetic.
All three findings are now fixed, each one found by a different real filing.

## Third industry shape: a lease-heavy retailer

The first two companies questioned the *arithmetic*. This one questions a
*definition*.

**Source:** Target Corporation, CIK 0000027419, FY2024–FY2026, SEC EDGAR XBRL
`companyfacts`, retrieved 2026-10-01, in `tests/fixtures/target_fy2024_2026.csv`.
Its fiscal years end in late January, so it is not a calendar-year company like
the other two.

### What still holds

All four identities reconcile, with zero loader warnings. Filed diluted EPS
matches on all three years: **8.94 / 8.86 / 8.13**.

Target also gives the `other_income` model a third independent test, because it
reports interest and other income as **separate** lines where Apple reported one
net figure:

```
FY2026  EBIT 5,117 + (other 95 − interest 445) = 4,767 filed pre-tax
```

### The finding: leases are excluded from debt, and that is material

Our debt is `short_term_debt + long_term_debt` as tagged. Leases are not in it.

| fiscal year | our debt/assets | + operating leases | + all leases | understated by |
|---|---|---|---|---|
| FY2024 | 27.6% | 34.1% | 37.7% | **10.2pp** |
| FY2025 | 26.9% | 33.7% | 37.5% | **10.6pp** |
| FY2026 | 27.8% | 34.2% | 37.8% | **10.0pp** |

For a retailer running most of its estate on lease, that is the difference
between what our tool reports and what a rating agency would quote. An agent
reading `debt_to_assets` deserves to know that.

The judgement splits in two. **Operating leases** (~$3.8bn) are arguably not
debt — they are rent, and the rent is already in operating expenses.
**Finance leases** (~$2.1bn, about 3.4pp of assets) are a financing obligation
recognised on the balance sheet, and rating agencies add them to debt without
discussion. Excluding those is a real understatement, not a matter of taste.

This is recorded as a documented caveat rather than a defect, and deliberately
does **not** raise a flag: Target is a clean filing, and turning a definitional
choice into a data error is the failure mode every previous bug here had.

Note also that Target tags no `Liabilities` fact at all, so its balance-sheet
check is *skipped* — the third company to need that treatment. Deriving it as
assets minus equity would have made the identity a tautology.

### The fourth shape is still outstanding

A **bank** is a different problem, not just another company to add. For a bank,
interest is *revenue*, so `operating_income` and `net_margin` are not
meaningful as defined — the tool would compute correct numbers describing
nothing real. Fixing that means giving the tool the ability to **refuse** and
say the definitions do not apply, which is a new capability rather than a
correction. `test_a_bank_would_be_wrong_in_a_way_a_retailer_is_not` records the
distinction so it cannot be quietly forgotten.



