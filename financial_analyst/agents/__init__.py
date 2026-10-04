"""Analyst, critic and orchestration for Stage 2.

Order of construction is deliberate: a provider seam, then the tool layer that
bounds what an agent can know, then the agents themselves, then the
orchestrator. Each layer is testable without the one above it.
"""

from .critic import CriticAgent, CriticVerdict, OK, REJECT, REVISE
from .analyst import AnalystAgent, AnalystResult
from .orchestrator import Orchestrator, Stage2Result
from .tools import MetricStore, ToolCatalogue, execute, parse_tool_request
from .verification import Claim, ClaimIssue, extract_claims, verify

__all__ = [
    "AnalystAgent", "AnalystResult",
    "CriticAgent", "CriticVerdict", "OK", "REVISE", "REJECT",
    "Orchestrator", "Stage2Result",
    "MetricStore", "ToolCatalogue", "execute", "parse_tool_request",
    "Claim", "ClaimIssue", "extract_claims", "verify",
]