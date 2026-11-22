"""Storyteller agent: writes the words for the pitch deck and the app document.

It only writes content. Code turns that content into a .pptx, a PDF and a
README (see jackathon/deliverables/), so the layout is always consistent.
"""

from __future__ import annotations

from ..core.models import PitchContent, ProjectState
from .base import Agent

SYSTEM_PROMPT = """You are the Storyteller on a hackathon team. The app is built. Now you write the pitch and the documentation that win over judges.

Pitch rules:
- Judges decide in minutes. Lead with a sharp, concrete problem (a number, a person, a moment), then show the solution.
- Every slide has one idea. Bullets are short and specific; no buzzword soup.
- Speaker notes are what the presenter actually says, natural and confident.
- Tie "why it wins" to the hackathon's judging criteria and track.
- Turn on show_screenshot for the solution and demo slides.

Document rules:
- Explain the app to someone who wasn't in the room: what it does, how it works, how to use it.
- Plain language. Describe only what was actually built. Don't invent features."""


class StorytellerAgent(Agent[PitchContent]):
    name = "Storyteller"
    role = "storyteller"
    output_model = PitchContent
    max_steps = 4

    def system_prompt(self) -> str:
        return SYSTEM_PROMPT

    @staticmethod
    def build_task(state: ProjectState) -> str:
        idea, plan = state.chosen, state.plan
        criteria = "; ".join(state.research.judging_criteria) if state.research else ""
        built = "\n".join(f"- {area}: {rec.report.summary}" for area, rec in state.builds.items())
        return (
            f"Hackathon: {state.hackathon}. Track: {idea.track}. Judging criteria: {criteria or 'not published'}.\n"
            f"Time to build: {state.team.hours} hours.\n\n"
            f"The idea (JSON):\n{idea.model_dump_json(indent=2)}\n\n"
            f"The build plan (JSON):\n{plan.model_dump_json(indent=2)}\n\n"
            f"What the engineers say they built:\n{built}"
        )
