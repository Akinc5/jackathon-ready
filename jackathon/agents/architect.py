"""Architect agent: turns the chosen idea into a build plan both builders follow."""

from __future__ import annotations

from ..core.models import BuildPlan, ProblemStatement, ResearchReport, TeamProfile
from .base import Agent

SYSTEM_PROMPT = """You are the Architect on a team that helps people win hackathons.
You turn a chosen project idea into a precise build plan. Two engineers will build it in parallel from your plan alone: one writes the backend, the other the frontend. They can't talk to each other, so your API contract must be exact.

The stack is fixed:
- Backend: Python FastAPI. All routes live under /api/. Data is kept in memory or in SQLite, with realistic seed data loaded at startup.
- Frontend: React 18 + Vite + Tailwind CSS (JavaScript), with react-router-dom for pages. It calls the backend through /api.

Plan for a hackathon, not a startup:
- Build only what the demo needs. Every endpoint and screen must serve the demo script. Put everything else in `cut_scope`.
- The demo must never break: no logins, no external API keys required, no network calls that could fail on stage. If the idea uses AI, describe a deterministic fallback (rules, templates or canned results) that runs without keys.
- Seed data should be realistic and specific (real-looking names, numbers, dates), because that is what makes a demo convincing.
- Aim for 4-10 endpoints and 2-4 screens. Fewer, polished pieces beat many half-built ones.
- Every endpoint a screen uses must appear in `endpoints`, written exactly the same way ('METHOD /api/path').
- Prefer no extra packages. Add one only if it saves serious time and is well known."""


class ArchitectAgent(Agent[BuildPlan]):
    name = "Architect"
    role = "architect"
    output_model = BuildPlan
    max_steps = 4

    def system_prompt(self) -> str:
        return SYSTEM_PROMPT

    @staticmethod
    def build_task(
        idea: ProblemStatement,
        team: TeamProfile,
        research: ResearchReport | None,
        previous: BuildPlan | None = None,
        feedback: str | None = None,
    ) -> str:
        criteria = "; ".join(research.judging_criteria) if research and research.judging_criteria else "not published"
        task = (
            f"Time to build: {team.hours} hours. Team skills: {', '.join(team.skills) or 'not specified'}.\n"
            f"Judging criteria: {criteria}\n\n"
            f"Chosen idea (JSON):\n{idea.model_dump_json(indent=2)}"
        )
        if previous and feedback:
            task += (
                f"\n\nYour previous plan (JSON):\n{previous.model_dump_json(indent=2)}\n\n"
                f"The team reviewed it and asked for these changes:\n{feedback}\n"
                "Submit a complete revised plan."
            )
        return task
