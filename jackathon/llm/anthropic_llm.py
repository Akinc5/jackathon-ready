"""Adapter for Claude models via the official Anthropic SDK."""

from __future__ import annotations

import os
from typing import Any

import anthropic

from .base import LLM, LLMError, LLMResponse, Message, ToolCall, ToolSpec, Usage

# Models that support server-side refusal fallbacks: if a safety classifier
# declines a request, the API retries it on another model automatically.
_FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicLLM(LLM):
    provider = "anthropic"

    def __init__(self, model: str, api_key: str | None = None, max_tokens: int = 16000):
        self.model = model
        self.max_tokens = max_tokens
        # How hard the model thinks: low | medium | high | xhigh | max.
        # Set JACK_EFFORT=none for older models that don't support it.
        self.effort = os.getenv("JACK_EFFORT", "high")
        # With no key passed, the SDK reads ANTHROPIC_API_KEY from the environment.
        self.client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=4)

    async def chat(
        self,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
    ) -> LLMResponse:
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": _to_anthropic(messages),
        }
        if tools:
            params["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]
        if self.effort != "none":
            params["output_config"] = {"effort": self.effort}

        try:
            if self.model in _FALLBACK_MODELS:
                response = await self.client.beta.messages.create(
                    **params, betas=[_FALLBACK_BETA], fallbacks="default"
                )
            else:
                response = await self.client.messages.create(**params)
        except anthropic.APIStatusError as e:
            raise LLMError(f"Anthropic API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError(f"Could not reach the Anthropic API: {e}") from e

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise LLMError(f"The model declined this request (category: {category}).")

        text = "".join(b.text for b in response.content if b.type == "text")
        calls = [
            ToolCall(id=b.id, name=b.name, arguments=dict(b.input))
            for b in response.content
            if b.type == "tool_use"
        ]
        message = Message(
            role="assistant",
            content=text,
            tool_calls=calls,
            raw=response.content,  # replayed verbatim on the next turn (keeps thinking blocks)
            raw_provider=self.provider,
        )
        usage = Usage(response.usage.input_tokens, response.usage.output_tokens)
        return LLMResponse(message=message, stop_reason=response.stop_reason or "", usage=usage)


def _to_anthropic(messages: list[Message]) -> list[dict[str, Any]]:
    """Convert neutral messages to Anthropic's format.

    Anthropic has no "tool" role: tool results are `tool_result` blocks inside a
    user message, and all results for one turn must go in the same message.
    So consecutive user-side blocks get merged into a single user message.
    """
    out: list[dict[str, Any]] = []

    def add_user_block(block: dict[str, Any]) -> None:
        if out and out[-1]["role"] == "user":
            out[-1]["content"].append(block)
        else:
            out.append({"role": "user", "content": [block]})

    for m in messages:
        if m.role == "user":
            add_user_block({"type": "text", "text": m.content})
        elif m.role == "tool":
            add_user_block(
                {
                    "type": "tool_result",
                    "tool_use_id": m.tool_call_id,
                    "content": m.content,
                    "is_error": m.is_error,
                }
            )
        elif m.raw is not None and m.raw_provider == "anthropic":
            out.append({"role": "assistant", "content": m.raw})
        else:
            # An assistant message written by another provider (or by a test):
            # rebuild it as text + tool_use blocks.
            blocks: list[dict[str, Any]] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            for c in m.tool_calls:
                blocks.append({"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments})
            out.append({"role": "assistant", "content": blocks})
    return out
