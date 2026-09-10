"""Claude backend. Streams the answer token by token so the card fills in live."""
from __future__ import annotations

import logging
from typing import AsyncIterator

import anthropic

log = logging.getLogger("llm.anthropic")


class AnthropicProvider:
    label = "Claude"

    def __init__(self, cfg):
        self.cfg = cfg
        # A key typed into the settings screen wins; otherwise the SDK resolves
        # ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, or an `ant auth login`
        # profile, in that order.
        self.client = (
            anthropic.AsyncAnthropic(api_key=cfg.api_key)
            if cfg.api_key
            else anthropic.AsyncAnthropic()
        )
        self._use_beta = True

    def _params(self, system: str, messages: list[dict]) -> dict:
        return {
            "model": self.cfg.model,
            "max_tokens": self.cfg.max_tokens,
            # The system prompt never changes, so cache it and stop paying for
            # it on every single question.
            "system": [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": messages,
            "output_config": {"effort": self.cfg.effort},
        }

    async def list_models(self) -> list[str]:
        """Ask the provider what it actually offers today."""
        page = await self.client.models.list(limit=50)
        return [m.id for m in page.data]

    @staticmethod
    def _auth_problem(exc: Exception) -> bool:
        """The SDK reports a missing key as a TypeError at request time."""
        return "resolve authentication" in str(exc).lower()

    async def stream(self, system: str, messages: list[dict]) -> AsyncIterator[str]:
        emitted = False
        if self._use_beta:
            try:
                async for text in self._beta_stream(system, messages):
                    emitted = True
                    yield text
                return
            except (TypeError, anthropic.APIStatusError) as exc:
                status = getattr(exc, "status_code", None)
                if emitted or (isinstance(exc, anthropic.APIStatusError) and status != 400):
                    raise
                if self._auth_problem(exc):
                    raise RuntimeError(
                        "No Anthropic API key. Add one in Settings, or put "
                        "ANTHROPIC_API_KEY in the .env file on the laptop."
                    ) from exc
                # A genuinely older SDK that does not know these parameters --
                # stop trying them for the rest of the session.
                log.warning(
                    "beta request rejected (%s); falling back to the plain "
                    "Messages API for the rest of this session", exc
                )
                self._use_beta = False

        async for text in self._plain_stream(system, messages):
            yield text

    async def _beta_stream(self, system: str, messages: list[dict]) -> AsyncIterator[str]:
        params = self._params(system, messages)
        # `fallbacks: "default"` re-runs a declined request on Anthropic's
        # recommended substitute model server-side instead of handing us a
        # refusal. Costs nothing when it never triggers.
        betas = ["server-side-fallback-2026-07-01"]
        if self.cfg.fast_mode:
            betas.append("fast-mode-2026-02-01")
            params["speed"] = "fast"

        async with self.client.beta.messages.stream(
            betas=betas, fallbacks="default", **params
        ) as stream:
            async for text in stream.text_stream:
                yield text
            final = await stream.get_final_message()
            if final.stop_reason == "refusal":
                yield "\n\n_(Claude declined this one -- rephrase and ask again.)_"

    async def _plain_stream(self, system: str, messages: list[dict]) -> AsyncIterator[str]:
        params = self._params(system, messages)
        try:
            async with self.client.messages.stream(**params) as stream:
                async for text in stream.text_stream:
                    yield text
            return
        except TypeError as exc:
            if self._auth_problem(exc):
                raise RuntimeError(
                    "No Anthropic API key. Add one in Settings, or put "
                    "ANTHROPIC_API_KEY in the .env file on the laptop."
                ) from exc
            params.pop("output_config", None)  # older SDK build

        async with self.client.messages.stream(**params) as stream:
            async for text in stream.text_stream:
                yield text
