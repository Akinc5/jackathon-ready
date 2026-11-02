"""The data that flows between agents.

These Pydantic models do double duty: they are the JSON schemas the agents
must fill in (field descriptions are instructions to the model!), and they
are what we save to disk in `runs/<run_id>/state.json`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, computed_field

# --- Research agent output ------------------------------------------------------


class Track(BaseModel):
    name: str
    description: str = ""
    prizes: list[str] = Field(default_factory=list)
    problem_statements: list[str] = Field(
        default_factory=list, description="Official problem statements under this track, if published."
    )


class PastWinner(BaseModel):
    project: str
    year: str = ""
    track: str = ""
    what_it_did: str
    why_it_won: str = Field("", description="Your best analysis of why judges picked it.")
    url: str = ""


class Source(BaseModel):
    title: str
    url: str


class ResearchReport(BaseModel):
    hackathon: str = Field(description="Official name of the hackathon.")
    edition: str = Field("", description="Which edition you researched, e.g. '2026' or 'Season 4'.")
    official_url: str = ""
    summary: str = Field(description="3-5 sentences: what this hackathon is about and who runs it.")
    dates: str = ""
    format: str = Field("", description="Online / in-person / hybrid, location, duration, team size.")
    tracks: list[Track] = Field(default_factory=list)
    judging_criteria: list[str] = Field(
        default_factory=list, description="Published criteria, with weights if known."
    )
    sponsors: list[str] = Field(default_factory=list)
    sponsor_tech: list[str] = Field(
        default_factory=list, description="Sponsor APIs or tools that can earn extra prizes."
    )
    past_winners: list[PastWinner] = Field(default_factory=list)
    judge_insights: list[str] = Field(
        default_factory=list, description="Patterns in what this hackathon's judges reward."
    )
    gaps: list[str] = Field(default_factory=list, description="Important things you could not find.")
    sources: list[Source] = Field(default_factory=list)


# --- Strategist agent output -------------------------------------------------------

# How much each score counts towards the total. Computed in code, not by the
# model: LLMs are unreliable at arithmetic, and this keeps the ranking honest.
SCORE_WEIGHTS = {
    "judge_fit": 0.30,
    "impact": 0.25,
    "feasibility": 0.20,
    "novelty": 0.15,
    "demo_wow": 0.10,
}


class Scores(BaseModel):
    judge_fit: int = Field(ge=1, le=10, description="How well it matches the judging criteria and track.")
    impact: int = Field(ge=1, le=10, description="How much it matters to real users.")
    feasibility: int = Field(ge=1, le=10, description="Can this team build a working demo in the time?")
    novelty: int = Field(ge=1, le=10, description="How fresh it is vs. typical submissions and past winners.")
    demo_wow: int = Field(ge=1, le=10, description="How memorable the live demo will be.")

    @computed_field
    @property
    def total(self) -> float:
        return round(sum(getattr(self, k) * w for k, w in SCORE_WEIGHTS.items()), 2)


class ProblemStatement(BaseModel):
    title: str = Field(description="A short, catchy project name.")
    track: str = Field(description="Which track this targets.")
    problem: str = Field(description="The real problem, in 2-3 sentences, ideally with evidence.")
    target_users: str
    solution: str = Field(description="What we build, in 2-4 sentences.")
    key_features: list[str] = Field(description="3-5 features that fit in the time limit.")
    wow_moment: str = Field(description="The one demo moment judges will remember.")
    suggested_stack: list[str]
    sponsor_tech_used: list[str] = Field(default_factory=list)
    why_it_wins: str = Field(description="Tie it to the judging criteria and past-winner patterns.")
    risks: list[str] = Field(description="What could go wrong, and how to reduce it.")
    scores: Scores


class StrategyReport(BaseModel):
    options: list[ProblemStatement] = Field(min_length=3, max_length=6)
    recommendation: str = Field(description="Which option you'd pick and why, in 2-4 sentences.")


# --- The whole run ---------------------------------------------------------------


class TeamProfile(BaseModel):
    skills: list[str] = Field(default_factory=list)
    hours: int = 36
    team_size: int | None = None


class ProjectState(BaseModel):
    """Everything about one run. Each stage fills in its part, then we save."""

    run_id: str
    created_at: str
    hackathon: str
    track: str | None = None
    team: TeamProfile = Field(default_factory=TeamProfile)

    research: ResearchReport | None = None
    strategy: StrategyReport | None = None
    chosen_index: int | None = None  # index into strategy.options

    usage: dict[str, dict[str, int]] = Field(default_factory=dict)  # tokens per agent

    @property
    def chosen(self) -> ProblemStatement | None:
        if self.strategy is None or self.chosen_index is None:
            return None
        return self.strategy.options[self.chosen_index]
