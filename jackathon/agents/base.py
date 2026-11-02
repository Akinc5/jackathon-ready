"""The agent loop that every Jackathon agent shares.

An agent is: a system prompt + some tools + a Pydantic `output_model`.
`run(task)` loops:

    ask the model -> it calls tools -> we run them and send back results -> repeat

until the model calls the special `submit_result` tool with an answer that
matches `output_model`. That's how we get typed, validated results out of
any provider: if the answer doesn't fit the schema, we send back the
validation errors and the model fixes them.
"""

from __future__ import annotations

import asyncio
from typing import ClassVar, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from ..core.events import Event, EventBus
from ..llm import LLM, Message, ToolCall, ToolSpec, Usage, llm_for
from ..tools import Tool, ToolError, json_schema

OutputT = TypeVar("OutputT", bound=BaseModel)

SUBMIT_TOOL = "submit_result"


class AgentError(RuntimeError):
    pass


class Agent(Generic[OutputT]):
    # Subclasses fill these in.
    name: ClassVar[str] = "Agent"  # shown in the UI
    role: ClassVar[str] = "default"  # picks the model: JACK_MODEL_<ROLE>
    output_model: ClassVar[type[BaseModel]]
    max_steps: ClassVar[int] = 20  # one step = one model call

    def __init__(self, llm: LLM | None = None, bus: EventBus | None = None):
        self.llm = llm or llm_for(self.role)
        self.bus = bus or EventBus()
        self.usage = Usage()

    # --- override these -------------------------------------------------------

    def system_prompt(self) -> str:
        raise NotImplementedError

    def tools(self) -> list[Tool]:
        return []

    # --- the loop -------------------------------------------------------------

    async def run(self, task: str) -> OutputT:
        tools = {t.name: t for t in self.tools()}
        specs = [t.spec() for t in tools.values()] + [self._submit_spec()]
        system = self.system_prompt() + _SUBMIT_RULES
        messages = [Message.user(task)]

        await self._emit("agent_started", f"{self.name} started", model=self.llm.label)

        for step in range(1, self.max_steps + 1):
            if step == self.max_steps:
                messages.append(Message.user(_LAST_STEP))

            response = await self.llm.chat(system, messages, specs)
            self.usage.input_tokens += response.usage.input_tokens
            self.usage.output_tokens += response.usage.output_tokens
            reply = response.message
            messages.append(reply)

            if reply.content.strip():
                await self._emit("agent_thought", reply.content.strip())

            if not reply.tool_calls:
                # The model stopped without submitting. Nudge it to finish properly.
                messages.append(Message.user(_NUDGE))
                continue

            # Run every tool the model asked for, in parallel.
            outcomes = await asyncio.gather(*(self._handle(c, tools) for c in reply.tool_calls))
            for result_msg, output in outcomes:
                if output is not None:
                    await self._emit(
                        "agent_finished",
                        f"{self.name} finished in {step} steps",
                        steps=step,
                        input_tokens=self.usage.input_tokens,
                        output_tokens=self.usage.output_tokens,
                    )
                    return output
            # All results for one turn go back together.
            messages.extend(result_msg for result_msg, _ in outcomes)

        raise AgentError(f"{self.name} did not submit a result within {self.max_steps} steps.")

    async def _handle(self, call: ToolCall, tools: dict[str, Tool]) -> tuple[Message, OutputT | None]:
        """Run one tool call. Returns the result message, plus the final output if this was a valid submit."""
        if call.parse_error:
            return Message.tool_result(call.id, call.parse_error, is_error=True), None

        if call.name == SUBMIT_TOOL:
            try:
                output = self.output_model.model_validate(call.arguments)
            except ValidationError as e:
                await self._emit("tool_result", "Result didn't match the schema; asking for a fix", error=True)
                return Message.tool_result(call.id, f"Fix these problems and submit again:\n{e}", is_error=True), None
            return Message.tool_result(call.id, "Accepted."), output  # type: ignore[return-value]

        tool = tools.get(call.name)
        if tool is None:
            return Message.tool_result(call.id, f"Unknown tool {call.name!r}.", is_error=True), None

        await self._emit("tool_call", _describe(call), tool=call.name, arguments=call.arguments)
        try:
            text = await tool.run(call.arguments)
        except ToolError as e:
            await self._emit("tool_result", str(e), tool=call.name, error=True)
            return Message.tool_result(call.id, str(e), is_error=True), None
        except Exception as e:  # a bug in a tool shouldn't kill the whole run
            await self._emit("tool_result", f"{type(e).__name__}: {e}", tool=call.name, error=True)
            return Message.tool_result(call.id, f"Tool crashed: {type(e).__name__}: {e}", is_error=True), None

        await self._emit("tool_result", _first_line(text), tool=call.name, chars=len(text))
        return Message.tool_result(call.id, text), None

    def _submit_spec(self) -> ToolSpec:
        return ToolSpec(
            SUBMIT_TOOL,
            "Submit your final result. Call this exactly once, when you are done.",
            json_schema(self.output_model),
        )

    async def _emit(self, kind, message: str, **data) -> None:
        await self.bus.emit(Event(kind=kind, message=message, agent=self.name, data=data))


_SUBMIT_RULES = f"""

When you have finished, call the `{SUBMIT_TOOL}` tool with your complete result.
That is the only way to deliver your work: text you write outside it is not saved."""

_NUDGE = f"Continue your work. When you're done, call `{SUBMIT_TOOL}` with your final result."

_LAST_STEP = f"You are out of steps. Call `{SUBMIT_TOOL}` now with your best result so far."


def _describe(call: ToolCall) -> str:
    """A one-line summary of a tool call, for display."""
    first_arg = next(iter(call.arguments.values()), "")
    return f"{call.name}: {first_arg}" if first_arg else call.name


def _first_line(text: str, limit: int = 120) -> str:
    line = text.strip().splitlines()[0] if text.strip() else "(empty)"
    return line if len(line) <= limit else line[: limit - 1] + "…"
