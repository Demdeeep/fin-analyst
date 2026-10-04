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

## Stage 2 (built) — LLM agents over Stage 1's artifacts

```
outputs/stage1_metrics.json ─┐
outputs/stage1_flags.csv    ─┴─► analyst ─► critic ─► outputs/stage2_analysis.{json,md}
```

Stage 1 states values without judging them; Stage 2 draws the conclusions, which
is the whole reason its output needs a different kind of checking. Verifying
Stage 1 means reading the code. Verifying prose means either constraining what
the model may say, or checking what it did say. Stage 2 does both.

```bash
python -m financial_analyst.stages.stage2_agents                    # stub, no key
python -m financial_analyst.stages.stage2_agents --provider openai   # live model
python -m financial_analyst.stages.stage2_agents --no-critic-model   # deterministic
```

### The provider seam

Everything depends on one method, `LLMClient.complete(system, user)`. Streaming,
native tool calling and multi-turn message arrays are deliberately excluded: they
are transport concerns, and letting them into the agent loop would tie the agents
to one vendor's protocol.

Two implementations ship: a deterministic `ScriptedLLM` that records every prompt
(prompts are where agent behaviour goes wrong, and they are otherwise
invisible), and an OpenAI adapter whose SDK is imported *inside* `complete()`, so
the package imports and the suite runs with no SDK and no key. `provider="auto"`
degrades to the stub rather than raising — a missing key should not produce a
pipeline that looks broken for the wrong reason.

### An agent may only cite, never recompute

`agents/tools.py` reads `stage1_metrics.json` and returns stored values. The
model *requests* numbers by name and the tool layer supplies them, so there is no
arithmetic path from the model into the output. That is a code guarantee, not a
request in a prompt.

Two details that are easy to get wrong, and both were wrong first:

- The valid set is Stage 1's **output columns**, not the function catalogue in
  `tools/registry.py`. `cagr` is the function behind the `revenue_cagr` column;
  testing against the catalogue reported valid citations as hallucinations.
- Rendering defaults to *percentage*, because almost every metric column is a
  decimal fraction. Anything else silently prints `0.1358` where a reader
  expects `13.6%`.

### The analyst

A two-turn loop, the minimum that is genuinely agentic while staying testable:

1. Shown the company, Stage 1's flags and the metric catalogue, the model is
   asked which metrics it needs; it answers with a `TOOL: a, b, c` line.
2. The orchestrator executes those lookups and feeds the results back.
3. The model writes the analysis.

The analyst prompt asks it to **name the rule id when it engages with a flag** —
"the `low_interest_coverage` flag", not "interest looks tight". That converts the
critic's coverage check from fuzzy text-matching into a stated protocol, and a
paraphrased flag that never names itself is genuinely indistinguishable from one
that was ignored.

### The critic

Three checks, in increasing cost and decreasing certainty.

**1. Figures** (`agents/verification.py`). Every unit-bearing figure in the prose
must reconcile with a value Stage 1 computed, with tolerance derived from the
*displayed* precision — `9.9%` reconciles with `0.099`, `12.0%` does not, and
rounding is never mistaken for fabrication. Bare integers are ignored: years and
list numbering would otherwise trigger false accusations. This also catches a
metric Stage 1 left as `None` being given a number.

**2. Flag coverage, screened** (`agents/coverage.py`). Flags arrive as structured
`FlagRef` records and are rendered to text only at the prompt boundary; matching
on pre-rendered English would be fuzzy in a way that changes with every wording
tweak.

**3. Flag coverage, adjudicated.** The model is asked only about unresolved flags,
and only about the ones whose severity means they matter.

### The screen never pronounces

Each flag resolves to one of **three** states, not two:

| state | meaning | goes to the model |
|---|---|---|
| `ADDRESSED` | the rule id, or the flag's own value, appears | no |
| `LOOKS_UNADDRESSED` | no trace, but the flag has a signal a real treatment would surface | yes, if critical |
| `UNCLEAR` | no signal exists — `imputed_column` has no number | always |

The screen *selects candidates and stops*. It never concludes a flag was ignored,
because an analysis that paraphrases without the keywords looks identical to one
that never thought about it, and a screen that quietly ruled on that would
manufacture false confidence. `UNCLEAR` exists precisely so that "nothing to look
for" is never confused with "nothing found".

Signals are the rule id and the flag's own `value` — deliberately not every number
in the message, because "below the 1.0x threshold" appears in nearly every flag
and matching on it would mark almost anything as addressed.

### Severity gating, in config

Naive per-flag checking would fire on essentially every real analysis: INIT has
23 flags and five sentences cannot address them. A check that always fires trains
its reader to ignore it — the same failure mode Stage 1's rule design warns about.
So the policy lives in `config.py`, not in the code:

```python
MUST_ADDRESS_SEVERITIES = ("critical",)   # ignoring these is REJECT
ADVISORY_SEVERITIES     = ("warning",)    # reported, does not move the verdict
OPTIONAL_SEVERITIES     = ("info",)       # never required
```

Model cost is therefore proportional to the actual problem: a company with no
flags costs nothing, and one whose flags are all cited costs nothing either.

### Verdicts, and why REJECT has two meanings

`OK` / `REVISE` / `REJECT`, with precedence: a numerified undefined metric outranks
an unaddressed flag, which outranks an unverifiable figure.

REJECT has two causes with **opposite** answers, so the verdict carries a
`retryable` field rather than leaving the reader to infer it from the word:

| cause | verdict | retryable | why |
|---|---|---|---|
| `numerified_undefined` | REJECT | **no** | the model filled a hole; rewording cannot help |
| critical flag not addressed | REJECT | **yes** | rewording is exactly the fix |

The report renders the reason (`REJECT (not retryable)`) because "REJECT" alone
tells a reader neither whether to trust the analysis nor where to look.
Two invariants hold throughout, and both are tested:

- **A deterministic finding is never overridden by the model.** If the figures do
  not reconcile the verdict is not OK whatever the critic says — a model asked to
  check arithmetic will often confirm whatever it is shown.
- **Abstention is not acquittal.** An unusable reply falls back to the
  deterministic result with a note saying so, rather than inventing a revision the
  analyst cannot act on.

### The revision loop

Bounded at one retry, and gated on the verdict being `REVISE`. A second attempt
usually means critic and analyst are arguing about style, and an unbounded loop
against a live model is an unbounded bill.

Note that the two `REJECT` causes never reach the loop at all — `REJECT` is not
`REVISE`. `retryable` is therefore carried for the *reader and the report*
rather than as a loop condition: it says whether another attempt could have
helped, which is the question a person staring at a `REJECT` actually has.

### Constraints inherited from Stage 1

- **Stage 1's output is a stable contract.** Changing a metric's meaning is a
  breaking change for everything downstream.
- **`None` means undefined and is passed through as such.** An agent told "n/a"
  can ask a follow-up; an agent handed `nan` will analyse it.
- **Judgement belongs here, not there.** Stage 1 states values without opinions
  (`tests/test_summary.py` enforces a banned-word list) precisely so it cannot bias
  the agents that read it. Stage 2 is where conclusions may be drawn — but every
  figure must still trace back to Stage 1.

### What is verified, and what is not

Verified: the pipeline, prompts, tool loop, critic, coverage screen and
orchestration all execute end to end with **no API key and no network**. A key
changes prose quality and nothing structural.

Not verified: the OpenAI adapter has never made a real call, and the model's
judgement on whether prose is *adequate* has never been exercised against real
output. Everything mechanical around it has been.

## Later stages

- **Stage 3** — retrieval over filings for the figures a CSV lacks (segment
  detail, footnotes, management discussion).
- **Stage 4** — cross-company comparison and peer-relative metrics, which needs
  the `sector` column the sample data already carries.

## Later stages

- **Stage 3** — retrieval over filings for the figures a CSV lacks (segment
  detail, footnotes, management discussion).
- **Stage 4** — cross-company comparison and peer-relative metrics, which needs
  the `sector` column the sample data already carries.
