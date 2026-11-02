"""Saves and loads runs as JSON: runs/<run_id>/state.json."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .models import ProjectState, TeamProfile


class RunStore:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir

    def create(self, hackathon: str, track: str | None, team: TeamProfile) -> ProjectState:
        now = datetime.now()
        slug = re.sub(r"[^a-z0-9]+", "-", hackathon.lower()).strip("-")[:40] or "run"
        state = ProjectState(
            run_id=f"{slug}-{now:%Y%m%d-%H%M%S}",
            created_at=now.isoformat(timespec="seconds"),
            hackathon=hackathon,
            track=track,
            team=team,
        )
        self.save(state)
        return state

    def save(self, state: ProjectState) -> None:
        path = self._path(state.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file first, so a crash mid-write can't corrupt the run.
        tmp = path.with_suffix(".tmp")
        tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)

    def load(self, run_id: str) -> ProjectState:
        path = self._path(run_id)
        if not path.exists():
            raise FileNotFoundError(f"No run called {run_id!r} in {self.base_dir}/")
        return ProjectState.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self) -> list[ProjectState]:
        if not self.base_dir.exists():
            return []
        states = [self.load(p.parent.name) for p in self.base_dir.glob("*/state.json")]
        return sorted(states, key=lambda s: s.created_at, reverse=True)

    def _path(self, run_id: str) -> Path:
        return self.base_dir / run_id / "state.json"
