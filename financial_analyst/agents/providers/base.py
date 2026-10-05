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

    ``finish_reason`` carries the API's own account of why a reply ended, when
    there is one. It is the difference between "the model refused to answer"
    and "the model ran out of tokens mid-sentence", and those are impossible to
    tell apart from the returned text alone -- a truncated reply parses as badly
    but for a completely different reason, and reading that as a substantive
    problem with the analysis would be a mistake.
    """

    def __init__(self, message: str, finish_reason: Optional[str] = None):
        super().__init__(message)
        self.finish_reason = finish_reason


def load_dotenv(path: str = ".env", override: bool = False) -> bool:
    """Populate ``os.environ`` from a ``.env`` file, if one exists.

    Deliberately not a dependency. The whole package imports, and the whole suite
    runs, with nothing installed beyond pandas and numpy -- that property is worth
    more than convenience, and adding ``python-dotenv`` to break it would be a bad
    trade for a file format this is simple enough to read.

    ``override=False`` so an already-exported key wins. A shell export is a more
    deliberate act than a file, and should not be silently overridden by one.

    Existing environment variables are never displaced, and the file's contents
    are never logged: a malformed line is skipped rather than echoed, since the
    only thing in such a file is a credential.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw_lines = handle.readlines()
    except OSError:
        return False

    for raw in raw_lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        name, separator, value = line.partition("=")
        if not separator:
            continue                      # not an assignment; skip quietly
        name = name.strip()
        if not name or not name.replace("_", "").isalnum():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if override or name not in os.environ:
            os.environ[name] = value
    return True


@dataclass(frozen=True)
class ProviderConfig:
    """Everything a provider needs, and nothing the agents should care about."""

    model: str = "gpt-4o-mini"
    api_key_env: str = "OPENAI_API_KEY"
    base_url: Optional[str] = None
    temperature: float = 0.0
    max_tokens: int = 1200
    timeout_seconds: float = 30.0
    #: Optional path to a ``.env`` file to read the key from. ``None`` reads only
    #: the process environment; set it to have this module find the key itself.
    dotenv_path: Optional[str] = None

    @property
    def api_key(self) -> Optional[str]:
        """The key from the environment, or ``None``. Never logged or echoed.

        A whitespace-only value counts as absent: ``"".strip() is not None`` is
        True, so returning the stripped string would present a blank
        credential as a configured one.
        """
        if self.dotenv_path is not None:
            load_dotenv(self.dotenv_path)
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
