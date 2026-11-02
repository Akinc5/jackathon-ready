import asyncio

import pytest
from pydantic import BaseModel

from jackathon.agents.base import SUBMIT_TOOL, Agent, AgentError
from jackathon.core.events import EventBus
from jackathon.tools import Tool

from .fakes import ScriptedLLM, call, say


class Answer(BaseModel):
    value: int


class EchoArgs(BaseModel):
    text: str


async def echo(args: EchoArgs) -> str:
    return f"echo: {args.text}"


class EchoAgent(Agent[Answer]):
    name = "Echo"
    output_model = Answer
    max_steps = 5

    def system_prompt(self) -> str:
        return "test"

    def tools(self):
        return [Tool("echo", "Echo text back", EchoArgs, echo)]


def run(agent, task="go"):
    return asyncio.run(agent.run(task))


def test_tool_call_then_submit():
    llm = ScriptedLLM([call("echo", text="hi"), call(SUBMIT_TOOL, "c2", value=42)])
    events = []
    bus = EventBus()
    bus.subscribe(events.append)

    result = run(EchoAgent(llm=llm, bus=bus))

    assert result == Answer(value=42)
    # The second model call saw the tool's output.
    tool_msg = llm.calls[1][-1]
    assert tool_msg.role == "tool" and tool_msg.content == "echo: hi"
    kinds = [e.kind for e in events]
    assert kinds[0] == "agent_started" and kinds[-1] == "agent_finished"
    assert "tool_call" in kinds


def test_invalid_submit_gets_errors_back_and_retries():
    llm = ScriptedLLM([call(SUBMIT_TOOL, value="not a number"), call(SUBMIT_TOOL, "c2", value=7)])
    assert run(EchoAgent(llm=llm)).value == 7
    error_msg = llm.calls[1][-1]
    assert error_msg.is_error and "Fix these problems" in error_msg.content


def test_nudges_when_model_stops_without_submitting():
    llm = ScriptedLLM([say("I think the answer is 3."), call(SUBMIT_TOOL, value=3)])
    assert run(EchoAgent(llm=llm)).value == 3
    nudge = llm.calls[1][-1]
    assert nudge.role == "user" and SUBMIT_TOOL in nudge.content


def test_unknown_tool_and_tool_errors_do_not_crash():
    llm = ScriptedLLM([call("nope"), call("echo", text=123), call(SUBMIT_TOOL, value=1)])
    assert run(EchoAgent(llm=llm)).value == 1
    assert llm.calls[1][-1].is_error  # unknown tool
    assert llm.calls[2][-1].is_error  # bad arguments for echo


def test_gives_up_after_max_steps():
    llm = ScriptedLLM([say("thinking...")] * 5)
    with pytest.raises(AgentError):
        run(EchoAgent(llm=llm))
