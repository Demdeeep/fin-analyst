# fin-analyst

A multi-agent financial analyst, built one stage at a time.

**Stage 1 is complete**: a deterministic analysis of a static CSV of company
financials — ratio maths, anomaly detection, and a written report. No language
model is involved. Running it twice on the same input produces byte-identical
output, which is what lets the test suite use golden files as a regression net.

**Stages 2 and 4 are also built.** Stage 2 runs an analyst, a critic and a
bounded orchestrator over Stage 1's artifacts, with every figure in the prose
verified mechanically and every anomaly flag screened for coverage. Stage 4
compares companies against their peers, computing medians, gaps and
direction-aware ranks in code rather than in the model.

Stage 3 (retrieval over filings) is the remaining planned stage. See
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
.venv/bin/python -m financial_analyst.stages.stage2_agents
.venv/bin/python -m financial_analyst.stages.stage4_peer_comparison
```

Stages 2 and 4 default to the scripted stub provider, so they need no API key.
Pass `--provider openai` to use a real model.

### Live model access

Two prerequisites, and the error message names whichever is missing:

1. **The SDK.** Optional by design — Stage 1 and the whole test suite run without it.

   ```bash
   uv pip install -e ".[openai]"
   ```

2. **A key.** `OPENAI_API_KEY`, read from the environment. Copy `.env.example`
   to `.env` and fill it in, then:

   ```bash
   .venv/bin/python -m financial_analyst.stages.stage2_agents \
       --ticker ACME --no-write --provider openai --dotenv .env
   ```

   A key already exported in your shell wins over the file, so a real
   credential in `.env` is never silently displaced by a stale one.

Both stage CLIs print `provider: <what will actually run>` to stderr before any
call is made. Worth reading: `--provider auto` with no key configured silently
produces stub output, and a finished-looking report written by a placeholder is
the worst outcome of a mistyped flag.

`.env` is gitignored; `.env.example` is tracked so the variable name is
discoverable. The key is never logged, echoed into a prompt, or written to an
artifact.

## Tests

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest --cov=financial_analyst --cov-report=term-missing
```

509 tests, 97% coverage. The suite is layered deliberately:

| Layer | File | What it proves |
|---|---|---|
| Hand-computed values | `test_calculations.py` | Each function matches a formula worked out on paper — never recomputed by the same code |
| Accounting identities | `test_identities.py` | ROE = margin × turnover × multiplier; assets = liabilities + equity. Catches a *consistently wrong* definition |
| Guard behaviour | `test_calculations.py`, `test_formatting.py` | Zero and negative denominators return `None` / `"n/a"`, never an exception |
| Validation | `test_loaders.py` | Missing columns, duplicates and mixed currency are fatal; identity breaks are warnings |
| Rule coverage | `test_anomalies.py` | Every rule both fires and stays silent; a new rule without a test fails the suite |
| Property-based | `test_properties.py` | Arbitrary floats: every metric returns a finite float or `None` |
| Real filings | `test_sanity_real_filings.py` | Definitions reproduce three real 10-Ks — Apple, Beyond Meat and Target — including filed EPS on the positive and negative side |
| Golden files | `test_stage1_pipeline.py`, `test_summary.py` | Output is byte-identical to a reviewed snapshot |
| Peer refusals | `test_peer_comparison.py` | Groups under 3 companies are declined with a reason; undefined metrics and peers are never read as zero; ranking is direction-aware |

Regenerate the golden files **only when the change is intended**, and read the
diff:

```bash
.venv/bin/python -m pytest tests/ -q   # confirms the current mismatch
```

## Known gap

**One definitional caveat: leases are excluded from debt.** For Target that
understates leverage by about **10 percentage points** (our 27.8% vs 37.8%
including all lease liabilities). Operating leases are arguably not debt; finance
leases (~$2.1bn, 3.4pp) are, and rating agencies add them. This is documented,
quantified in `docs/stage1.md`, and deliberately does not raise a flag — a
definitional choice is not a data error.

**Still missing: the bank case.** For a bank, interest is *revenue*, so
`operating_income` and `net_margin` are not meaningful as defined. The tool would
compute correct numbers describing nothing real. Fixing that means giving it
the ability to refuse and say the definitions do not apply — a new capability,
not a correction.

Verified against three real 10-Ks (Apple, Beyond Meat, Target) with all four
accounting identities reconciling and filed EPS matching on every one. That
caught four real defects, and it is still nowhere near enough to claim the
schema is general.

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
