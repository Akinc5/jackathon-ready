"""Build checks for the generated project: install, compile, test, build.

The builder agents call these through a tool to see their own errors, and
the pipeline calls them again afterwards to verify the result for real.

Install safety: npm runs with --ignore-scripts and pip with --only-binary, so
installing a package can't run code from that package. Install scripts are
the most common way malicious packages attack.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

from ..core.models import BuildPlan
from .runner import ProcessResult, run_process


class CheckResult:
    def __init__(self, ok: bool, report: str):
        self.ok = ok
        self.report = report

    def __str__(self) -> str:
        return f"{'PASSED' if self.ok else 'FAILED'}\n\n{self.report}"


# --- backend -----------------------------------------------------------------


def venv_python(backend: Path) -> Path:
    sub = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    return backend / ".venv" / sub


async def ensure_backend_env(backend: Path) -> ProcessResult:
    """Create backend/.venv and install requirements.txt (skipped if nothing changed)."""
    python = venv_python(backend)
    if not python.exists():
        made = await run_process([sys.executable, "-m", "venv", ".venv"], backend, timeout=180)
        if not made.ok:
            return made
    return await _install_if_changed(
        backend / "requirements.txt",
        backend / ".venv" / ".installed",
        [str(python), "-m", "pip", "install", "-q", "--disable-pip-version-check",
         "--only-binary=:all:", "-r", "requirements.txt"],
        backend,
    )


async def check_backend(project: Path, plan: BuildPlan) -> CheckResult:
    backend = project / "backend"
    install = await ensure_backend_env(backend)
    if not install.ok:
        return CheckResult(False, f"Installing requirements.txt failed:\n{install.output}")
    py = str(venv_python(backend))

    compiled = await run_process([py, "-m", "compileall", "-q", "app"], backend, timeout=60)
    if not compiled.ok:
        return CheckResult(False, f"Python syntax errors:\n{compiled.output}")

    contract = await run_process(
        [py, "../.jackathon/contract_check.py", "../.jackathon/contract.json"], backend, timeout=60
    )
    tests = await run_process(
        [py, "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider"], backend, timeout=180
    )
    ok = contract.ok and tests.ok
    report = f"API contract check:\n{contract.output}\n\nTests (pytest):\n{tests.output}"
    return CheckResult(ok, report)


# --- frontend ------------------------------------------------------------------


async def ensure_frontend_deps(frontend: Path) -> ProcessResult:
    return await _install_if_changed(
        frontend / "package.json",
        frontend / "node_modules" / ".installed",
        ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund", "--loglevel=error"],
        frontend,
        timeout=600,
    )


async def check_frontend(project: Path, plan: BuildPlan) -> CheckResult:
    frontend = project / "frontend"
    install = await ensure_frontend_deps(frontend)
    if not install.ok:
        return CheckResult(False, f"npm install failed:\n{install.output}")

    build = await run_process(["npm", "run", "build"], frontend, timeout=300)
    if not build.ok:
        return CheckResult(False, f"Build failed (vite build):\n{build.output}")

    # Mistakes that compile fine but crash in the browser.
    problems = runtime_pitfalls(frontend / "src")
    if problems:
        return CheckResult(False, "The build passed, but this code will crash in the browser:\n" + "\n".join(problems))

    unused = unreferenced_endpoints(frontend / "src", plan)
    report = "Build succeeded."
    if unused:
        report += "\n\nWarning: these contract endpoints are never called from src/:\n" + "\n".join(
            f"  {e}" for e in unused
        )
    return CheckResult(True, report)


def unreferenced_endpoints(src: Path, plan: BuildPlan) -> list[str]:
    """Endpoints whose path never appears in the frontend code (a cheap contract check)."""
    code = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in src.rglob("*.js*"))
    missing = []
    for e in plan.endpoints:
        prefix = e.path.split("{")[0].rstrip("/")  # /api/patients/{id} -> /api/patients
        if prefix not in code:
            missing.append(e.key)
    return missing


def runtime_pitfalls(src: Path) -> list[str]:
    """Known mistakes `vite build` can't see. (A real browser smoke test comes with the Evaluator.)"""
    problems = []
    for p in src.rglob("*.jsx"):
        if p.name == "main.jsx":
            continue
        if re.search(r"<(BrowserRouter|HashRouter|MemoryRouter|Router)\b", p.read_text(encoding="utf-8", errors="ignore")):
            rel = p.relative_to(src).as_posix()
            problems.append(
                f"  src/{rel} renders a Router, but src/main.jsx already wraps the app in <BrowserRouter>. "
                "React Router crashes with two. Remove it and keep only <Routes>/<Route>."
            )
    return problems


CHECKS = {"backend": check_backend, "frontend": check_frontend}


# --- helpers ---------------------------------------------------------------------


async def _install_if_changed(
    manifest: Path, stamp: Path, cmd: list[str], cwd: Path, timeout: float = 600
) -> ProcessResult:
    """Run an install command only when the manifest's contents changed since last time."""
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    if stamp.exists() and stamp.read_text() == digest:
        return ProcessResult(0, "Dependencies up to date.")
    result = await run_process(cmd, cwd, timeout=timeout)
    if result.ok:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(digest)
    return result
