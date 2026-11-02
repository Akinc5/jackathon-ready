"""Multi-provider LLM layer. Use `create_llm("provider:model")` or `llm_for(role)`."""

from __future__ import annotations

import os

from ..config import PROVIDERS, model_for
from .base import LLM, LLMError, LLMResponse, Message, ToolCall, ToolSpec, Usage

__all__ = [
    "LLM",
    "LLMError",
    "LLMResponse",
    "Message",
    "ToolCall",
    "ToolSpec",
    "Usage",
    "create_llm",
    "llm_for",
]


def create_llm(spec: str) -> LLM:
    """Build an LLM from a `provider:model` string, e.g. `gemini:gemini-2.5-pro`."""
    provider, sep, model = spec.partition(":")
    if not sep or not model:
        raise ValueError(f"Model must look like 'provider:model', got {spec!r}")
    info = PROVIDERS.get(provider)
    if info is None:
        raise ValueError(f"Unknown provider {provider!r}. Choose from: {', '.join(PROVIDERS)}")

    api_key = os.getenv(info.key_env) if info.key_env else None
    if info.key_env and not api_key:
        raise LLMError(f"{info.key_env} is not set. Add it to your .env file to use {provider}.")

    # Imported here so you only need the SDK for the providers you actually use.
    if provider == "anthropic":
        from .anthropic_llm import AnthropicLLM

        return AnthropicLLM(model, api_key=api_key)

    from .openai_llm import OpenAICompatibleLLM

    base_url = (os.getenv(info.base_url_env) if info.base_url_env else None) or info.base_url
    return OpenAICompatibleLLM(provider, model, api_key=api_key, base_url=base_url)


def llm_for(role: str) -> LLM:
    """The LLM configured for an agent role (see config.model_for)."""
    return create_llm(model_for(role))
