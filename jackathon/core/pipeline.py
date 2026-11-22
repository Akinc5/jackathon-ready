"""The orchestrator: runs every stage in order and saves after each one.

    intake -> research -> strategy -> [you pick] -> plan -> [you approve]
      -> build (backend + frontend in parallel) -> browser test (+1 auto-repair)
      -> [you review: ship it, or ask for changes] -> GitHub -> deploy
      -> pitch deck + app document -> wait for the deploy to go live

State is saved after every stage, so a run can be resumed: finished stages
are skipped. Human decisions go through a `Human` object (terminal or web),
and everything that touches the outside world goes through a `Toolbox`, so
tests can swap in fakes.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ..agents import (
    Agent,
    ArchitectAgent,
    BackendAgent,
    BuilderAgent,
    FrontendAgent,
    IntakeAgent,
    ResearchAgent,
    StorytellerAgent,
    StrategistAgent,
)
from ..deliverables import build_html, render_deck, render_pdf, render_readme
from ..llm import LLM, llm_for
from ..scaffold import scaffold
from ..tools import deploy as hosting, publish as github
from ..tools.checks import CHECKS, CheckResult
from ..tools.smoke import smoke_test
from .events import Event, EventBus
from .models import BuildPlan, BuildRecord, ProjectState
from .store import RunStore

STAGES = {
    "intake": "Reading your flyer",
    "research": "Researching the hackathon",
    "strategy": "Ranking problem statements",
    "choose": "Your pick",
    "plan": "Designing the build plan",
    "build": "Building the backend and frontend",
    "test": "Testing in a real browser",
    "review": "Your review",
    "publish": "Publishing to GitHub",
    "deploy": "Deploying",
    "docs": "Writing the pitch deck and app document",
    "done": "Done",
}


class Human(Protocol):
    """The team's decisions. The CLI asks in the terminal, the web UI in the browser."""

    async def choose(self, state: ProjectState) -> int:
        """Return the index of the problem statement to build."""

    async def approve_plan(self, state: ProjectState) -> str | None:
        """Return None to approve the plan, or text describing changes."""

    async def review_app(self, state: ProjectState) -> str | None:
        """Return None to ship the app, or text describing changes."""


async def verify_with_checks(area: str, project: Path, plan: BuildPlan) -> CheckResult:
    return await CHECKS[area](project, plan)


@dataclass
class Toolbox:
    """Everything that runs real commands or talks to real services. Tests replace these."""

    verify: Callable[..., Awaitable[CheckResult]] = verify_with_checks
    smoke: Callable[..., Awaitable] = smoke_test
    create_repo: Callable[..., Awaitable[str]] = github.create_repo
    push_update: Callable[..., Awaitable[None]] = github.push_update
    deploy: Callable[..., Awaitable[str]] = hosting.deploy_space
    wait_deploy: Callable[..., Awaitable[str]] = hosting.wait_for_space
    render_pdf: Callable[..., Awaitable[Path]] = render_pdf
    can_deploy: Callable[[], bool] = field(default=lambda: hosting.hf_token() is not None)


class Pipeline:
    def __init__(
        self,
        store: RunStore,
        bus: EventBus,
        human: Human,
        llm_factory: Callable[[str], LLM] = llm_for,
        tools: Toolbox | None = None,
    ):
        self.store = store
        self.bus = bus
        self.human = human
        self.llm_factory = llm_factory
        self.tools = tools or Toolbox()

    async def run(self, state: ProjectState) -> ProjectState:
        for stage in (
            self._intake, self._research, self._strategy, self._choose, self._plan, self._build,
            self._test, self._review, self._publish, self._deploy, self._docs, self._finish_deploy,
        ):
            await stage(state)
        await self._stage("done")
        return state

    # --- understanding the hackathon ---------------------------------------------------

    async def _intake(self, state: ProjectState) -> None:
        if state.brief is not None or not state.source.file_name:
            return
        await self._stage("intake")
        file = self.store.run_dir(state.run_id) / "input" / state.source.file_name
        task, images = IntakeAgent.build_task(file, state.source.text)
        agent = self._make(IntakeAgent)
        state.brief = await agent.run(task, images)
        if not state.source.text:
            state.hackathon = state.brief.name
        self._save(state, agent)

    async def _research(self, state: ProjectState) -> None:
        if state.research is not None:
            return
        await self._stage("research")
        agent = self._make(ResearchAgent)
        state.research = await agent.run(ResearchAgent.build_task(state.hackathon, state.track, state.brief))
        self._save(state, agent)

    async def _strategy(self, state: ProjectState) -> None:
        if state.strategy is not None:
            return
        await self._stage("strategy")
        agent = self._make(StrategistAgent)
        state.strategy = await agent.run(StrategistAgent.build_task(state.research, state.track, state.team))
        self._save(state, agent)

    async def _choose(self, state: ProjectState) -> None:
        if state.chosen_index is not None:
            return
        await self._stage("choose")
        index = await self.human.choose(state)
        if not 0 <= index < len(state.strategy.options):
            raise ValueError(f"Choice {index + 1} is out of range.")
        state.chosen_index = index
        self.store.save(state)

    # --- planning and building -------------------------------------------------------------

    async def _plan(self, state: ProjectState) -> None:
        feedback = None
        while not state.plan_approved:
            if state.plan is None or feedback:
                await self._stage("plan")
                agent = self._make(ArchitectAgent)
                task = ArchitectAgent.build_task(state.chosen, state.team, state.research, state.plan, feedback)
                state.plan = await agent.run(task)
                self._save(state, agent)
            feedback = await self.human.approve_plan(state)
            if feedback is None:
                state.plan_approved = True
                self.store.save(state)

    async def _build(self, state: ProjectState) -> None:
        pending = [cls for cls in (BackendAgent, FrontendAgent) if cls.area not in state.builds]
        if not pending:
            return
        scaffold(self._project(state), state.plan)
        await self._stage("build")
        await self._run_builders(state, pending)

    async def _run_builders(self, state: ProjectState, classes: list[type[BuilderAgent]], request: str | None = None) -> None:
        """Run builders in parallel. Each one's result is saved as soon as it finishes."""
        results = await asyncio.gather(
            *(self._build_one(state, cls, request) for cls in classes), return_exceptions=True
        )
        errors = [r for r in results if isinstance(r, BaseException)]
        if errors:
            raise errors[0]

    async def _build_one(self, state: ProjectState, cls: type[BuilderAgent], request: str | None) -> None:
        project = self._project(state)
        agent = cls(project, state.plan, llm=self.llm_factory(cls.role), bus=self.bus)
        report = await agent.run(agent.build_task(request))
        # Don't take the agent's word for it: run the checks ourselves.
        check = await self.tools.verify(cls.area, project, state.plan)
        state.builds[cls.area] = BuildRecord(report=report, verified=check.ok, check_output=check.report)
        await self._emit("build_verified", f"{cls.name}: checks {'passed' if check.ok else 'FAILED'}", agent=cls.name, ok=check.ok)
        self._save(state, agent)

    async def _test(self, state: ProjectState) -> None:
        """Open every screen in a real browser. If something breaks, give the builders one chance to fix it."""
        if state.smoke is not None:
            return
        await self._stage("test")
        smoke = await self._smoke(state)
        if not smoke.ok:
            problems = smoke.failures()
            await self._emit("notice", "Problems found in the browser. Sending them back to the builders.", details=problems)
            fixers = [FrontendAgent] + ([BackendAgent] if "/api/" in problems or smoke.problem else [])
            request = f"The app was opened in a real browser and these problems were found:\n{problems}\nFix them."
            await self._run_builders(state, fixers, request)
            smoke = await self._smoke(state)
        state.smoke = smoke
        self.store.save(state)

    async def _smoke(self, state: ProjectState):
        run_dir = self.store.run_dir(state.run_id)
        smoke = await self.tools.smoke(self._project(state), state.plan, run_dir / "screens", run_dir)
        await self._emit(
            "smoke_result",
            "Every screen works in a real browser" if smoke.ok else "Some screens have problems",
            ok=smoke.ok,
            pages=[p.model_dump() for p in smoke.pages],
            problem=smoke.problem,
        )
        return smoke

    async def _review(self, state: ProjectState) -> None:
        while not state.changes_done:
            await self._stage("review")
            feedback = await self.human.review_app(state)
            if feedback is None:
                state.changes_done = True
                self.store.save(state)
                return
            state.change_requests.append(feedback)
            self.store.save(state)
            await self._stage("build")
            request = f"The team tried the app and asked for these changes:\n{feedback}"
            await self._run_builders(state, [BackendAgent, FrontendAgent], request)
            state.smoke = None
            await self._test(state)

    # --- shipping -------------------------------------------------------------------------

    async def _publish(self, state: ProjectState) -> None:
        if state.outputs.repo_url:
            return
        await self._stage("publish")
        project = self._project(state)
        (project / "README.md").write_text(render_readme(state, None), encoding="utf-8")
        try:
            state.outputs.repo_url = await self.tools.create_repo(project, state.plan.product_name, state.plan.one_liner)
            await self._emit("output", "GitHub repo created", key="repo_url", url=state.outputs.repo_url)
        except github.PublishError as e:
            await self._emit("error", str(e))
        self.store.save(state)

    async def _deploy(self, state: ProjectState) -> None:
        if state.outputs.space_id:
            return
        await self._stage("deploy")
        if not self.tools.can_deploy():
            state.outputs.deploy_status = "skipped: add HF_TOKEN to .env to deploy"
            await self._emit("notice", "Deploy skipped: HF_TOKEN isn't set in .env.")
            self.store.save(state)
            return
        name = github.slugify(state.plan.product_name)
        try:
            state.outputs.space_id = await self.tools.deploy(
                self._project(state), name, state.plan.product_name, state.plan.one_liner, state.outputs.repo_url
            )
            state.outputs.deploy_url = hosting.space_url(state.outputs.space_id)
            state.outputs.deploy_status = "building"
            await self._emit("output", "Uploaded. Hugging Face is building the app", key="deploy_url", url=state.outputs.deploy_url)
        except hosting.DeployError as e:
            state.outputs.deploy_status = f"failed: {e}"
            await self._emit("error", str(e))
        self.store.save(state)

    async def _docs(self, state: ProjectState) -> None:
        if state.outputs.deck and state.outputs.doc:
            return
        await self._stage("docs")
        if state.pitch is None:
            agent = self._make(StorytellerAgent)
            state.pitch = await agent.run(StorytellerAgent.build_task(state))
            self._save(state, agent)

        run_dir, project = self.store.run_dir(state.run_id), self._project(state)
        docs = project / "docs"
        shots = [run_dir / p.screenshot for p in (state.smoke.pages if state.smoke else []) if p.ok and p.screenshot]
        shots = [s for s in shots if s.exists()]
        (docs / "screens").mkdir(parents=True, exist_ok=True)
        for s in shots:
            shutil.copy(s, docs / "screens" / s.name)

        deck = render_deck(state, shots, docs / "pitch-deck.pptx")
        state.outputs.deck = deck.relative_to(run_dir).as_posix()
        await self._emit("output", "Pitch deck ready", key="deck")
        pdf = await self.tools.render_pdf(build_html(state, run_dir), docs / "app-document.pdf")
        state.outputs.doc = pdf.relative_to(run_dir).as_posix()
        await self._emit("output", "App document ready", key="doc")

        readme_shot = f"docs/screens/{shots[0].name}" if shots else None
        (project / "README.md").write_text(render_readme(state, readme_shot), encoding="utf-8")
        if state.outputs.repo_url:
            try:
                await self.tools.push_update(project, "Add pitch deck, app document and screenshots")
            except github.PublishError as e:
                await self._emit("error", str(e))
        self.store.save(state)

    async def _finish_deploy(self, state: ProjectState) -> None:
        if state.outputs.deploy_status != "building":
            return
        await self._stage("deploy")
        await self._emit("notice", "Waiting for Hugging Face to finish building (usually 3-8 minutes)...")
        state.outputs.deploy_status = await self.tools.wait_deploy(state.outputs.space_id)
        ok = state.outputs.deploy_status == "running"
        await self._emit("output" if ok else "error", f"Deploy {state.outputs.deploy_status}", key="deploy_url", url=state.outputs.deploy_url)
        self.store.save(state)

    # --- helpers ----------------------------------------------------------------------------

    def _project(self, state: ProjectState) -> Path:
        return self.store.project_dir(state.run_id)

    def _make(self, agent_cls: type[Agent]) -> Agent:
        return agent_cls(llm=self.llm_factory(agent_cls.role), bus=self.bus)

    def _save(self, state: ProjectState, agent: Agent) -> None:
        usage = state.usage.setdefault(agent.role, {"input_tokens": 0, "output_tokens": 0})
        usage["input_tokens"] += agent.usage.input_tokens
        usage["output_tokens"] += agent.usage.output_tokens
        self.store.save(state)

    async def _stage(self, stage: str) -> None:
        await self.bus.emit(Event("stage_started", STAGES[stage], data={"stage": stage}))

    async def _emit(self, kind, message: str, agent: str | None = None, **data) -> None:
        await self.bus.emit(Event(kind, message, agent=agent, data=data))
