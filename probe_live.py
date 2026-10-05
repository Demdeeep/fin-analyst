"""One-off live probe: ACME's analyst turns, against a real model.

Not part of the suite. Lives in the repo root so a live call is always a
deliberate, visible act rather than something a test can trigger. Writes to
outputs/, which is gitignored.
"""

from pathlib import Path

from financial_analyst import config
from financial_analyst.agents.analyst import AnalystAgent
from financial_analyst.agents.flags import load_flag_refs
from financial_analyst.agents.providers.base import ProviderConfig, load_dotenv
from financial_analyst.agents.providers.openai_client import OpenAIClient
from financial_analyst.agents.tools import MetricStore
from financial_analyst.agents.verification import verify

outputs = config.OUTPUT_DIR
load_dotenv(".env")

store = MetricStore.from_json(config.OUTPUT_METRICS_JSON)
client = OpenAIClient(config=ProviderConfig())
flags_by_ticker = load_flag_refs(config.OUTPUT_FLAGS_CSV)

result = AnalystAgent(client, store).analyse(
    "ACME", flags=[flag.render() for flag in flags_by_ticker.get("ACME", ())]
)
issues = verify(result.narrative, store, "ACME")

lines = [
    "=== ACME analyst, live model, --no-write ===",
    "",
    "--- TURN 1 raw reply (tool request) ---",
    repr(result.first_turn),
    "",
    "--- parsed tool request ---",
    repr(list(result.observation.requested)),
    "",
    "--- TURN 2 raw reply (narrative) ---",
    result.narrative,
    "",
    "--- finish reasons ---",
    f"last={client.last_finish_reason!r} truncated={len(client.truncated)}",
    "",
    "--- mechanical verification ---",
    f"issues={issues}",
]
out_path = outputs / "_live_acme.md"
out_path.write_text("\n".join(lines) + "\n")
print(f"wrote {out_path}")

