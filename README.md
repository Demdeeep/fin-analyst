# fin-analyst

A multi-agent financial analyst, built one stage at a time.

**Stage 1 is complete**: a deterministic analysis of a static CSV of company
financials — ratio maths, anomaly detection, and a written report. No language
model is involved. Running it twice on the same input produces byte-identical
output, which is what lets the test suite use golden files as a regression net.

**Stages 2, 3 and 4 are also built.** Stage 2 runs an analyst, a critic and a
bounded orchestrator over Stage 1's artifacts, with every figure in the prose
verified mechanically and every anomaly flag screened for coverage. Stage 4
compares companies against their peers, computing medians, gaps and
direction-aware ranks in code rather than in the model. Stage 3 searches a
company's latest annual SEC filing and returns passages with source metadata.
See
[`docs/architecture.md`](docs/architecture.md).

## Quick start

### Local web dashboard

Install the optional web interface and local PDF text/table extraction, then run Northstar locally:

```bash
python -m pip install -e '.[web,pdf]'
streamlit run app.py
```

Northstar starts with the included sample CSV. You can upload another CSV,
run Stage 1, then try Stage 2 and Stage 4 with the deterministic `stub` provider
without API credits. Stage 2 can also retrieve SEC filing excerpts when you
enable filing context and provide an identifying SEC contact email.

The **PDF review** tab extracts text and page references locally. Scanned PDFs
that contain only images need OCR and are not supported yet. To generate a PDF
narrative, select OpenAI or Anthropic and explicitly allow the extracted text to
be sent to that provider. That narrative is grounded in the document pages but
is not checked against Stage 1's CSV metrics. Live analysis uses the selected
provider's API key from the environment.

Use **Extract financial tables** for local rule-based extraction of recognized
income statement, balance sheet and cash flow rows. Results include PDF page and
table references and can be downloaded as CSV. Rows keep the source amount;
USD-million normalization is filled only when the table clearly reports
millions or thousands. Review every row against its source table before use.

### Deploy the interactive interface on Netlify

The original Streamlit dashboard remains available locally. The Netlify version
uses the same Python analysis code through a small FastAPI service: Netlify
hosts the interactive static interface, and a separate Python web service runs
the financial pipeline. The browser sends each uploaded file to that Python
service for the selected analysis; uploads and generated reports are not saved
to the repository or a database.

For a no-credit test, keep the provider set to **Stub**. It is deterministic and
does not call a model API. To host the Python service for testing, Render offers
a free web-service tier with limits; it sleeps after 15 minutes without traffic
and can take about a minute to wake. See [Render's free-service limitations](https://render.com/docs/free).

1. Push the version you want to try to GitHub after reviewing its changes.
2. In Render, create a Blueprint from this repository and use the included
   `render.yaml`. The service runs `financial_analyst.api:app`; copy its
   `https://...onrender.com` URL after the health check succeeds. For a public
   test site, create a random preview token with
   `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'` and set it as
   `NORTHSTAR_API_TOKEN` in Render. This protects the upload and analysis API
   from callers who do not know the token.
3. In Netlify, import the same repository. `netlify.toml` publishes the
   `frontend/` folder; there is no frontend build step.
4. Open the Netlify site, choose **Connection settings**, paste the Python API
   URL, enter the same preview token, and select **Test connection**. The URL
   and token are saved in that browser only. Share the token only with intended
   testers; this is a simple shared gate, not a user-account system.
5. Use **Try sample data** and **Stub** first. The included sample contains
   synthetic data; it does not make model calls.

The backend allows Netlify preview URLs by default. For a custom domain, set
`NORTHSTAR_CORS_ORIGINS` in the Render service to the exact site origin, such
as `https://northstar.example.com`, without a trailing slash. If you later add
OpenAI or Anthropic credentials, store those provider keys on the Python
service—never in Netlify's public frontend. Live-provider endpoints are
disabled until `NORTHSTAR_API_TOKEN` is configured, and the interface asks
before sending company data to a model. Keep confidential CSVs and PDFs off a
public test deployment; use synthetic or public data unless the service's
access controls are appropriate for the material.

To run the new split interface locally, install the API extras, then use two
terminals:

```bash
python3 -m pip install -e ".[api,pdf]"
python3 -m uvicorn financial_analyst.api:app --reload --port 8000
python3 -m http.server 5173 --directory frontend
```

Open `http://localhost:5173`. The browser defaults to the local API at
`http://127.0.0.1:8000`. The API reference is available at
`http://127.0.0.1:8000/docs`.

### Deploy Northstar on Streamlit Community Cloud

The repository is prepared for Streamlit Community Cloud. The tested runtime is
Python 3.11; the deployment page must use the same version because the package
declares Python `>=3.11,<3.12`. The root `requirements.txt` contains the app,
PDF and provider packages used by the hosted interface. The `.streamlit/config.toml`
sets the visual theme and a 25 MB upload limit.

Before deploying, make sure the code you want to publish is in the GitHub
repository and that you have permission to deploy that repository. In
Streamlit Community Cloud:

1. Create an app and select this repository, the branch to deploy, and
   `app.py` as the entrypoint.
2. In **Advanced settings**, choose Python 3.11.
3. Add only the secrets you intend to use. For OpenAI, Anthropic or SEC EDGAR,
   use the names shown in `.streamlit/secrets.toml.example`. The model keys are
   optional; Northstar starts with the free `stub` provider. Hosted secrets are
   read by the app and are not shown in its interface.
4. Choose whether the hosted app should be public or restricted to approved
   viewers in the host's app settings.

Uploaded financial CSVs and PDFs are processed by the hosted app, so do not
upload confidential documents unless the hosting and access settings are
appropriate for them. Stage 2 and Stage 4 send the metrics needed for a requested
analysis to the selected model provider. PDF text is sent to OpenAI or Anthropic
only after the PDF analysis consent checkbox is selected. SEC filing searches
send the ticker and query to EDGAR.

To check the same environment locally before deploying:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest -q
streamlit run app.py
```

The CI workflow installs `requirements-dev.txt`; Streamlit Community Cloud
uses the runtime-only `requirements.txt`.

For local hosted-style secrets, copy `.streamlit/secrets.toml.example` to
`.streamlit/secrets.toml` and fill only the credentials you need. The real
`secrets.toml` is gitignored. Environment variables set in the shell take
precedence over this file.

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
.venv/bin/python -m financial_analyst.stages.stage3_filing_retrieval --ticker AAPL --query "services revenue growth"
.venv/bin/python -m financial_analyst.stages.stage4_peer_comparison
```

Stages 2 and 4 default to the scripted stub provider, so they need no API key.
Pass `--provider openai` or `--provider anthropic` to use a live model.

### Stage 3 SEC access

Stage 3 uses public EDGAR filing data and does not need an API key or model
credits. Set an identifying SEC User-Agent with a contact email before making
requests:

```bash
export FIN_ANALYST_SEC_USER_AGENT="Your Name your.email@example.com"
.venv/bin/python -m financial_analyst.stages.stage3_filing_retrieval \
    --ticker AAPL --query "services revenue growth"
```

The command writes `outputs/stage3_filing_retrieval.json` and
`outputs/stage3_filing_retrieval.md`. Retrieval is lexical: it ranks passages
that share query terms and supplies filing dates, accession number and source
URL. It does not interpret or verify the passage. Review the source filing
before relying on a result. `--no-write` prints results without saving files.

To give retrieved passages to the Stage 2 analyst, run Stage 2 with a query:

```bash
.venv/bin/python -m financial_analyst.stages.stage2_agents \
    --ticker AAPL --provider stub --filing-query "services revenue growth"
```

The ticker must exist in the Stage 1 metrics and be listed by EDGAR. Stage 2
adds matched passages to the analyst's context and asks it to cite them as
`[FILING 1]`, with a source list in the report. Filing excerpts provide
qualitative context; the analyst must use Stage 1 for figures so the existing
number checks still apply. This is optional: Stage 2 remains offline when
`--filing-query` is omitted.

### Live model access

Two prerequisites, and the error message names whichever is missing:

1. **The SDK.** Optional by design — Stage 1 and the whole test suite run without it.

   ```bash
   uv pip install -e ".[openai]"
   ```

   For Anthropic instead, install `uv pip install -e ".[anthropic]"`.

2. **A key.** Use `OPENAI_API_KEY` with `--provider openai` or
   `ANTHROPIC_API_KEY` with `--provider anthropic`. Both are read from the
   environment. Copy `.env.example` to `.env` and fill in the key you use, then
   pass `--dotenv .env`:

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

GitHub Actions runs the full suite on pushes and pull requests using Python 3.11
and the root `requirements.txt`.

The suite is layered deliberately:

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
| Filing retrieval | `test_stage3_filing_retrieval.py` | SEC lookup, annual filing selection, lexical ranking, abstention and cited outputs work offline |

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
