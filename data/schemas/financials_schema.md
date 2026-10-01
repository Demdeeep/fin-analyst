# Financials CSV schema — `data/raw/company_financials.csv`

Grain: **one row per `(ticker, fiscal_year)`**. Long/tidy format, not wide, so a
new year is an append rather than a new column and a missing year is an *absent
row* (a signal) rather than a null column (silence).

## Conventions (these are the things that silently break analytics)

1. **Currency**: every monetary figure is **USD millions**. The `currency`
   column must read `USD` on every row; mixed currencies are rejected rather
   than converted, because an implicit FX rate is an invisible assumption.
2. **Sign convention**:
   - *Positive magnitudes* (never negative): `cogs`, `operating_expenses`,
     `interest_expense`, `tax_expense`, `capex`, `dividends_paid`, and all
     balance-sheet stock figures.
   - *Signed* (may be negative): `revenue`, `gross_profit`, `operating_income`,
     `pretax_income`, `net_income`, `ebitda`, `cash_from_operations`,
     `free_cash_flow`, `total_equity`, and the derived ratios.
   - A negative `net_income`, `ebitda` or `free_cash_flow` means a loss / cash
     burn, not a data error.
3. **`capex` is a positive magnitude**, so free cash flow is `CFO - capex`.
4. **`short_term_debt` includes the current portion of long-term debt.**
   `total_debt = short_term_debt + long_term_debt`; leases and trade payables are
   deliberately excluded from that definition.
5. **Derived columns are stored *as reported* and are also recomputable.** The
   loader cross-checks stored vs. recomputed within a tolerance and raises a
   warning rather than an exception — a mismatch is information an analyst (or a
   later agent) should see, not a reason to abort the run.
6. `ebitda` and `free_cash_flow` are **optional**. When absent the loader
   derives them (`operating_income + depreciation_amortization`,
   `cash_from_operations - capex`) and records that it did. The sample data
   exercises both paths: GLOB has no `ebitda`, VCTR has no `free_cash_flow`.

## Columns

`✅` required · `⭕` optional

### Identity

| Column | Type | | Notes |
|---|---|---|---|
| `ticker` | str | ✅ | key |
| `company_name` | str | ✅ | |
| `sector` | str | ⭕ | peer comparison in later stages |
| `fiscal_year` | int | ✅ | key, 4-digit |
| `period_end` | date ISO | ⭕ | `YYYY-MM-DD` |
| `currency` | str | ✅ | must be `USD` |
| `source` | str | ⭕ | provenance |

### Income statement

| Column | Type | | Notes |
|---|---|---|---|
| `revenue` | float | ✅ | signed |
| `cogs` | float | ✅ | positive magnitude |
| `gross_profit` | float | ✅ | = `revenue - cogs` |
| `operating_expenses` | float | ✅ | positive magnitude |
| `operating_income` | float | ✅ | EBIT, signed |
| `interest_expense` | float | ✅ | positive magnitude |
| `pretax_income` | float | ✅ | = EBIT − interest |
| `tax_expense` | float | ✅ | positive magnitude |
| `net_income` | float | ✅ | signed |
| `ebitda` | float | ⭕ | derive: EBIT + D&A |
| `depreciation_amortization` | float | ⭕ | |
| `diluted_shares` | float | ✅ | millions → EPS in USD/share |

### Balance sheet

| Column | Type | | Notes |
|---|---|---|---|
| `cash_and_equivalents` | float | ✅ | |
| `accounts_receivable` | float | ⭕ | enables DSO / receivables turnover |
| `inventory` | float | ✅ | enables quick ratio, DIO |
| `total_current_assets` | float | ✅ | |
| `total_assets` | float | ✅ | may include intangibles not broken out |
| `accounts_payable` | float | ⭕ | enables DPO |
| `short_term_debt` | float | ✅ | incl. current portion of LTD |
| `total_current_liabilities` | float | ✅ | |
| `long_term_debt` | float | ✅ | |
| `total_liabilities` | float | ✅ | = current + long-term |
| `total_equity` | float | ✅ | **may be negative** |

### Cash flow

| Column | Type | | Notes |
|---|---|---|---|
| `cash_from_operations` | float | ✅ | signed (negative = cash burn) |
| `capex` | float | ✅ | positive magnitude |
| `dividends_paid` | float | ⭕ | positive magnitude |
| `free_cash_flow` | float | ⭕ | derive: CFO − capex |

### Market (needed only for valuation metrics)

| Column | Type | | Notes |
|---|---|---|---|
| `share_price` | float | ⭕ | period-end close |
| `shares_outstanding` | float | ⭕ | millions |

## Accounting identities enforced by the test suite

- `total_assets == total_liabilities + total_equity`
- `gross_profit == revenue - cogs`
- `operating_income == gross_profit - operating_expenses`
- `pretax_income == operating_income - interest_expense`
- `net_income == pretax_income - tax_expense`
- `free_cash_flow == cash_from_operations - capex`
