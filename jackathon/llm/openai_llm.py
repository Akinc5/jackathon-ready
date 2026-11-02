"""Adapter for every provider that speaks the OpenAI "chat completions" format.

That covers OpenAI itself plus Gemini, Groq, OpenRouter and Ollama, which all
offer an OpenAI-compatible endpoint. Only the base URL and API key differ.
"""

from __future__ import annotations

import json
from typing import Any

import openai

from .base import LLM, LLMError, LLMResponse, Message, ToolCall, ToolSpec, Usage


class OpenAICompatibleLLM(LLM):
    def __init__(self, provider: str, model: str, api_key: str | None, base_url: str | None = None):
        self.provider = provider
        self.model = model
        # Ollama needs no key, but the SDK insists on one, so pass a placeholder.
        self.client = openai.AsyncOpenAI(
            api_key=api_key or "not-needed", base_url=base_url, max_retries=4
        )

    async def chat(
        self,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
    ) -> LLMResponse:
        params: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *_to_openai(messages)],
        }
        if tools:
            params["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]

        try:
            response = await self.client.chat.completions.create(**params)
        except openai.APIStatusError as e:
            raise LLMError(f"{self.provider} API error {e.status_code}: {e.message}") from e
        except openai.APIConnectionError as e:
            raise LLMError(f"Could not reach the {self.provider} API: {e}") from e

        choice = response.choices[0]
        msg = choice.message
        calls = []
        for i, tc in enumerate(msg.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments or "{}")
                parse_error = None
            except json.JSONDecodeError as e:
                args, parse_error = {}, f"Arguments were not valid JSON: {e}"
            calls.append(
                ToolCall(
                    id=tc.id or f"call_{i}",
                    name=tc.function.name,
                    arguments=args,
                    parse_error=parse_error,
                )
            )

        usage = Usage(
            getattr(response.usage, "prompt_tokens", 0) or 0,
            getattr(response.usage, "completion_tokens", 0) or 0,
        )
        return LLMResponse(
            message=Message(role="assistant", content=msg.content or "", tool_calls=calls),
            stop_reason=choice.finish_reason or "",
            usage=usage,
        )


def _to_openai(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "user":
            out.append({"role": "user", "content": m.content})
        elif m.role == "tool":
            content = f"ERROR: {m.content}" if m.is_error else m.content
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": content})
        else:
            entry: dict[str, Any] = {"role": "assistant", "content": m.content or None}
            if m.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                    }
                    for c in m.tool_calls
                ]
            out.append(entry)
    return out
