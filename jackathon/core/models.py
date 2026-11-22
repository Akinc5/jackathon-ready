"""The data that flows between agents.

These Pydantic models do double duty: they are the JSON schemas the agents
must fill in (field descriptions are instructions to the model!), and they
are what we save to disk in `runs/<run_id>/state.json`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, computed_field, field_validator

# --- What the user gave us -------------------------------------------------------


class RunInput(BaseModel):
    """The single input bar: a name, or a flyer image / PDF (with optional text)."""

    text: str = ""
    file_name: str | None = None  # stored as runs/<run_id>/input/<file_name>


class HackathonBrief(BaseModel):
    """What the Intake agent read off a flyer or PDF."""

    name: str = Field(description="The hackathon's name exactly as printed.")
    edition: str = ""
    website: str = ""
    dates: str = ""
    location: str = Field("", description="City/venue, or 'online'.")
    themes: list[str] = Field(default_factory=list, description="Tracks or themes.")
    problem_statements: list[str] = Field(default_factory=list, description="Official problem statements, verbatim.")
    prizes: list[str] = Field(default_factory=list)
    judging_criteria: list[str] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list, description="Eligibility, team size, submission requirements.")
    sponsors: list[str] = Field(default_factory=list)
    other: str = Field("", description="Anything else useful that doesn't fit above.")


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


# --- Architect agent output ------------------------------------------------------------


class FieldSpec(BaseModel):
    name: str
    type: str = Field(description="string, integer, float, boolean, datetime, list[...], or another model's name")
    description: str = ""


class DataModel(BaseModel):
    name: str = Field(description="PascalCase, e.g. Patient")
    description: str = ""
    fields: list[FieldSpec]


class Endpoint(BaseModel):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str = Field(description="Starts with /api/. Path parameters in braces, e.g. /api/patients/{patient_id}")
    summary: str
    request_body: str = Field("", description="A data model name, or a small JSON example. Empty if none.")
    response: str = Field(description="A data model name like 'list[Patient]', or a small JSON example.")

    @field_validator("path")
    @classmethod
    def _under_api(cls, path: str) -> str:
        if not path.startswith("/api/"):
            raise ValueError("every path must start with /api/")
        return path.rstrip("/")

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"


class Screen(BaseModel):
    name: str
    route: str = Field(description="Frontend route, e.g. / or /patients/:id")
    purpose: str
    uses_endpoints: list[str] = Field(description="Endpoints this screen calls, written as 'METHOD /api/path'.")


class BuildPlan(BaseModel):
    """The contract both builders follow, so the frontend and backend fit together."""

    product_name: str
    one_liner: str
    architecture: str = Field(description="How the pieces fit together, in 3-6 sentences.")
    data_models: list[DataModel] = Field(min_length=1)
    endpoints: list[Endpoint] = Field(min_length=2, max_length=15)
    screens: list[Screen] = Field(min_length=1, max_length=6)
    seed_data: str = Field(description="The realistic demo data the backend ships with, so the demo works offline.")
    ai_features: str = Field(
        "", description="If the product uses AI: how, plus a no-API-key fallback so the demo never breaks."
    )
    python_packages: list[str] = Field(
        default_factory=list,
        description="Extra PyPI packages beyond fastapi, uvicorn, pydantic, pytest and httpx. Keep it minimal.",
    )
    npm_packages: list[str] = Field(
        default_factory=list,
        description="Extra npm packages beyond react, react-dom, react-router-dom and tailwindcss. Keep it minimal.",
    )
    demo_script: list[str] = Field(description="The live demo, click by click, ending on the wow moment.")
    cut_scope: list[str] = Field(default_factory=list, description="What was deliberately left out to fit the time.")


# --- Builder agents output ------------------------------------------------------------


class BuildReport(BaseModel):
    """What a builder agent says about its own work."""

    summary: str = Field(description="What you built, in 2-4 sentences.")
    files: list[str] = Field(description="The main files you created or changed.")
    known_issues: list[str] = Field(default_factory=list, description="Anything unfinished or shaky. Be honest.")


class BuildRecord(BaseModel):
    """A builder's report plus our own verification. We re-run the checks
    ourselves rather than trusting the agent's word that it works."""

    report: BuildReport
    verified: bool
    check_output: str = ""


# --- Storyteller agent output (pitch deck + app document) -----------------------------


class Slide(BaseModel):
    title: str = Field(description="Short slide title, at most 8 words.")
    bullets: list[str] = Field(min_length=1, max_length=5, description="3-5 punchy points, at most 15 words each.")
    notes: str = Field(description="What the presenter says on this slide: 40-90 words, spoken style.")
    show_screenshot: bool = Field(False, description="True to show a screenshot of the app (solution and demo slides).")


class PitchContent(BaseModel):
    tagline: str = Field(description="One line, at most 12 words.")
    slides: list[Slide] = Field(
        min_length=5,
        max_length=9,
        description="In order: problem, solution, demo / wow moment, how it works, why it wins, what's next. "
        "The title and closing slides are added for you.",
    )
    overview: str = Field(description="For the app document: 2-3 paragraphs on what it is, who it's for, why it matters.")
    how_it_works: str = Field(description="2-3 paragraphs explaining the architecture and main flows in plain language.")
    user_journey: list[str] = Field(description="5-8 steps: what a user does and sees.")
    whats_next: list[str] = Field(description="3-5 realistic next steps after the hackathon.")


# --- Testing and shipping --------------------------------------------------------------


class PageCheck(BaseModel):
    route: str
    ok: bool
    errors: list[str] = Field(default_factory=list)
    screenshot: str | None = None  # path relative to the run folder


class SmokeReport(BaseModel):
    """A real browser opened each screen of the built app."""

    ok: bool
    pages: list[PageCheck] = Field(default_factory=list)
    problem: str = ""  # set when the app couldn't even start

    def failures(self) -> str:
        lines = [self.problem] if self.problem else []
        for p in self.pages:
            if not p.ok:
                lines.append(f"Screen {p.route}:\n" + "\n".join(f"  {e}" for e in p.errors))
        return "\n".join(lines)


class Outputs(BaseModel):
    repo_url: str | None = None
    space_id: str | None = None  # e.g. "you/pulsesignal" on Hugging Face
    deploy_url: str | None = None
    deploy_status: str = "not started"  # not started | building | running | failed: ... | skipped: ...
    deck: str | None = None  # paths relative to the run folder
    doc: str | None = None


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
    source: RunInput = Field(default_factory=RunInput)

    brief: HackathonBrief | None = None
    research: ResearchReport | None = None
    strategy: StrategyReport | None = None
    chosen_index: int | None = None  # index into strategy.options
    plan: BuildPlan | None = None
    plan_approved: bool = False
    builds: dict[str, BuildRecord] = Field(default_factory=dict)  # "backend" / "frontend"
    smoke: SmokeReport | None = None
    pitch: PitchContent | None = None
    change_requests: list[str] = Field(default_factory=list)
    changes_done: bool = False  # the team said "ship it"
    outputs: Outputs = Field(default_factory=Outputs)

    usage: dict[str, dict[str, int]] = Field(default_factory=dict)  # tokens per agent

    @property
    def chosen(self) -> ProblemStatement | None:
        if self.strategy is None or self.chosen_index is None:
            return None
        return self.strategy.options[self.chosen_index]
