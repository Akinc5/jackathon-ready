"""A tiny event bus so the outside world can watch agents work.

Agents and the pipeline `emit` events ("agent started", "called web_search", ...).
Anything can `subscribe`: today the CLI prints them; later the web UI will
stream them to the browser. Agents never need to know who is listening.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

EventKind = Literal[
    "stage_started",
    "stage_finished",
    "agent_started",
    "agent_thought",  # text the model wrote between tool calls
    "tool_call",
    "tool_result",
    "agent_finished",
    "build_verified",  # our own re-check after a builder says it's done
    "smoke_result",  # the browser test's verdict, per screen
    "output",  # a deliverable is ready (repo, deploy, deck, doc)
    "notice",
    "error",
]


@dataclass
class Event:
    kind: EventKind
    message: str
    agent: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    at: datetime = field(default_factory=datetime.now)


Subscriber = Callable[[Event], None] | Callable[[Event], Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []

    def subscribe(self, fn: Subscriber) -> None:
        self._subscribers.append(fn)

    async def emit(self, event: Event) -> None:
        for fn in self._subscribers:
            result = fn(event)
            if inspect.isawaitable(result):  # subscribers may be sync or async
                await result
