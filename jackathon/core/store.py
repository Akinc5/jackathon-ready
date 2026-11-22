"""Saves and loads runs as JSON. Each run gets a folder:

    runs/<run_id>/state.json     everything we know about the run
    runs/<run_id>/input/         the flyer or PDF you uploaded
    runs/<run_id>/screens/       screenshots from the browser test
    runs/<run_id>/project/       the generated app (plus docs/ with the deck and PDF)
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .models import ProjectState, RunInput, TeamProfile

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


class RunStore:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir

    def create(
        self,
        hackathon: str,
        track: str | None,
        team: TeamProfile,
        file_name: str | None = None,
        file_bytes: bytes | None = None,
    ) -> ProjectState:
        """Start a run from a name, or from an uploaded flyer/PDF (the name is then read from it)."""
        now = datetime.now()
        label = hackathon or Path(file_name or "").stem
        slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:40] or "run"
        state = ProjectState(
            run_id=f"{slug}-{now:%Y%m%d-%H%M%S}",
            created_at=now.isoformat(timespec="seconds"),
            hackathon=hackathon or label,
            track=track,
            team=team,
            source=RunInput(text=hackathon),
        )
        if file_name and file_bytes is not None:
            if len(file_bytes) > MAX_UPLOAD_BYTES:
                raise ValueError("That file is over 20 MB. Upload a smaller image or PDF.")
            safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(file_name).name)  # never trust upload names
            (self.run_dir(state.run_id) / "input").mkdir(parents=True, exist_ok=True)
            (self.run_dir(state.run_id) / "input" / safe_name).write_bytes(file_bytes)
            state.source.file_name = safe_name
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
        if not re.fullmatch(r"[a-z0-9-]+", run_id) or not path.exists():
            raise FileNotFoundError(f"No run called {run_id!r} in {self.base_dir}/")
        return ProjectState.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self) -> list[ProjectState]:
        if not self.base_dir.exists():
            return []
        states = [self.load(p.parent.name) for p in self.base_dir.glob("*/state.json")]
        return sorted(states, key=lambda s: s.created_at, reverse=True)

    def run_dir(self, run_id: str) -> Path:
        return self.base_dir / run_id

    def project_dir(self, run_id: str) -> Path:
        """Where the generated app's code lives."""
        return self.base_dir / run_id / "project"

    def _path(self, run_id: str) -> Path:
        return self.base_dir / run_id / "state.json"
