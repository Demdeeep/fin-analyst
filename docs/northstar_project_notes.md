# Northstar — project explanation notes

Use these notes for a demo, class presentation, portfolio walkthrough, or a
conversation with someone seeing Northstar for the first time.

## One-sentence introduction

**Northstar is a multi-stage financial research tool that turns company
financial statements into calculated metrics, review flags, and a traceable
analyst narrative.**

## Short introduction (about 30 seconds)

I built Northstar to make financial analysis easier to follow and check. A user
uploads a company-financials CSV or reviews a text-based annual-report PDF.
Northstar calculates ratios and trends in Python, points out data or performance
signals, and can ask an AI analyst to explain what it sees. A separate critic
checks the analyst's numbers against the calculations. The interface connects
to a Python service, so the calculations stay in the original analysis code.

## The problem it addresses

Financial reports contain many figures, but it can be hard to connect them into
a clear view of a company's performance. A generated explanation can also sound
confident while using the wrong figure. Northstar separates the arithmetic from
the generated explanation: Python calculates the metrics, and a mechanical
check compares numbers in the narrative with those results.

## How the workflow works

### Stage 1 — Fundamentals

The user supplies a CSV with one row per company and fiscal year. Northstar
validates its columns and values, calculates financial metrics, and runs
deterministic checks for issues such as declining revenue, compressed margins,
or accounting identities that do not balance. This stage does not need a model
or API credits.

### Stage 2 — Analyst review

An analyst agent uses Stage 1 metrics and the flags to describe performance and
risks. A critic then checks its numeric claims against Stage 1 and checks
whether required flags were addressed. The default **Stub** provider is
deterministic and free; OpenAI and Anthropic are optional providers that need
their own API credentials.

### Stage 3 — Filing context

Northstar can search a company's latest annual SEC filing for passages related
to a question. It returns excerpts with page or filing-source references for the
analyst to use. Retrieval gives context; it does not prove that a filing claim
is true or complete.

### Stage 4 — Peer comparison

Northstar compares selected companies using metrics calculated in Stage 1. It
computes medians, gaps, and direction-aware ranks in Python, then can ask the
selected provider to explain the comparison. It declines comparisons when the
group does not have enough companies or a metric is unavailable.

### PDF review

The document workspace extracts selectable text and recognized financial-table
rows from a PDF, with page references. Users can inspect source rows and create
a Stage 1 CSV draft. PDF extraction is a starting point for human review: units
can be unclear, layouts vary, and scanned image PDFs need OCR, which is not
included.

## How to demonstrate it

1. Open the Netlify interface and show the connection status.
2. Choose **Try sample data**, then run Stage 1. Explain that the sample is
   synthetic and the calculations are deterministic.
3. Point to the revenue trend, calculated-metrics table, and flag filters.
4. Keep the provider on **Stub** and run an analyst review. Show the verdict,
   figure-verification status, and any flags still needing review.
5. Run the peer comparison and explain that Northstar computes peer statistics
   in Python rather than trusting the language model to do arithmetic.
6. If time allows, open the Document Lab and inspect a PDF's page text and
   extracted values. Point out that each extracted value should be checked
   against its cited source page before using it.

## Suggested spoken walkthrough

> Northstar is a financial research project I built in stages. It starts with a
> CSV of company financials and calculates the ratios and trends in Python.
> Those results feed anomaly checks, an optional analyst and critic workflow,
> SEC filing search, and peer comparison. The key design choice is that the
> model explains and summarizes, while Python handles the arithmetic and checks
> numeric claims. I also added a document workspace to extract annual-report
> text and financial rows with page references. The Netlify site is the user
> interface; a separate Python API runs the existing analysis pipeline.

## What makes the design useful

- **Reproducible arithmetic:** Stage 1 and peer calculations are performed by
  code, not by a language model.
- **Reviewable output:** Flags, source excerpts, figure checks, and declined
  comparisons are exposed so a person can inspect them.
- **Bounded model role:** The optional model writes and reviews prose; its
  numeric claims can be checked against the computed data.
- **Low-cost development path:** Stub mode supports a full workflow demo
  without paid model credits.
- **Separated deployment:** Netlify serves the browser interface, while a
  Python API calls the existing analysis modules.

## Explain the deployment simply

The browser loads Northstar's interface from Netlify. When someone uploads a
CSV or PDF, the browser sends it to the separately hosted Python API. The API
runs the existing Stage 1–4 code and returns results for the interface to show.
Uploads are handled for the request and are not saved to a project database.
For a public test deployment, configure the shared API token and only use
synthetic or public data. Model keys, if used, belong on the Python service and
must never be placed in the public frontend.

## Important limitations to say out loud

- Northstar is a prototype and research aid, not investment advice or an
  automated buy/sell system.
- The input CSV must use Northstar's schema and reliable source data. Incorrect
  source values can produce incorrect analysis.
- Financial definitions do not apply equally to every industry; banks and other
  specialized businesses may need different metrics.
- A Stub narrative demonstrates the workflow, not the quality of a live model.
- Language-model explanations can still be incomplete or misleading even when
  their figures reconcile. Read the flags and source data.
- PDF table extraction can miss or misread rows. Confirm values, units, and
  fiscal years against the original filing.
- A free test backend may sleep when idle, making the first request slower.

## Useful terms

- **Financial metric:** A calculated measure such as gross margin or debt to
  assets.
- **Anomaly flag:** A rule-based signal that tells the reader what deserves a
  closer look; it is not automatically a conclusion about the company.
- **Critic:** The review step that checks figures and required flag coverage.
- **Peer median:** The middle peer value used as a comparison point; it is not a
  prediction or a judgment that one company is better overall.
- **Stub provider:** A scripted provider used to run the pipeline without an
  external model or model credits.
