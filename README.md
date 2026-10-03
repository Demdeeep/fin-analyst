# fin-analyst

A multi-agent financial analyst, built one stage at a time.

**Stage 1 is complete**: a deterministic analysis of a static CSV of company
financials — ratio maths, anomaly detection, and a written report. No language
model is involved. Running it twice on the same input produces byte-identical
output, which is what lets the test suite use golden files as a regression net.

Stages 2+ (LLM agents) are reserved but not built. See
[`docs/architecture.md`](docs/architecture.md).

## Quick start

```bash
uv venv --python 3.11
uv pip install -e ".[dev]"

.venv/bin/python -m financial_analyst.stages.stage1_static_csv
```

That reads `data/raw/company_financials.csv` and writes:

| Artifact | Contents |
|---|---|
| `data/processed/company_metrics.csv` | one row per company-year, all ratios |
| `outputs/stage1_metrics.json` | `{ticker: {year: {metric: value}}}` — what Stage 2 reads |
| `outputs/stage1_flags.csv` | one row per anomaly |
| `outputs/stage1_report.md` | the written narrative |

Other entry points:

```bash
.venv/bin/python -m financial_analyst.stages.stage1_static_csv --input other.csv
.venv/bin/python -m financial_analyst.stages.stage1_static_csv --no-write
```

## Tests

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest --cov=financial_analyst --cov-report=term-missing
```

390 tests, 97% coverage. The suite is layered deliberately:

| Layer | File | What it proves |
|---|---|---|
| Hand-computed values | `test_calculations.py` | Each function matches a formula worked out on paper — never recomputed by the same code |
| Accounting identities | `test_identities.py` | ROE = margin × turnover × multiplier; assets = liabilities + equity. Catches a *consistently wrong* definition |
| Guard behaviour | `test_calculations.py`, `test_formatting.py` | Zero and negative denominators return `None` / `"n/a"`, never an exception |
| Validation | `test_loaders.py` | Missing columns, duplicates and mixed currency are fatal; identity breaks are warnings |
| Rule coverage | `test_anomalies.py` | Every rule both fires and stays silent; a new rule without a test fails the suite |
| Property-based | `test_properties.py` | Arbitrary floats: every metric returns a finite float or `None` |
| Real filings | `test_sanity_real_filings.py` | Definitions reproduce Apple's *and* Beyond Meat's 10-K figures, including filed EPS on both the positive and negative side |
| Golden files | `test_stage1_pipeline.py`, `test_summary.py` | Output is byte-identical to a reviewed snapshot |

Regenerate the golden files **only when the change is intended**, and read the
diff:

```bash
.venv/bin/python -m pytest tests/ -q   # confirms the current mismatch
```

## Known gaps

**All identity gaps found by the sanity checks are closed.** Verified against
two real 10-Ks — Apple's and Beyond Meat's — with all four accounting
identities reconciling on both, and computed EPS matching the filed EPS to the
cent in each case. The checks are documented in `docs/stage1.md`, including the
three wrong definitions those filings exposed and how each was fixed.

What is *not* yet covered is a third industry shape. A lease-heavy retailer
would expose that our debt definition excludes operating leases, and a bank
raises a design question rather than a bug: for a bank, interest is revenue, so
`operating_income` and `net_margin` are not meaningful as defined — the tool
would need to refuse rather than emit confident nonsense.

Sanity coverage is still only two companies. That is enough to have caught three
real defects, and nowhere near enough to claim the schema is general.

## Layout

```
financial_analyst/
├── config.py               paths, tolerances, thresholds
├── tools/
│   ├── calculations.py     pure ratio maths -- imports nothing, not even pandas
│   ├── loaders.py          CSV → validated dataframe
│   ├── metrics.py          applies calculations per company-year
│   ├── anomalies.py        14 fixed rules
│   └── registry.py         self-describing metric catalogue
├── reporting/
│   ├── templates.py        the prose, as data
│   └── summary.py          deterministic renderer
└── stages/
    └── stage1_static_csv.py   orchestration + CLI
```

## Two conventions worth knowing

**Ratios are decimal fractions in the data** (`0.153` = 15.3%) and become
percentages only in the report.

**An undefined ratio is `None`, never `NaN` and never a number.** Return on
*negative* equity is not a return; `272 / -90` is not low leverage. Those
computations are declined and render as `n/a`, because `nan%` reads like a
measurement and would mislead every agent that reads it. The full list of guards
is in [`docs/stage1.md`](docs/stage1.md).

## Known gap

The definitions have been checked for internal consistency and against
hand-worked arithmetic, but **not yet against a real filing**. There is an empty
sanity-check table at the end of `docs/stage1.md` for that. It needs a human
with a 10-K, and it is the only thing between "the code agrees with itself" and
"the code is right".
