"""Browser smoke test: run the built app and open every screen in headless Chromium.

`vite build` and pytest can both pass while the page is blank in a browser
(our first live run proved it). This test runs the app exactly as it will be
deployed (FastAPI serving the built frontend), visits each screen in the plan,
records any JavaScript errors and takes a screenshot of each one. The
screenshots go into the pitch deck and the PDF.
"""

from __future__ import annotations

import asyncio
import re
import socket
import time
from pathlib import Path

import httpx

from ..core.models import BuildPlan, PageCheck, SmokeReport
from .checks import venv_python
from .runner import kill_tree, run_process, safe_env

IGNORED_ERRORS = ("favicon.ico", "Download the React DevTools")
MIN_TEXT = 20  # a "rendered" page shows at least this many characters


class AppServer:
    """Runs the built app (FastAPI serving the built frontend) on a free local port.

        async with AppServer(project) as app:
            ...visit app.url...

    The smoke test uses it, and so does the web UI's "try the app" preview.
    """

    def __init__(self, project: Path):
        self.project = project.resolve()
        self.url = ""
        self.error = ""
        self._server = None
        self._log_file = None

    async def __aenter__(self) -> AppServer:
        if not (self.project / "frontend").is_dir() or not venv_python(self.project / "backend").exists():
            self.error = "The app hasn't been built and installed yet."
            return self
        build = await run_process(["npm", "run", "build"], self.project / "frontend", timeout=300)
        if not build.ok:
            self.error = f"The frontend doesn't build:\n{build.output}"
            return self
        port = _free_port()
        log = self.project / ".jackathon" / "server.log"
        log.parent.mkdir(exist_ok=True)
        self._log_file = open(log, "w", encoding="utf-8")
        env = {**safe_env(), "STATIC_DIR": str(self.project / "frontend" / "dist")}
        self._server = await asyncio.create_subprocess_exec(
            str(venv_python(self.project / "backend")), "-m", "uvicorn", "serve:app", "--port", str(port),
            cwd=self.project / "backend", env=env, stdout=self._log_file, stderr=self._log_file,
        )
        self.url = f"http://127.0.0.1:{port}"
        if not await _wait_until_up(f"{self.url}/api/health"):
            self._log_file.flush()
            self.error = "The backend didn't start:\n" + log.read_text(encoding="utf-8")[-3000:]
        return self

    async def __aexit__(self, *exc) -> None:
        await self.stop()

    async def stop(self) -> None:
        if self._server and self._server.returncode is None:
            kill_tree(self._server.pid)
            await self._server.wait()
        if self._log_file:
            self._log_file.close()
            self._log_file = None


async def smoke_test(project: Path, plan: BuildPlan, shots_dir: Path, run_dir: Path) -> SmokeReport:
    async with AppServer(project) as app:
        if app.error:
            return SmokeReport(ok=False, problem=app.error)
        pages = await _visit_screens(app.url, plan, shots_dir, run_dir)
    return SmokeReport(ok=bool(pages) and all(p.ok for p in pages), pages=pages)


async def _visit_screens(base: str, plan: BuildPlan, shots_dir: Path, run_dir: Path) -> list[PageCheck]:
    from playwright.async_api import async_playwright

    shots_dir.mkdir(parents=True, exist_ok=True)
    routes = [s.route for s in plan.screens] or ["/"]
    found_links: list[str] = []
    results: list[PageCheck] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        for route in routes:
            url_path = route if ":" not in route else _fill_params(route, found_links)
            if url_path is None:
                continue  # e.g. /patients/:id with no matching link to follow
            # A fresh tab per screen, so one screen's errors aren't blamed on the next.
            page = await browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(f"Uncaught: {e}"))
            try:
                await page.goto(base + url_path, wait_until="networkidle", timeout=20_000)
                await page.wait_for_timeout(800)  # let charts and effects settle
                # The React app must have rendered into #root. Raw JSON or an error page has no #root.
                rendered = await page.evaluate(
                    "() => { const r = document.getElementById('root'); return r ? r.innerText.trim().length : -1 }"
                )
                if rendered == -1:
                    errors.append("This URL doesn't show the app at all (a backend route answers it instead).")
                elif rendered < MIN_TEXT:
                    errors.append("The page rendered (almost) nothing: it looks blank.")
                found_links += await page.eval_on_selector_all("a[href^='/']", "els => els.map(e => e.getAttribute('href'))")
            except Exception as e:  # navigation timeouts etc.
                errors.append(f"Couldn't load the page: {e}")
            errors = [e[:500] for e in errors if not any(skip in e for skip in IGNORED_ERRORS)]
            shot = shots_dir / f"{_slug(url_path)}.png"
            await page.screenshot(path=shot)
            results.append(
                PageCheck(route=url_path, ok=not errors, errors=errors, screenshot=shot.relative_to(run_dir).as_posix())
            )
            await page.close()
        await browser.close()
    return results


def _fill_params(route: str, links: list[str]) -> str | None:
    """Turn /beds/:bed_id into a real link seen on an earlier page, like /beds/BED-02."""
    pattern = "^" + re.sub(r":[^/]+", "[^/]+", route) + "$"
    return next((link for link in links if re.match(pattern, link)), None)


async def _wait_until_up(url: str, timeout: float = 40) -> bool:
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            try:
                if (await client.get(url, timeout=2)).status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.5)
    return False


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _slug(path: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-") or "home"
