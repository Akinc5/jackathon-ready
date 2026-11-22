"""Deploys the generated project to a Hugging Face Docker Space.

The project's Dockerfile builds the React app and serves it from FastAPI in
one container. Hugging Face builds that container and hosts it for free at
https://<owner>-<space>.hf.space.

Needs HF_TOKEN (a free "Write" token from huggingface.co/settings/tokens).
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path

IGNORE = ["**/node_modules/**", "**/.venv/**", "**/dist/**", "**/__pycache__/**", "**/.pytest_cache/**",
          ".jackathon/**", ".git/**", "docs/**", "README.md"]

SPACE_README = """---
title: {title}
emoji: 🚀
colorFrom: indigo
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
---

{tagline}

Source code: {repo}
"""


class DeployError(RuntimeError):
    pass


def hf_token() -> str | None:
    return os.getenv("HF_TOKEN") or None


def space_url(repo_id: str) -> str:
    owner, name = repo_id.split("/")
    return f"https://{re.sub(r'[^a-z0-9]+', '-', f'{owner}-{name}'.lower())}.hf.space"


async def deploy_space(project: Path, name: str, title: str, tagline: str, repo_url: str | None) -> str:
    """Create (or update) the Space and upload the project. Returns the Space id, e.g. 'you/pulsesignal'.
    Hugging Face then builds it in the background; see wait_for_space()."""
    from huggingface_hub import HfApi

    token = hf_token()
    if not token:
        raise DeployError("HF_TOKEN isn't set. Create a free Write token at https://huggingface.co/settings/tokens and add it to .env.")
    api = HfApi(token=token)
    try:
        owner = (await asyncio.to_thread(api.whoami))["name"]
        repo_id = f"{owner}/{name}"
        await asyncio.to_thread(api.create_repo, repo_id, repo_type="space", space_sdk="docker", exist_ok=True)
        await asyncio.to_thread(
            api.upload_folder, folder_path=str(project), repo_id=repo_id, repo_type="space",
            ignore_patterns=IGNORE, commit_message="Deploy from Jackathon",
        )
        readme = SPACE_README.format(title=title[:30], tagline=tagline, repo=repo_url or "")
        await asyncio.to_thread(
            api.upload_file, path_or_fileobj=readme.encode(), path_in_repo="README.md", repo_id=repo_id,
            repo_type="space", commit_message="Space README",
        )
    except Exception as e:  # network errors, bad token, name clashes...
        raise DeployError(f"Uploading to Hugging Face failed: {e}") from e
    return repo_id


async def wait_for_space(repo_id: str, timeout: float = 1200) -> str:
    """Poll the Space until it's running or fails. Returns 'running' or 'failed: <stage>'."""
    from huggingface_hub import HfApi

    api = HfApi(token=hf_token())
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        stage = (await asyncio.to_thread(api.get_space_runtime, repo_id)).stage
        if stage == "RUNNING":
            return "running"
        if stage in ("BUILD_ERROR", "RUNTIME_ERROR", "CONFIG_ERROR", "NO_APP_FILE"):
            return f"failed: {stage.lower().replace('_', ' ')} (see the build logs on the Space page)"
        await asyncio.sleep(15)
    return "failed: still building after 20 minutes (check the Space page)"
