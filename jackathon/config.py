"""Settings, read from environment variables (and a `.env` file if present).

Every agent has a *role* name ("research", "strategist", ...). The model for a
role comes from `JACK_MODEL_<ROLE>` if set, otherwise from `JACK_MODEL`.
A model is written as `provider:model`, e.g. `anthropic:claude-opus-5-5`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DEFAULT_MODEL = "anthropic:claude-opus-5-5"

# Where each run's state is saved: runs/<run_id>/state.json
RUNS_DIR = Path(os.getenv("JACK_RUNS_DIR", "runs"))


@dataclass(frozen=True)
class ProviderInfo:
    """How to reach one LLM provider."""

    key_env: str | None  # env var holding the API key (None = no key needed)
    base_url: str | None = None  # for OpenAI-compatible providers
    base_url_env: str | None = None  # env var that can override base_url


# Every provider except Anthropic speaks the OpenAI "chat completions" format,
# so one adapter (OpenAICompatibleLLM) covers all of them.
PROVIDERS: dict[str, ProviderInfo] = {
    "anthropic": ProviderInfo(key_env="ANTHROPIC_API_KEY"),
    "openai": ProviderInfo(key_env="OPENAI_API_KEY"),
    "gemini": ProviderInfo(
        key_env="GEMINI_API_KEY",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    ),
    "groq": ProviderInfo(key_env="GROQ_API_KEY", base_url="https://api.groq.com/openai/v1"),
    "openrouter": ProviderInfo(key_env="OPENROUTER_API_KEY", base_url="https://openrouter.ai/api/v1"),
    "ollama": ProviderInfo(
        key_env=None,
        base_url="http://localhost:11434/v1",
        base_url_env="OLLAMA_BASE_URL",
    ),
}


def model_for(role: str) -> str:
    """Return the `provider:model` string configured for an agent role."""
    return os.getenv(f"JACK_MODEL_{role.upper()}") or os.getenv("JACK_MODEL") or DEFAULT_MODEL


def tavily_key() -> str | None:
    return os.getenv("TAVILY_API_KEY") or None
