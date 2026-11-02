"""The provider-neutral vocabulary every agent speaks.

Agents never import `anthropic` or `openai` directly. They build `Message`s,
describe tools with `ToolSpec`s, and call `LLM.chat(...)`. Each provider
adapter translates these into its own API format and back.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["user", "assistant", "tool"]


class LLMError(RuntimeError):
    """Raised when a provider call fails in a way the agent can't recover from."""


@dataclass
class ToolCall:
    """The model asking us to run a tool."""

    id: str
    name: str
    arguments: dict[str, Any]
    # Set when the provider sent arguments we couldn't parse; the agent
    # reports it back to the model instead of running the tool.
    parse_error: str | None = None


@dataclass
class Message:
    """One turn in a conversation.

    - role="user":      text from us to the model
    - role="assistant": the model's reply (text and/or tool_calls)
    - role="tool":      the result of one tool call (tool_call_id says which)
    """

    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    is_error: bool = False
    # The provider's own copy of an assistant reply. Some providers (Anthropic)
    # need their reply sent back exactly as they wrote it, including hidden
    # "thinking" blocks, so the adapter keeps it here and replays it.
    raw: Any = None
    raw_provider: str | None = None

    @classmethod
    def user(cls, text: str) -> Message:
        return cls(role="user", content=text)

    @classmethod
    def tool_result(cls, call_id: str, text: str, is_error: bool = False) -> Message:
        return cls(role="tool", content=text, tool_call_id=call_id, is_error=is_error)


@dataclass
class ToolSpec:
    """A tool as the model sees it: a name, what it does, and a JSON schema for its arguments."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class LLMResponse:
    message: Message  # always role="assistant"
    stop_reason: str
    usage: Usage = field(default_factory=Usage)


class LLM(ABC):
    """Base class for every provider adapter."""

    provider: str
    model: str

    @abstractmethod
    async def chat(
        self,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
    ) -> LLMResponse:
        """Send the conversation and return the model's next reply."""

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"
