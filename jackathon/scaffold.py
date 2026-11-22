"""Creates a new project from the templates, before the builders start.

Starting from a known-good skeleton (a working FastAPI app and a working
Vite + React + Tailwind setup) means the agents spend their effort on the
actual product, not on config files they might get wrong.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from .core.models import BuildPlan

TEMPLATES = Path(__file__).parent / "templates"

# Only plain package names get through, so a plan can't smuggle in URLs or flags.
_PYPI_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9,._-]+\])?$")
_NPM_NAME = re.compile(r"^(@[a-z0-9~][a-z0-9-._~]*/)?[a-z0-9~][a-z0-9-._~]*$")


def scaffold(project: Path, plan: BuildPlan) -> None:
    """Copy the templates into `project` (skipping parts that already exist) and add the plan's packages."""
    for part in ("backend", "frontend"):
        if not (project / part).exists():
            shutil.copytree(TEMPLATES / part, project / part)
    # Files owned by Jackathon, not the agents: refreshed every time.
    shutil.copy(TEMPLATES / "backend" / "serve.py", project / "backend" / "serve.py")
    shutil.copytree(TEMPLATES / "root", project, dirs_exist_ok=True)  # Dockerfile, .gitignore, .dockerignore

    tools = project / ".jackathon"
    tools.mkdir(parents=True, exist_ok=True)
    shutil.copy(TEMPLATES / "_tools" / "contract_check.py", tools / "contract_check.py")
    contract = [[e.method, e.path] for e in plan.endpoints]
    (tools / "contract.json").write_text(json.dumps(contract, indent=2), encoding="utf-8")

    _add_python_packages(project / "backend" / "requirements.txt", plan.python_packages)
    _add_npm_packages(project / "frontend" / "package.json", plan.npm_packages)


def _add_python_packages(requirements: Path, packages: list[str]) -> None:
    current = requirements.read_text(encoding="utf-8").split()
    extra = [p for p in packages if _PYPI_NAME.match(p) and p not in current]
    if extra:
        requirements.write_text("\n".join(current + extra) + "\n", encoding="utf-8")


def _add_npm_packages(package_json: Path, packages: list[str]) -> None:
    data = json.loads(package_json.read_text(encoding="utf-8"))
    deps = data.setdefault("dependencies", {})
    known = deps | data.get("devDependencies", {})
    for name in packages:
        if _NPM_NAME.match(name) and name not in known:
            deps[name] = "latest"
    package_json.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
