"""Writes the generated project's README.md (what GitHub shows on the repo page)."""

from __future__ import annotations

from ..core.models import ProjectState


def render_readme(state: ProjectState, screenshot: str | None) -> str:
    plan, idea, pitch, links = state.plan, state.chosen, state.pitch, state.outputs
    tagline = pitch.tagline if pitch else plan.one_liner
    lines = [f"# {plan.product_name}", "", f"**{tagline}**", ""]
    badges = []
    if links.deploy_url:
        badges.append(f"[Live demo]({links.deploy_url})")
    if links.deck:
        badges.append("[Pitch deck](docs/pitch-deck.pptx)")
    if links.doc:
        badges.append("[App document](docs/app-document.pdf)")
    if badges:
        lines += [" · ".join(badges), ""]
    if screenshot:
        lines += [f"![{plan.product_name}]({screenshot})", ""]
    lines += [
        f"Built in {state.team.hours} hours for **{state.hackathon}** ({idea.track} track).",
        "",
        "## The problem",
        "",
        idea.problem,
        "",
        "## What it does",
        "",
        idea.solution,
        "",
        *[f"- {f}" for f in idea.key_features],
        "",
        "## Run it locally",
        "",
        "```bash",
        "# backend (http://localhost:8000/docs)",
        "cd backend",
        "python -m venv .venv && .venv/Scripts/activate   # macOS/Linux: source .venv/bin/activate",
        "pip install -r requirements.txt",
        "uvicorn app.main:app --reload",
        "",
        "# frontend (http://localhost:5173), in a second terminal",
        "cd frontend",
        "npm install",
        "npm run dev",
        "```",
        "",
        "Or run everything in one container: `docker build -t app . && docker run -p 7860:7860 app`",
        "",
        "## API",
        "",
        "| Method | Path | What it does |",
        "|---|---|---|",
        *[f"| {e.method} | `{e.path}` | {e.summary} |" for e in plan.endpoints],
        "",
        "## Tech stack",
        "",
        "FastAPI (Python) · React 18 + Vite · Tailwind CSS"
        + "".join(f" · {p}" for p in [*plan.python_packages, *plan.npm_packages]),
        "",
        "---",
        "Planned, built, tested and shipped with [Jackathon](https://github.com/Akinc5/jackathon-ready).",
        "",
    ]
    return "\n".join(lines)
