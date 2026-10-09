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
        #: Replies that hit the token limit. Recorded rather than raised, because
        #: a truncated reply is still readable -- it is just not trustworthy, and
        #: the run should be able to finish and say so.
        self.truncated: list[str] = []
        #: The ``finish_reason`` of the most recent call, for diagnostics.
        self.last_finish_reason: Optional[str] = None
        #: Token-usage metadata from the most recent call, when the SDK returns it.
        #: Stored as a plain dict so callers and tests do not depend on SDK types.
        self.last_usage: Optional[dict[str, int]] = None

    # -- lazily built so importing this module needs no SDK ---------------
    def _sdk(self):
        if self._client is None:
            # Key first. With both problems present -- which is the state this
            # project was in -- "install the SDK" is advice you cannot act on
            # until you have a key, and it hides the fact that one is missing.
            key = self.config.api_key
            if key is None:
                raise CompletionError(
                    f"{self.config.api_key_env} is not set, so no live call can be made"
                )
            try:
                from openai import OpenAI  # type: ignore
            except ImportError as error:
                raise CompletionError(
                    "the 'openai' package is not installed; "
                    "install it (pip install -e '.[openai]') or use provider='stub'"
                ) from error
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

        choice = response.choices[0]
        content = choice.message.content
        finish_reason = getattr(choice, "finish_reason", None)
        usage = getattr(response, "usage", None)

        if content is None:
            raise CompletionError(
                f"openai returned an empty completion "
                f"(finish_reason={finish_reason})",
                finish_reason=finish_reason,
            )

        text = content.strip()
        self.last_finish_reason = finish_reason
        if usage is None:
            self.last_usage = None
        else:
            self.last_usage = {
                "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
                "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
            }

        # Truncation is reported, not raised. A reply cut off at the token limit
        # arrives as well-formed prose that happens to stop mid-thought, and the
        # caller -- the critic's verdict parser -- cannot tell that from a
        # genuinely unparseable reply. Raising here would abort the run; better
        # to hand back the text with the reason attached and let the orchestrator
        # record that this reply is not trustworthy.
        if finish_reason == "length":
            self.truncated.append(text)

        return text
