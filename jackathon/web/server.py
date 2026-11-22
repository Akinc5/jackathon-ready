"""The web interface: one input bar in, a live view of the whole build out.

It runs the exact same Pipeline as the CLI. Two pieces make it work in a browser:

- Every pipeline Event is kept in a list and streamed to the page with
  Server-Sent Events (/api/runs/<id>/events). Opening the page later replays
  the history, so a refresh never loses anything.
- `WebHuman` answers the pipeline's questions (pick an idea, approve the plan,
  review the app). Each question waits on an asyncio Future that the
  /answer endpoint resolves when you click a button.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ..config import RUNS_DIR, model_for
from ..core.events import Event, EventBus
from ..core.models import ProjectState, TeamProfile
from ..core.pipeline import Pipeline
from ..core.store import RunStore
from ..llm import LLMError, create_llm
from ..tools.smoke import AppServer

STATIC = Path(__file__).parent / "static"
ROLES = ["intake", "research", "strategist", "architect", "backend", "frontend", "storyteller"]


class RunSession:
    """One pipeline running in the background, plus everything the page needs to follow it."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.events: list[dict[str, Any]] = []
        self.listeners: set[asyncio.Queue] = set()
        self.question: dict[str, Any] | None = None
        self.answer: asyncio.Future | None = None
        self.status = "running"  # running | waiting | done | error
        self.task: asyncio.Task | None = None

    def publish(self, item: dict[str, Any]) -> None:
        self.events.append(item)
        for queue in self.listeners:
            queue.put_nowait(item)

    def on_event(self, e: Event) -> None:
        item = asdict(e)
        item["at"] = e.at.isoformat(timespec="seconds")
        self.publish(item)


class WebHuman:
    """Answers the pipeline's questions with whatever the user clicks in the browser."""

    def __init__(self, session: RunSession, store: RunStore, preview=AppServer):
        self.session = session
        self.store = store
        self.preview = preview  # tests pass a stand-in so they don't run npm

    async def _ask(self, kind: str, **extra) -> dict[str, Any]:
        s = self.session
        s.answer = asyncio.get_running_loop().create_future()
        s.question = {"kind": kind, **extra}
        s.status = "waiting"
        s.publish({"kind": "question", "message": kind, "data": s.question})
        try:
            return await s.answer
        finally:
            s.question, s.answer, s.status = None, None, "running"
            s.publish({"kind": "answered", "message": kind, "data": {}})

    async def choose(self, state: ProjectState) -> int:
        return int((await self._ask("choose"))["index"])

    async def approve_plan(self, state: ProjectState) -> str | None:
        return (await self._ask("plan")).get("feedback") or None

    async def review_app(self, state: ProjectState) -> str | None:
        # Run the built app so the user can click around before shipping it.
        async with self.preview(self.store.project_dir(state.run_id)) as preview:
            answer = await self._ask("review", preview_url=None if preview.error else preview.url)
        return answer.get("feedback") or None


def create_app(store: RunStore | None = None, pipeline_factory=None, preview=AppServer, check_models: bool = True) -> FastAPI:
    store = store or RunStore(RUNS_DIR)
    sessions: dict[str, RunSession] = {}
    app = FastAPI(title="Jackathon")

    def start(state: ProjectState) -> RunSession:
        session = RunSession(state.run_id)
        sessions[state.run_id] = session
        bus = EventBus()
        bus.subscribe(session.on_event)
        human = WebHuman(session, store, preview)
        pipeline = pipeline_factory(store, bus, human) if pipeline_factory else Pipeline(store, bus, human)

        async def run() -> None:
            try:
                await pipeline.run(state)
                session.status = "done"
                session.publish({"kind": "finished", "message": "All done", "data": {}})
            except Exception as e:  # show any failure on the page; progress is saved, so it can resume
                session.status = "error"
                session.publish({"kind": "failed", "message": f"{type(e).__name__}: {e}", "data": {}})

        session.task = asyncio.create_task(run())
        return session

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.post("/api/runs")
    async def create_run(text: str = Form(""), file: UploadFile | None = File(None)) -> dict:
        text = text.strip()
        if not text and not file:
            raise HTTPException(400, "Type a hackathon name, or attach a flyer or PDF.")
        for role in ROLES if check_models else []:
            try:
                create_llm(model_for(role))
            except (LLMError, ValueError) as e:
                raise HTTPException(400, f"{e} (run: jackathon doctor)") from e
        data = await file.read() if file else None
        try:
            state = store.create(text, None, TeamProfile(), file.filename if file else None, data)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        start(state)
        return {"run_id": state.run_id}

    @app.get("/api/runs")
    def list_runs() -> list[dict]:
        return [
            {"run_id": s.run_id, "hackathon": s.hackathon, "created_at": s.created_at,
             "product": s.plan.product_name if s.plan else None, "live": s.outputs.deploy_url}
            for s in store.list()[:12]
        ]

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        state = _load(store, run_id)
        session = sessions.get(run_id)
        return {
            "state": state.model_dump(mode="json"),
            "status": session.status if session else ("done" if state.outputs.deck else "stopped"),
            "question": session.question if session else None,
        }

    @app.post("/api/runs/{run_id}/resume")
    def resume(run_id: str) -> dict:
        session = sessions.get(run_id)
        if session and session.status in ("running", "waiting"):
            return {"ok": True}
        start(_load(store, run_id))
        return {"ok": True}

    @app.post("/api/runs/{run_id}/answer")
    def answer(run_id: str, body: dict) -> dict:
        session = sessions.get(run_id)
        if not session or not session.answer or session.answer.done():
            raise HTTPException(409, "There's no open question for this run.")
        session.answer.set_result(body)
        return {"ok": True}

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str) -> StreamingResponse:
        _load(store, run_id)
        session = sessions.get(run_id)

        async def stream():
            if session is None:
                yield _sse({"kind": "idle", "message": "Not running", "data": {}})
                return
            queue: asyncio.Queue = asyncio.Queue()
            history = list(session.events)
            session.listeners.add(queue)
            try:
                for item in history:
                    yield _sse(item)
                while True:
                    try:
                        yield _sse(await asyncio.wait_for(queue.get(), timeout=15))
                    except TimeoutError:
                        yield ": keep-alive\n\n"  # stops proxies and browsers from closing an idle stream
            finally:
                session.listeners.discard(queue)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/api/runs/{run_id}/file/{path:path}")
    def run_file(run_id: str, path: str) -> FileResponse:
        """Download a file from the run folder (deck, PDF, screenshots), never anything outside it."""
        root = store.run_dir(_load(store, run_id).run_id).resolve()
        file = (root / path).resolve()
        allowed = ("screens/", "project/docs/")
        if not file.is_relative_to(root) or not file.is_file() or not path.startswith(allowed):
            raise HTTPException(404)
        return FileResponse(file, filename=file.name if path.startswith("project/docs/") and file.suffix != ".png" else None)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def _load(store: RunStore, run_id: str) -> ProjectState:
    try:
        return store.load(run_id)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e


def _sse(item: dict[str, Any]) -> str:
    return f"data: {json.dumps(item, default=str)}\n\n"
