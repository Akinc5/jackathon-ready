"""Publishes the generated project to GitHub with the `gh` CLI.

Uses whatever account `gh auth login` is signed into, so Jackathon never
handles your GitHub password or token itself.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from .runner import ProcessResult, run_process


class PublishError(RuntimeError):
    pass


def slugify(name: str, max_len: int = 60) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:max_len].strip("-") or "hackathon-app"


async def _run(cmd: list[str], cwd: Path, what: str) -> ProcessResult:
    result = await run_process(cmd, cwd, timeout=180)
    if not result.ok:
        raise PublishError(f"{what} failed:\n{result.output}")
    return result


async def github_owner(cwd: Path) -> str:
    if not shutil.which("gh"):
        raise PublishError("The GitHub CLI (gh) isn't installed. Install it from https://cli.github.com and run: gh auth login")
    result = await run_process(["gh", "api", "user", "--jq", ".login"], cwd, timeout=30)
    if not result.ok:
        raise PublishError("The GitHub CLI isn't signed in. Run: gh auth login")
    return result.output.strip()


async def create_repo(project: Path, name: str, description: str, public: bool = True) -> str:
    """Create a new GitHub repo from `project` and push it. Returns the repo URL."""
    owner = await github_owner(project)
    repo = await _unused_name(project, owner, slugify(name))
    await _commit(project, "Initial commit (built with Jackathon)")
    await _run(
        ["gh", "repo", "create", repo, "--public" if public else "--private",
         "--description", description[:300], "--source", ".", "--remote", "origin", "--push"],
        project,
        "Creating the GitHub repo",
    )
    return f"https://github.com/{owner}/{repo}"


async def push_update(project: Path, message: str) -> None:
    """Commit any changes and push them to the existing repo."""
    if await _commit(project, message):
        await _run(["git", "push", "origin", "HEAD"], project, "Pushing to GitHub")


async def _commit(project: Path, message: str) -> bool:
    """Stage and commit everything. Returns False if there was nothing to commit."""
    if not (project / ".git").exists():
        await _run(["git", "init", "-b", "main"], project, "git init")
    await _run(["git", "add", "-A"], project, "git add")
    status = await _run(["git", "status", "--porcelain"], project, "git status")
    if not status.output.strip():
        return False
    await _run(["git", "commit", "-q", "-m", message], project, "git commit")
    return True


async def _unused_name(cwd: Path, owner: str, name: str) -> str:
    """name, or name-2, name-3... if a repo with that name already exists."""
    candidate, n = name, 1
    while (await run_process(["gh", "repo", "view", f"{owner}/{candidate}"], cwd, timeout=30)).ok:
        n += 1
        candidate = f"{name}-{n}"
    return candidate
