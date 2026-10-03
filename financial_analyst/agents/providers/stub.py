"""A deterministic LLM stand-in.

Used by every test and as the fallback when no key is configured. Two properties
make it useful rather than a mock:

* **Scripted.** You queue the replies; each call pops the next one. That is what
  lets a test drive a two-turn tool loop end to end.
* **Recorded.** Every call is kept, so a test can assert on the *prompt* the
  agent built -- the prompts are where agent behaviour usually goes wrong, and
  they are otherwise invisible.

With no queued replies it returns a fixed placeholder rather than raising, so a
miscounted script produces an obviously wrong result rather than an error that
looks like a product bug.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .base import CompletionError

PLACEHOLDER = "[no scripted reply available]"


@dataclass
class Call:
    """One recorded request."""

    system: str
    user: str
    options: dict = field(default_factory=dict)


class ScriptedLLM:
    """An :class:`~financial_analyst.agents.providers.base.LLMClient` that replays."""

    def __init__(self, replies: Optional[list[str]] = None, fallback: str = PLACEHOLDER):
        self._replies = list(replies or [])
        self._fallback = fallback
        self.calls: list[Call] = []

    def complete(self, *, system: str, user: str, **options) -> str:
        self.calls.append(Call(system=system, user=user, options=dict(options)))
        if not self._replies:
            return self._fallback
        return self._replies.pop(0)

    # -- helpers for tests -------------------------------------------------
    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def remaining(self) -> int:
        return len(self._replies)

    def last_user(self) -> str:
        """The most recent user prompt, for asserting on prompt content."""
        if not self.calls:
            raise AssertionError("no calls were made")
        return self.calls[-1].user

    def reset(self) -> None:
        self.calls.clear()


class AlwaysSays(ScriptedLLM):
    """A client that returns the same reply however many times it is called."""

    def __init__(self, reply: str):
        super().__init__(replies=[], fallback=reply)

    def complete(self, *, system: str, user: str, **options) -> str:
        self.calls.append(Call(system=system, user=user, options=dict(options)))
        return self._fallback


class ExplodingLLM:
    """A client that always fails, for testing error propagation."""

    def __init__(self, message: str = "provider unavailable"):
        self._message = message
        self.calls: list[Call] = []

    def complete(self, *, system: str, user: str, **options) -> str:
        self.calls.append(Call(system=system, user=user, options=dict(options)))
        raise CompletionError(self._message)
