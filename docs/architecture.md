# Architecture

A multi-agent financial analyst, built one stage at a time. Each stage consumes
the previous stage's artifacts and never reaches back into its internals.

## Stage 1 (built) — deterministic analysis of a static CSV

```
data/raw/company_financials.csv
        │
        ▼  tools/loaders.py        read, validate, impute optional columns
        │                         (errors vs. warnings -- see below)
        ▼  tools/metrics.py        apply tools/calculations.py per company-year
        │                         tools/registry.py describes the result
        ▼  tools/anomalies.py      14 fixed rules, thresholds from config.py
        ▼  reporting/summary.py    templates + strict interpolation
        │
        ├── data/processed/company_metrics.csv    one row per company-year
        ├── outputs/stage1_metrics.json           {ticker: {year: metrics}}
        ├── outputs/stage1_flags.csv              one row per flag
        └── outputs/stage1_report.md              the written narrative
```

**No LLM is involved.** Stage 1 is reproducible byte-for-byte, which is what
makes the golden-file regression tests meaningful and gives Stage 2 a stable
contract to trust.

### The layering rule

`tools/calculations.py` is a **leaf**: it imports nothing from the package and
nothing from pandas. Everything that touches files or dataframes lives in a
sibling. This buys three things at once — unit tests need no fixtures, the ratio
math cannot be corrupted by dataframe plumbing, and each function is a clean
candidate "tool" for a future agent.

### Errors versus warnings

The loader makes one judgement call and holds to it:

- **Fatal** (`FinancialsValidationError`): the file cannot be trusted — missing
  column, non-numeric cell, duplicate company-year, non-USD currency. Continuing
  would produce confidently wrong numbers.
- **Warning** (returned, then flagged): the file is usable but suspect — an
  identity that does not balance, an optional column that had to be derived.

Once agents are in the loop, an exception kills a run while a flag is
information an agent can reason about. That asymmetry drives the split.

## Stage 2 (next) — LLM agents over Stage 1's artifacts

Reserved, not yet built. The intended shape:

```
outputs/stage1_metrics.json ──► tools/registry.py ──► analyst agent
outputs/stage1_flags.csv    ──►                        │
                                                     ▼
                                              critic agent
```

`tools/registry.py` already exists so that Stage 2 gets a self-describing tool
catalogue for free: name, required columns, and whether up is good — so no agent
has to guess whether a lower `debt_to_assets` is an improvement.

Constraints Stage 2 inherits from Stage 1:

- **Stage 1's output must stay a stable contract.** Changing a metric's meaning
  is a breaking change for the agents downstream.
- **`None` means undefined, and must be passed through as such.** An agent told
  "n/a" can ask a follow-up question; an agent handed `nan` will analyse it.
- **Judgement belongs here, not there.** Stage 1 deliberately states values and
  flags without opinions (`tests/test_summary.py` enforces a banned-word list),
  precisely so it cannot bias the agents that read it.

## Later stages

- **Stage 3** — retrieval over filings for the figures a CSV lacks (segment
  detail, footnotes, management discussion).
- **Stage 4** — cross-company comparison and peer-relative metrics, which needs
  the `sector` column the sample data already carries.
