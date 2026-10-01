"""End-to-end tests for Stage 1.

These run the whole pipeline on the real sample file and check the artifacts
that Stage 2 will consume. The golden-file comparisons are the regression net:
any accidental change to a formula, a threshold, a column order or a sentence
shows up here as a diff rather than as a subtly different number three stages
down.

The golden files are reviewed by hand once, then frozen. Regenerate them only
when the change is intended, and read the diff before committing it.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from financial_analyst.stages import stage1_static_csv as stage1

FIXTURES = "tests/fixtures"

#: The columns a Stage 2 agent is most likely to rely on. Comparing all 65 is
#: noise; comparing none is no regression test at all.
GOLDEN_METRIC_COLUMNS = [
    "ticker", "fiscal_year", "revenue", "revenue_growth", "revenue_cagr",
    "gross_margin", "net_margin", "ebitda_margin", "return_on_assets",
    "return_on_equity", "debt_to_assets", "debt_to_equity", "interest_coverage",
    "net_debt_to_ebitda", "current_ratio", "working_capital",
    "earnings_per_share", "price_to_earnings",
]


@pytest.fixture(scope="module")
def result(sample_csv):
    return stage1.run_stage1(sample_csv, write=False)


def read_fixture(name) -> pd.DataFrame:
    return pd.read_csv(f"{FIXTURES}/{name}")


# ---------------------------------------------------------------------------
# Running the pipeline
# ---------------------------------------------------------------------------
def test_stage1_produces_the_expected_shape(result, dataset):
    assert len(result.metrics) == len(dataset.frame)
    assert len(result.flags) > 0
    assert result.report.startswith("# Stage 1")
    assert result.written == {}


def test_no_write_leaves_the_repository_untouched(result):
    assert result.written == {}
    assert result.metrics is not None


def test_writes_all_four_artifacts(tmp_path, sample_csv):
    written = stage1.run_stage1(sample_csv, write=True, output_dir=tmp_path).written
    assert set(written) == {"metrics_csv", "metrics_json", "report_md", "flags_csv"}
    for path in written.values():
        assert path.exists(), path
        assert path.stat().st_size > 0


def test_default_paths_are_the_configured_ones(tmp_path):
    paths = stage1._artifact_paths(None)
    assert paths["metrics_csv"].name == "company_metrics.csv"
    assert paths["report_md"].name == "stage1_report.md"
    overridden = stage1._artifact_paths(tmp_path)
    assert all(path.parent == tmp_path for path in overridden.values())


def test_cli_entry_point_runs(capsys, sample_csv):
    exit_code = stage1.main(["--input", str(sample_csv), "--no-write"])
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "companies" in output
    assert "flags" in output


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------
def test_two_runs_produce_identical_artifacts(tmp_path, sample_csv):
    first = tmp_path / "first"
    second = tmp_path / "second"
    for destination in (first, second):
        stage1.run_stage1(sample_csv, write=True, output_dir=destination)
    for name in ("company_metrics.csv", "stage1_metrics.json",
                 "stage1_report.md", "stage1_flags.csv"):
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


def test_output_is_independent_of_input_row_order(tmp_path, sample_csv):
    source = pd.read_csv(sample_csv)
    shuffled_path = tmp_path / "shuffled.csv"
    source.sample(frac=1.0, random_state=3).to_csv(shuffled_path, index=False)

    ordered = stage1.run_stage1(sample_csv, write=False)
    shuffled = stage1.run_stage1(shuffled_path, write=False)
    pd.testing.assert_frame_equal(ordered.metrics, shuffled.metrics)
    # Only the source name differs between the two renders.
    assert ordered.report == shuffled.report.replace(
        "shuffled.csv", "company_financials.csv"
    )


# ---------------------------------------------------------------------------
# JSON output: the artifact a Stage 2 agent reads
# ---------------------------------------------------------------------------
def test_json_is_strictly_valid_with_nulls_not_nan(result):
    payload = stage1.metrics_to_nested_json(result.metrics)
    text = json.dumps(payload, allow_nan=False)  # raises if any NaN survived
    assert "NaN" not in text
    assert "Infinity" not in text


def test_json_is_keyed_by_ticker_then_year(result):
    payload = stage1.metrics_to_nested_json(result.metrics)
    assert set(payload) == {"ACME", "GLOB", "INIT", "VCTR"}
    assert sorted(payload["GLOB"]) == ["2020", "2021", "2022", "2024"]
    row = payload["ACME"]["2024"]
    assert "ticker" not in row and "fiscal_year" not in row
    assert row["revenue"] == pytest.approx(1750.0)


def test_json_uses_null_for_undefined_metrics(result):
    payload = stage1.metrics_to_nested_json(result.metrics)
    # INIT FY2022 has negative equity, so ROE is undefined -> null.
    assert payload["INIT"]["2022"]["return_on_equity"] is None
    assert payload["INIT"]["2022"]["price_to_earnings"] is None


def test_json_round_trips_through_disk(tmp_path, sample_csv):
    written = stage1.run_stage1(sample_csv, write=True, output_dir=tmp_path).written
    loaded = json.loads(written["metrics_json"].read_text())
    assert set(loaded) == {"ACME", "GLOB", "INIT", "VCTR"}


def test_json_safe_covers_numpy_and_missing_values():
    import numpy as np

    assert stage1._json_safe(np.float64(1.5)) == 1.5
    assert stage1._json_safe(np.int64(3)) == 3
    assert stage1._json_safe(float("nan")) is None
    assert stage1._json_safe(float("inf")) is None
    assert stage1._json_safe(None) is None
    assert stage1._json_safe("text") == "text"
    assert stage1._json_safe(True) is True


# ---------------------------------------------------------------------------
# Golden files
# ---------------------------------------------------------------------------
def test_metrics_match_the_reviewed_golden_file(result):
    golden = read_fixture("expected_metrics.csv")
    actual = result.metrics[GOLDEN_METRIC_COLUMNS].copy()
    pd.testing.assert_frame_equal(
        actual.reset_index(drop=True),
        golden[GOLDEN_METRIC_COLUMNS].reset_index(drop=True),
        check_dtype=False, rtol=1e-9,
    )


def test_flags_match_the_reviewed_golden_file(result):
    from financial_analyst.tools import anomalies as an

    golden = read_fixture("expected_flags.csv")
    actual = an.flags_to_frame(result.flags)
    pd.testing.assert_frame_equal(
        actual.reset_index(drop=True), golden.reset_index(drop=True), check_dtype=False
    )


def test_report_matches_the_reviewed_golden_file(result):
    assert result.report == open(f"{FIXTURES}/expected_summary.md").read()

