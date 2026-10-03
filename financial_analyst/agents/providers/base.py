"""The seam every other Stage 2 module is written against.

One method on purpose. The richer surfaces real SDKs offer -- streaming, native
tool calling, multi-turn message arrays -- are transport concerns, and letting
them into the agent loop would tie the agents to one vendor's protocol.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable


class CompletionError(RuntimeError):
    """The provider could not produce a completion.

    Distinct from a bad completion: a *provider* failure is an infrastructure
    problem and should abort the run loudly, whereas a completion that fails
    verification is a finding to report.
    """


@dataclass(frozen=True)
class ProviderConfig:
    """Everything a provider needs, and nothing the agents should care about."""

    model: str = "gpt-4o-mini"
    api_key_env: str = "OPENAI_API_KEY"
    base_url: Optional[str] = None
    temperature: float = 0.0
    max_tokens: int = 1200
    timeout_seconds: float = 30.0

    @property
    def api_key(self) -> Optional[str]:
        """The key from the environment, or ``None``. Never logged or echoed.

        A whitespace-only value counts as absent: ``"".strip() is not None`` is
        True, so returning the stripped string would present a blank
        credential as a configured one.
        """
        value = os.environ.get(self.api_key_env)
        if value is None:
            return None
        return value.strip() or None

    @property
    def is_configured(self) -> bool:
        return self.api_key is not None


@runtime_checkable
class LLMClient(Protocol):
    """The single method Stage 2 depends on."""

    def complete(self, *, system: str, user: str, **options) -> str:
        """Return the assistant's reply as plain text.

        Implementations raise :class:`CompletionError` on infrastructure
        failure. They must not raise on a poor-quality reply: judging quality
        is the critic's job, not the transport's.
        """
        ...
