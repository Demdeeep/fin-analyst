# Stage 1 financial analysis

Deterministic analysis of `company_financials.csv`. 4 companies, 19 company-year rows, covering FY2020–FY2024. All monetary figures are USD millions. Ratios are shown as percentages; `n/a` marks a value that is undefined rather than unknown -- see `docs/stage1.md` for why those two are kept apart.

## How to read this

Every figure below is computed by `financial_analyst/tools/calculations.py`,
a module of pure functions with no I/O and no judgement. Where a ratio would
have an undefined denominator -- negative equity, negative EBITDA, growth off a
loss-making base -- this report prints `n/a` instead of a number.

Flags come from `financial_analyst/tools/anomalies.py`: fixed rules with
thresholds in `financial_analyst/config.py`. A rule that finds nothing says
nothing; this report never claims a company is "clean", only which rules ran
and what they returned.

### Thresholds applied

| Metric | Threshold |
| --- | --- |
| Debt / equity | at or below 2.0x |
| Interest coverage | at or above 1.0x |
| Net debt / EBITDA | at or below 4.0x |
| Margin compression | a fall of more than 5.0pp in one year |

### Flags raised across the sample

| Company | Severity | Rule | Year | Detail |
| --- | --- | --- | --- | --- |
| INIT | critical | `low_interest_coverage` | FY2020 | operating income covers interest -0.84x, below the 1.0x threshold |
| INIT | critical | `low_interest_coverage` | FY2021 | operating income covers interest -2.01x, below the 1.0x threshold |
| INIT | critical | `low_interest_coverage` | FY2022 | operating income covers interest -2.57x, below the 1.0x threshold |
| INIT | critical | `negative_equity` | FY2022 | shareholders' equity is -20.0; ROE and debt-to-equity are undefined this year |
| INIT | critical | `low_interest_coverage` | FY2023 | operating income covers interest -1.17x, below the 1.0x threshold |
| INIT | critical | `low_interest_coverage` | FY2024 | operating income covers interest 0.36x, below the 1.0x threshold |
| GLOB | warning | `fcf_negative` | FY2021 | free cash flow -10.0, the 2nd consecutive negative year |
| GLOB | warning | `missing_fiscal_year` | FY2023 | FY2023 has no row between this company's first and last reported year |
| INIT | warning | `high_debt_to_equity` | FY2020 | debt/equity 3.22x, above the 2.0x threshold |
| INIT | warning | `high_net_debt_to_ebitda` | FY2020 | net debt/EBITDA 25.56x, above the 4.0x threshold |
| INIT | warning | `ebitda_margin_collapse` | FY2021 | EBITDA margin fell 9.6pp year over year |
| INIT | warning | `fcf_negative` | FY2021 | free cash flow -23.0, the 2nd consecutive negative year |
| INIT | warning | `high_debt_to_equity` | FY2021 | debt/equity 7.25x, above the 2.0x threshold |
| INIT | warning | `margin_compression` | FY2021 | net margin fell 8.6pp year over year |
| INIT | warning | `negative_working_capital` | FY2021 | working capital -23.0 |
| INIT | warning | `ebitda_margin_collapse` | FY2022 | EBITDA margin fell 7.4pp year over year |
| INIT | warning | `fcf_negative` | FY2022 | free cash flow -47.0, the 3rd consecutive negative year |
| INIT | warning | `margin_compression` | FY2022 | net margin fell 9.4pp year over year |
| INIT | warning | `margin_compression` | FY2022 | gross margin fell 6.0pp year over year |
| INIT | warning | `negative_working_capital` | FY2022 | working capital -49.0 |
| INIT | warning | `revenue_decline` | FY2022 | revenue fell 11.8% year over year |
| INIT | warning | `fcf_negative` | FY2023 | free cash flow -20.0, the 4th consecutive negative year |
| INIT | warning | `high_debt_to_equity` | FY2023 | debt/equity 2.89x, above the 2.0x threshold |
| INIT | warning | `high_net_debt_to_ebitda` | FY2023 | net debt/EBITDA 51.09x, above the 4.0x threshold |
| INIT | warning | `negative_working_capital` | FY2023 | working capital -13.0 |
| GLOB | info | `imputed_column` | -- | ebitda was blank in the source and has been derived |
| VCTR | info | `imputed_column` | -- | free_cash_flow was blank in the source and has been derived |

## Acme Corp (ACME)

Technology · FY2020–FY2024 · 5 years reported

Revenue went from $1,200M in FY2020 to $1,750M in FY2024, a CAGR of 9.9%.

Net margin rose from 6.9% to 10.8% (+3.9pp). Gross margin ended at 44.0%.

In FY2024: operating margin 15.5%, EBITDA margin 20.5%, ROE 9.1%, free cash flow $170M.

Leverage: debt/assets 13.6%, debt/equity 0.2x, interest coverage 8.5x, net debt/EBITDA 0.0x.

Liquidity: current ratio 2.47x, working capital $405M.

At 44.0%, FY2024 gross margin was the highest of 4 companies in the sample.

No flags: none of the 14 configured rules matched this company.

## Globex Industries (GLOB)

Industrials · FY2020–FY2024 · 4 years reported

Revenue went from $850M in FY2020 to $1,010M in FY2024, a CAGR of 4.4%.

Net margin rose from 3.5% to 5.0% (+1.5pp). Gross margin ended at 31.5%.

In FY2024: operating margin 9.0%, EBITDA margin 16.9%, ROE 3.9%, free cash flow $15M.

Leverage: debt/assets 18.7%, debt/equity 0.3x, interest coverage 3.5x, net debt/EBITDA 1.1x.

Liquidity: current ratio 1.60x, working capital $210M.

At 31.5%, FY2024 gross margin was the 4th highest of 4 companies in the sample.

🟠 3 flag(s) on this company:
- (warning, fcf_negative, FY2021) free cash flow -10.0, the 2nd consecutive negative year
- (warning, missing_fiscal_year, FY2023) FY2023 has no row between this company's first and last reported year
- (info, imputed_column) ebitda was blank in the source and has been derived

## Initech Systems (INIT)

Technology · FY2020–FY2024 · 5 years reported

Revenue went from $300M in FY2020 to $430M in FY2024, a CAGR of 9.4%.

Net margin rose from -15.3% to -3.6% (+11.8pp). Gross margin ended at 40.0%.

In FY2024: operating margin 2.0%, EBITDA margin 11.3%, ROE -7.9%, free cash flow $0M.

Leverage: debt/assets 41.9%, debt/equity 1.3x, interest coverage 0.4x, net debt/EBITDA 3.8x.

Liquidity: current ratio 1.14x, working capital $28M.

At 40.0%, FY2024 gross margin was the 2nd highest of 4 companies in the sample.

🔴 23 flag(s) on this company:
- (critical, low_interest_coverage, FY2020) operating income covers interest -0.84x, below the 1.0x threshold
- (critical, low_interest_coverage, FY2021) operating income covers interest -2.01x, below the 1.0x threshold
- (critical, low_interest_coverage, FY2022) operating income covers interest -2.57x, below the 1.0x threshold
- (critical, negative_equity, FY2022) shareholders' equity is -20.0; ROE and debt-to-equity are undefined this year
- (critical, low_interest_coverage, FY2023) operating income covers interest -1.17x, below the 1.0x threshold
- (critical, low_interest_coverage, FY2024) operating income covers interest 0.36x, below the 1.0x threshold
- (warning, high_debt_to_equity, FY2020) debt/equity 3.22x, above the 2.0x threshold
- (warning, high_net_debt_to_ebitda, FY2020) net debt/EBITDA 25.56x, above the 4.0x threshold
- (warning, ebitda_margin_collapse, FY2021) EBITDA margin fell 9.6pp year over year
- (warning, fcf_negative, FY2021) free cash flow -23.0, the 2nd consecutive negative year
- (warning, high_debt_to_equity, FY2021) debt/equity 7.25x, above the 2.0x threshold
- (warning, margin_compression, FY2021) net margin fell 8.6pp year over year
- (warning, negative_working_capital, FY2021) working capital -23.0
- (warning, ebitda_margin_collapse, FY2022) EBITDA margin fell 7.4pp year over year
- (warning, fcf_negative, FY2022) free cash flow -47.0, the 3rd consecutive negative year
- (warning, margin_compression, FY2022) net margin fell 9.4pp year over year
- (warning, margin_compression, FY2022) gross margin fell 6.0pp year over year
- (warning, negative_working_capital, FY2022) working capital -49.0
- (warning, revenue_decline, FY2022) revenue fell 11.8% year over year
- (warning, fcf_negative, FY2023) free cash flow -20.0, the 4th consecutive negative year
- (warning, high_debt_to_equity, FY2023) debt/equity 2.89x, above the 2.0x threshold
- (warning, high_net_debt_to_ebitda, FY2023) net debt/EBITDA 51.09x, above the 4.0x threshold
- (warning, negative_working_capital, FY2023) working capital -13.0

## Vector Retail Group (VCTR)

Consumer Staples · FY2020–FY2024 · 5 years reported

Revenue went from $2,400M in FY2020 to $2,940M in FY2024, a CAGR of 5.2%.

Net margin rose from 7.9% to 10.3% (+2.5pp). Gross margin ended at 37.5%.

In FY2024: operating margin 14.5%, EBITDA margin 18.9%, ROE 10.0%, free cash flow $320M.

Leverage: debt/assets 15.2%, debt/equity 0.2x, interest coverage 8.4x, net debt/EBITDA 0.2x.

Liquidity: current ratio 2.19x, working capital $612M.

At 37.5%, FY2024 gross margin was the 3rd highest of 4 companies in the sample.

🔵 1 flag(s) on this company:
- (info, imputed_column) free_cash_flow was blank in the source and has been derived

---

Generated by Stage 1 of the financial analyst. Deterministic and reproducible:
re-running on the same input produces this file byte for byte.
Stage 2 will consume `outputs/stage1_metrics.json` and `outputs/stage1_flags.csv`.
