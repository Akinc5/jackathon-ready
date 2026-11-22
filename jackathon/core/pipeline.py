"""The orchestrator: runs the agents in order and saves after every stage.

Because state is saved after each stage, a run can be resumed: stages that
already have results are skipped. Phase 2+ agents (Architect, Builders, ...)
will be added here as new stages.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from ..agents import Agent, ResearchAgent, StrategistAgent
from ..llm import LLM, llm_for
from .events import Event, EventBus
from .models import ProjectState
from .store import RunStore

# Shows a human the research and ranked options, and returns the index they pick.
# The CLI asks in the terminal; the web UI will ask in the browser.
ChooseFn = Callable[[ProjectState], Awaitable[int]]


class Pipeline:
    def __init__(
        self,
        store: RunStore,
        bus: EventBus,
        choose: ChooseFn,
        llm_factory: Callable[[str], LLM] = llm_for,
    ):
        self.store = store
        self.bus = bus
        self.choose = choose
        self.llm_factory = llm_factory  # tests pass a fake here

    async def run(self, state: ProjectState) -> ProjectState:
        if state.research is None:
            await self._stage_started("research", "Researching the hackathon")
            agent = self._make(ResearchAgent)
            state.research = await agent.run(ResearchAgent.build_task(state.hackathon, state.track))
            self._finish(state, agent)
            await self._stage_finished("research")

        if state.strategy is None:
            await self._stage_started("strategy", "Ranking problem statements")
            agent = self._make(StrategistAgent)
            task = StrategistAgent.build_task(state.research, state.track, state.team)
            state.strategy = await agent.run(task)
            self._finish(state, agent)
            await self._stage_finished("strategy")

        if state.chosen_index is None:
            # Human checkpoint: the team, not the AI, decides what to build.
            index = await self.choose(state)
            if not 0 <= index < len(state.strategy.options):
                raise ValueError(f"Choice {index + 1} is out of range.")
            state.chosen_index = index
            self.store.save(state)

        return state

    def _make(self, agent_cls: type[Agent]) -> Agent:
        return agent_cls(llm=self.llm_factory(agent_cls.role), bus=self.bus)

    def _finish(self, state: ProjectState, agent: Agent) -> None:
        state.usage[agent.role] = {
            "input_tokens": agent.usage.input_tokens,
            "output_tokens": agent.usage.output_tokens,
        }
        self.store.save(state)

    async def _stage_started(self, stage: str, message: str) -> None:
        await self.bus.emit(Event("stage_started", message, data={"stage": stage}))

    async def _stage_finished(self, stage: str) -> None:
        await self.bus.emit(Event("stage_finished", f"{stage} done", data={"stage": stage}))
