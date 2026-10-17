"""Parser cases based on the labeled rows in Apple's 2025 Form 10-K."""

from financial_analyst.documents.financial_tables import (
    _extract_text_rows,
    _unit,
    make_stage1_csv_rows,
)


APPLE_INCOME_PAGE = """Apple Inc.
CONSOLIDATED STATEMENTS OF OPERATIONS
(In millions, except number of shares, which are reflected in thousands, and per-share amounts)
Years ended
September 27,
2025
September 28,
2024
September 30,
2023
Total net sales 416,161 391,035 383,285
Total cost of sales 220,960 210,352 214,137
Gross margin 195,201 180,683 169,148
Research and development 34,550 31,370 29,915
Selling, general and administrative 27,601 26,097 24,932
Operating income 133,050 123,216 114,301
Income before provision for income taxes 132,729 123,485 113,736
Provision for income taxes 20,719 29,749 16,741
Net income 112,010 93,736 96,995
"""


def test_apple_millions_heading_wins_over_share_thousands_qualifier():
    unit, scale = _unit(APPLE_INCOME_PAGE, ())

    assert unit == "USD millions as reported"
    assert scale == 1.0


def test_apple_text_rows_support_split_year_headers_and_stage1_values():
    unit, scale = _unit(APPLE_INCOME_PAGE, ())
    _, values = _extract_text_rows(
        APPLE_INCOME_PAGE,
        page_number=31,
        unit=unit,
        unit_scale=scale,
    )

    by_key = {(value.metric, value.fiscal_year): value.usd_millions for value in values}
    assert by_key[("revenue", 2025)] == 416_161
    assert by_key[("cost_of_revenue", 2024)] == 210_352
    assert by_key[("gross_profit", 2023)] == 169_148
    assert by_key[("income_before_tax", 2025)] == 132_729
    assert by_key[("income_tax_expense", 2024)] == 29_749
    assert by_key[("net_income", 2023)] == 96_995

    rows, notes = make_stage1_csv_rows(
        values, ticker="AAPL", company_name="Apple Inc."
    )
    fy2025 = next(row for row in rows if row["fiscal_year"] == 2025)
    assert fy2025["revenue"] == 416_161
    assert fy2025["operating_expenses"] == 62_151
    assert fy2025["source"].endswith("page(s) 31; verify against filing")
    assert any("Required Stage 1 fields left blank" in note for note in notes)


def test_text_amounts_allow_dollar_signs_with_separating_spaces():
    text = """CONSOLIDATED BALANCE SHEETS
(In millions)
2025
2024
Cash and cash equivalents $ 35,934 $ 29,943
"""
    unit, scale = _unit(text, ())
    _, values = _extract_text_rows(text, page_number=33, unit=unit, unit_scale=scale)

    assert [(value.fiscal_year, value.usd_millions) for value in values] == [
        (2025, 35_934), (2024, 29_943),
    ]


def test_apple_balance_sheet_debt_components_keep_their_current_classification():
    text = """CONSOLIDATED BALANCE SHEETS
(In millions)
2025
2024
Current liabilities:
Commercial paper 7,979 9,967
Term debt 12,350 10,912
Non-current liabilities:
Term debt 78,328 85,750
"""
    unit, scale = _unit(text, ())
    _, values = _extract_text_rows(text, page_number=33, unit=unit, unit_scale=scale)
    rows, _ = make_stage1_csv_rows(values, ticker="AAPL", company_name="Apple Inc.")

    fy2025 = next(row for row in rows if row["fiscal_year"] == 2025)
    assert fy2025["short_term_debt"] == 20_329
    assert fy2025["long_term_debt"] == 78_328
