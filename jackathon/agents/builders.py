"""Backend and Frontend agents: write the code, run the checks, fix, repeat.

Both get the same BuildPlan (the contract) and work in parallel, each locked
to its own folder of the generated project.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from ..core.events import EventBus
from ..core.models import BuildPlan, BuildReport
from ..llm import LLM
from ..tools import Tool
from ..tools.checks import CHECKS
from ..tools.files import NoArgs, Workspace, file_tools
from .base import Agent

_WORKFLOW = """
How to work:
1. Call list_files and read the starter files first.
2. Write the code, one focused file at a time. Always write complete files, never fragments or "..." placeholders.
3. Call {check} to install, build and test. Read the errors carefully and fix them.
4. Repeat step 3 until it PASSES. Then submit. If you truly can't fix something, list it in known_issues."""

BACKEND_PROMPT = """You are the Backend Engineer on a hackathon team. You build the FastAPI backend for the build plan you're given. A frontend engineer is building the UI in parallel from the same plan, so implement the API contract exactly: same methods, same paths, same JSON field names.

Rules:
- Your folder already holds a working FastAPI app (app/main.py, with /api/health) and requirements.txt. Build on it. Don't edit serve.py: it serves the built frontend in production.
- Every route goes under /api/ exactly as written in the plan. Return JSON whose keys match the plan's data model fields.
- Store data in memory, or in SQLite via the built-in sqlite3 module, and load realistic, specific seed data at startup so the demo works immediately.
- Nothing may need an API key or the internet. For "AI" features, write a deterministic fallback (rules, scoring, templates) that produces convincing results.
- Split the code sensibly (e.g. app/models.py, app/data.py, app/routes.py) and keep app/main.py as the entry point that defines `app`.
- Write tests in tests/test_api.py with fastapi.testclient.TestClient that call every endpoint in the plan and check the status codes and key fields.
- Only add a package to requirements.txt if it's really needed.""" + _WORKFLOW.format(check="run_backend_checks")

FRONTEND_PROMPT = """You are the Frontend Engineer on a hackathon team. You build a polished React UI for the build plan you're given. A backend engineer is building the API in parallel from the same plan, so call the API contract exactly as written. You won't see the backend code.

Rules:
- Your folder already holds a working React 18 + Vite + Tailwind CSS 3 setup (JavaScript, JSX files). Build on it. Don't change vite.config.js, tailwind.config.js or postcss.config.js.
- Call the backend only through the `api()` helper in src/api.js, with paths exactly as in the contract, e.g. api("/api/patients") or api(`/api/patients/${id}`, { method: "POST", body }).
- src/main.jsx already wraps <App /> in <BrowserRouter>. In src/App.jsx use <Routes> and <Route> only. Never add another BrowserRouter or Router: nesting routers crashes the app at runtime even though the build passes.
- Put each screen in src/pages/ and reusable pieces in src/components/.
- Only use packages that are already in package.json, unless the plan lists more.

Make it look premium, because judges notice polish within seconds:
- A clean app shell: a top bar with the product name and navigation, and centred content (max-w-6xl).
- One accent color used consistently, neutral grays, generous spacing, rounded-2xl cards with subtle borders and shadows, clear type hierarchy.
- Every data view handles loading (skeletons), empty and error states.
- Use realistic copy. No lorem ipsum, no "TODO".
- Make the plan's wow moment the visual centrepiece of its screen.""" + _WORKFLOW.format(check="run_frontend_build")


class BuilderAgent(Agent[BuildReport]):
    area: ClassVar[str]  # "backend" or "frontend": the folder this agent owns
    check_tool: ClassVar[str]
    output_model = BuildReport
    max_steps = 40

    def __init__(self, project: Path, plan: BuildPlan, llm: LLM | None = None, bus: EventBus | None = None):
        super().__init__(llm=llm, bus=bus)
        self.project = project
        self.plan = plan
        self.workspace = Workspace(project / self.area)

    def tools(self) -> list[Tool]:
        async def run_checks(_: NoArgs) -> str:
            return str(await CHECKS[self.area](self.project, self.plan))

        description = (
            "Install dependencies, then build and test your code. Returns PASSED or FAILED with the errors."
        )
        return [*file_tools(self.workspace), Tool(self.check_tool, description, NoArgs, run_checks)]

    def build_task(self, request: str | None = None) -> str:
        plan = f"Build plan (JSON):\n{self.plan.model_dump_json(indent=2)}"
        if request is None:
            return f"Build the {self.area} for this plan.\n\n{plan}"
        return (
            f"The {self.area} for this plan is already built and in your folder. {request}\n\n"
            "Read the relevant files first, make the changes, then run your checks until they pass and submit. "
            f"If nothing here concerns the {self.area}, just run your checks and submit.\n\n{plan}"
        )


class BackendAgent(BuilderAgent):
    name = "Backend"
    role = "backend"
    area = "backend"
    check_tool = "run_backend_checks"

    def system_prompt(self) -> str:
        return BACKEND_PROMPT


class FrontendAgent(BuilderAgent):
    name = "Frontend"
    role = "frontend"
    area = "frontend"
    check_tool = "run_frontend_build"

    def system_prompt(self) -> str:
        return FRONTEND_PROMPT
