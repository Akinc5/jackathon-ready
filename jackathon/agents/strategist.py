"""Strategist agent: turns research into ranked, scored problem statements."""

from __future__ import annotations

from ..core.models import ResearchReport, StrategyReport, TeamProfile
from .base import Agent

SYSTEM_PROMPT = """You are the Strategist on a team that helps people win hackathons.
You think like an experienced hackathon judge. You know judges see dozens of projects, remember the few with a sharp problem and a working, memorable demo, and quickly forget generic ideas.

From the research you are given, propose 5 distinct problem statements the team could build, then score each one honestly.

Rules:
- Every option must target a real track and speak directly to the judging criteria.
- Each must be buildable by this team, with these skills, within the time limit. Cut scope rather than promise too much.
- Avoid generic ideas (yet another chatbot, to-do app or dashboard) unless there is a sharp, specific twist.
- Learn from past winners, but don't copy them. Judges notice repeats.
- Use sponsor technology where it fits naturally, because it can earn extra prizes.
- Each option needs a concrete "wow moment" for the demo.
- Make the 5 options genuinely different from each other (different tracks, users or approaches).

Scoring: rate each criterion from 1 to 10. Be strict: 9-10 should be rare, and a typical solid idea scores 5-7. The total score is calculated for you, so don't add one."""


class StrategistAgent(Agent[StrategyReport]):
    name = "Strategist"
    role = "strategist"
    output_model = StrategyReport
    max_steps = 4  # no tools: think, then submit

    def system_prompt(self) -> str:
        return SYSTEM_PROMPT

    @staticmethod
    def build_task(research: ResearchReport, track: str | None, team: TeamProfile) -> str:
        skills = ", ".join(team.skills) if team.skills else "not specified (assume a typical student team)"
        size = str(team.team_size) if team.team_size else "not specified"
        return (
            f"Track preference: {track or 'none - consider every track'}\n"
            f"Team skills: {skills}\n"
            f"Team size: {size}\n"
            f"Time to build: {team.hours} hours\n\n"
            f"Research report (JSON):\n{research.model_dump_json(indent=2)}"
        )

    async def run(self, task: str) -> StrategyReport:
        report = await super().run(task)
        # Rank by the weighted total we compute ourselves.
        report.options.sort(key=lambda o: o.scores.total, reverse=True)
        return report
