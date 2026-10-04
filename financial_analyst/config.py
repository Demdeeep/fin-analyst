"""Paths, tolerances and thresholds used across the pipeline.

Deliberately separate from :mod:`financial_analyst.tools.calculations` so the
pure math module stays dependency-free, and separate from the anomaly rules so
a Stage 2 agent can be told exactly which thresholds were applied.
"""

from __future__ import annotations

from pathlib import Path

from .tools.calculations import DAYS_PER_YEAR

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
OUTPUT_DIR = PROJECT_ROOT / "outputs"

DEFAULT_INPUT_CSV = RAW_DIR / "company_financials.csv"
PROCESSED_METRICS_CSV = PROCESSED_DIR / "company_metrics.csv"
OUTPUT_METRICS_JSON = OUTPUT_DIR / "stage1_metrics.json"
OUTPUT_REPORT_MD = OUTPUT_DIR / "stage1_report.md"
OUTPUT_FLAGS_CSV = OUTPUT_DIR / "stage1_flags.csv"

# --------------------------------------------------------------------------
# Validation tolerances (all monetary figures are USD millions)
# --------------------------------------------------------------------------
BALANCE_TOLERANCE = 0.01
INCOME_STATEMENT_TOLERANCE = 0.01
CURRENCY_REQUIRED = "USD"

# --------------------------------------------------------------------------
# Anomaly thresholds -- named so they show up in report output and diffs
# --------------------------------------------------------------------------
DEBT_TO_EQUITY_THRESHOLD = 2.0
INTEREST_COVERAGE_THRESHOLD = 1.0
NET_DEBT_TO_EBITDA_THRESHOLD = 4.0
# Percentage points of year-over-year margin decline that counts as compression.
MARGIN_COMPRESSION_THRESHOLD_PP = 5.0

SEVERITIES = ("info", "warning", "critical")

# ---------------------------------------------------------------------------
# Flag-coverage policy (Stage 2 critic)
# ---------------------------------------------------------------------------
#: Ignoring a flag at these severities is a defect, not a judgement call. An
#: analysis that omits "operating income covers interest 0.36x, below the
#: threshold" while presenting a confident summary is actively misleading --
#: arguably worse than a hallucinated figure, because a wrong figure at least
#: looks wrong on inspection.
MUST_ADDRESS_SEVERITIES = ("critical",)

#: Ignoring a flag at these severities is usually correct. A company with 17
#: warning flags and five sentences of prose cannot address all of them, and a
#: check that fires on every analysis is a check whose reader learns to ignore
#: it. So these are recorded and reported, but they do not move the verdict.
ADVISORY_SEVERITIES = ("warning",)

#: Never required to be addressed. Informational provenance notes.
OPTIONAL_SEVERITIES = ("info",)

__all__ = [
    "PROJECT_ROOT",
    "DATA_DIR",
    "RAW_DIR",
    "PROCESSED_DIR",
    "OUTPUT_DIR",
    "DEFAULT_INPUT_CSV",
    "PROCESSED_METRICS_CSV",
    "OUTPUT_METRICS_JSON",
    "OUTPUT_REPORT_MD",
    "OUTPUT_FLAGS_CSV",
    "BALANCE_TOLERANCE",
    "INCOME_STATEMENT_TOLERANCE",
    "CURRENCY_REQUIRED",
    "DEBT_TO_EQUITY_THRESHOLD",
    "INTEREST_COVERAGE_THRESHOLD",
    "NET_DEBT_TO_EBITDA_THRESHOLD",
    "MARGIN_COMPRESSION_THRESHOLD_PP",
    "DAYS_PER_YEAR",
    "SEVERITIES",
    "MUST_ADDRESS_SEVERITIES",
    "ADVISORY_SEVERITIES",
    "OPTIONAL_SEVERITIES",
]
