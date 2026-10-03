"""OpenAI adapter.

The ``openai`` package is imported *inside* :meth:`complete`, not at module
import time. That single decision means the whole package imports, and the whole
test suite runs, on a machine with no SDK and no API key -- which is the state
this project is in.

The key is read from the environment and never logged, echoed into a prompt, or
written to an artifact. :class:`~financial_analyst.agents.providers.base.ProviderConfig`
exposes only ``is_configured`` and a private ``api_key`` property, so a config
object is safe to put in a repr.
"""

from __future__ import annotations

from typing import Optional

from .base import CompletionError, ProviderConfig


class OpenAIClient:
    """Chat-completions adapter. Stateless: each call is self-contained."""

    def __init__(self, config: Optional[ProviderConfig] = None):
        self.config = config or ProviderConfig()
        self._client = None

    # -- lazily built so importing this module needs no SDK ---------------
    def _sdk(self):
        if self._client is None:
            try:
                from openai import OpenAI  # type: ignore
            except ImportError as error:
                raise CompletionError(
                    "the 'openai' package is not installed; "
                    "install it or use provider='stub'"
                ) from error
            key = self.config.api_key
            if key is None:
                raise CompletionError(
                    f"{self.config.api_key_env} is not set, so no live call can be made"
                )
            kwargs = {"api_key": key, "timeout": self.config.timeout_seconds}
            if self.config.base_url:
                kwargs["base_url"] = self.config.base_url
            self._client = OpenAI(**kwargs)
        return self._client

    def complete(self, *, system: str, user: str, **options) -> str:
        client = self._sdk()
        try:
            response = client.chat.completions.create(
                model=options.get("model", self.config.model),
                temperature=options.get("temperature", self.config.temperature),
                max_tokens=options.get("max_tokens", self.config.max_tokens),
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
        except CompletionError:
            raise
        except Exception as error:  # noqa: BLE001 - SDK raises a broad family
            # The message can echo request content, so keep it short and do not
            # include the prompt.
            raise CompletionError(f"openai request failed: {type(error).__name__}") from error

        content = response.choices[0].message.content
        if content is None:
            raise CompletionError("openai returned an empty completion")
        return content.strip()
