"""Stage 1: load a static CSV of company financials and report on it.

The pipeline is four steps with no hidden state between them::

    load_financials -> compute_metric_frame -> run_anomalies -> render_report

and four artifacts come out: a metrics table (CSV + JSON) and two human-facing
outputs (a markdown narrative and a flags CSV).

Runnable as a module::

    python -m financial_analyst.stages.stage1_static_csv
    python -m financial_analyst.stages.stage1_static_csv --input path.csv --no-write

``run_stage1`` is deterministic: the same input file produces byte-identical
outputs, which is what makes the golden-file regression tests meaningful.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

from .. import config
from ..reporting.summary import render_report
from ..tools import anomalies as an
from ..tools.loaders import FinancialsDataset, load_financials
from ..tools.metrics import compute_metric_frame


@dataclass(frozen=True)
class Stage1Result:
    """Everything Stage 1 produced, for callers and for the tests."""

    dataset: FinancialsDataset
    metrics: pd.DataFrame
    flags: list[an.Flag]
    report: str
    written: dict[str, Path]


def _json_safe(value: object) -> object:
    """One cell -> something ``json.dump`` will accept.

    ``NaN``/``inf``/``NaT`` become ``None`` and numpy scalars become Python
    ones. Using ``df.where(cond, None)`` instead looks tidier but silently
    leaves NaN in place on float columns, which then trips ``allow_nan=False``
    -- or worse, gets written as bare ``nan`` into a file an agent will read.
    """
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        return None if (math.isnan(value) or math.isinf(value)) else value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(number) or math.isinf(number)) else number


def metrics_to_nested_json(metrics: pd.DataFrame) -> dict:
    """``{ticker: {fiscal_year: {metric: value}}}`` with strict JSON values.

    Missing metrics become ``null``, never ``NaN``: ``NaN`` is not valid JSON and
    a downstream agent reading ``nan`` would treat it as a measurement.
    """
    nested: dict[str, dict] = {}
    for raw in metrics.to_dict("records"):
        row = {key: _json_safe(value) for key, value in raw.items()}
        year = str(int(row.pop("fiscal_year")))
        ticker = str(row.pop("ticker"))
        nested.setdefault(ticker, {})[year] = row
    return nested


def run_stage1(
    input_csv: Optional[Path] = None,
    *,
    write: bool = True,
    output_dir: Optional[Path] = None,
) -> Stage1Result:
    """Run the whole of Stage 1 over one CSV.

    Set ``write=False`` to compute everything without touching the filesystem --
    that is the mode the pipeline tests use, so they cannot pass by accident on
    stale output files.
    """
    source = Path(input_csv) if input_csv else config.DEFAULT_INPUT_CSV
    dataset = load_financials(source)
    metrics = compute_metric_frame(dataset.frame)

    context = an.AnalysisContext(
        metrics=metrics,
        source=dataset.frame,
        raw=dataset.raw_frame,
        imputed_columns=dataset.imputed_columns,
        loader_warnings=dataset.warnings,
    )
    flags = an.run_anomalies(context)
    report = render_report(metrics, flags, source_name=source.name)

    written: dict[str, Path] = {}
    if write:
        written = _write_artifacts(metrics, flags, report, _artifact_paths(output_dir))

    return Stage1Result(
        dataset=dataset, metrics=metrics, flags=flags, report=report, written=written
    )


def _artifact_paths(output_dir: Optional[Path]) -> dict[str, Path]:
    """Where each artifact goes.

    With no override the four files sit where ``config`` says they should --
    the metrics table with the data, the human-facing outputs together. With an
    override (the tests use a tmp dir) everything lands in one place.
    """
    if output_dir is not None:
        base = Path(output_dir)
        return {
            "metrics_csv": base / config.PROCESSED_METRICS_CSV.name,
            "metrics_json": base / config.OUTPUT_METRICS_JSON.name,
            "report_md": base / config.OUTPUT_REPORT_MD.name,
            "flags_csv": base / config.OUTPUT_FLAGS_CSV.name,
        }
    return {
        "metrics_csv": config.PROCESSED_METRICS_CSV,
        "metrics_json": config.OUTPUT_METRICS_JSON,
        "report_md": config.OUTPUT_REPORT_MD,
        "flags_csv": config.OUTPUT_FLAGS_CSV,
    }


def _write_artifacts(
    metrics: pd.DataFrame, flags: list[an.Flag], report: str, paths: dict[str, Path]
) -> dict[str, Path]:
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)

    metrics.to_csv(paths["metrics_csv"], index=False)
    with paths["metrics_json"].open("w") as handle:
        json.dump(metrics_to_nested_json(metrics), handle, indent=2, allow_nan=False)
        handle.write("\n")
    paths["report_md"].write_text(report)
    an.flags_to_frame(flags).to_csv(paths["flags_csv"], index=False)
    return paths


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Stage 1 financial analysis")
    parser.add_argument(
        "--input", type=Path, default=config.DEFAULT_INPUT_CSV,
        help="path to the financials CSV",
    )
    parser.add_argument(
        "--no-write", action="store_true",
        help="compute and print a summary without writing any files",
    )
    arguments = parser.parse_args(argv)

    result = run_stage1(arguments.input, write=not arguments.no_write)

    print(f"companies : {len(result.dataset.tickers)} {result.dataset.tickers}")
    print(f"rows      : {len(result.metrics)}")
    print(f"flags     : {len(result.flags)}")
    for flag in result.flags:
        year = f"FY{flag.fiscal_year}" if flag.fiscal_year else "-----"
        print(f"  [{flag.severity:8}] {flag.ticker:5} {year:6} {flag.rule}")
    if result.dataset.imputed_columns:
        print(f"derived   : {', '.join(result.dataset.imputed_columns)}")
    for name, path in result.written.items():
        print(f"wrote {name:12} -> {path.relative_to(config.PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
