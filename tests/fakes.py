"""A fake LLM that replays scripted replies, so tests run offline and free."""

from __future__ import annotations

from jackathon.llm import LLM, LLMResponse, Message, ToolCall, ToolSpec, Usage


class ScriptedLLM(LLM):
    provider = "fake"

    def __init__(self, replies: list[Message], model: str = "scripted"):
        self.model = model
        self.replies = list(replies)
        self.calls: list[list[Message]] = []  # what the agent sent each time

    async def chat(self, system: str, messages: list[Message], tools: list[ToolSpec] | None = None) -> LLMResponse:
        self.calls.append(list(messages))
        if not self.replies:
            raise AssertionError("ScriptedLLM ran out of replies")
        return LLMResponse(self.replies.pop(0), stop_reason="end_turn", usage=Usage(10, 5))


def call(name: str, call_id: str = "c1", **arguments) -> Message:
    """An assistant reply that calls one tool."""
    return Message(role="assistant", tool_calls=[ToolCall(call_id, name, arguments)])


def say(text: str) -> Message:
    return Message(role="assistant", content=text)
