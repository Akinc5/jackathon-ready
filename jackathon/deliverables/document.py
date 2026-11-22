"""Renders the app document (PDF): what the app is, how it works, every screen and endpoint.

We write the document as HTML, then let headless Chromium (Playwright)
print it to PDF. HTML + CSS gives far nicer typography than building a
PDF by hand, and the browser is already installed for the smoke test.
"""

from __future__ import annotations

import base64
import re
from datetime import date
from html import escape
from pathlib import Path

from ..core.models import ProjectState

CSS = """
@page { size: A4; margin: 18mm 16mm; }
* { box-sizing: border-box; }
body { font-family: 'Segoe UI', Inter, system-ui, sans-serif; color: #1e293b; font-size: 10.5pt; line-height: 1.55; margin: 0; }
.cover { height: 250mm; display: flex; flex-direction: column; justify-content: center; page-break-after: always; }
.eyebrow { color: #6366f1; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; font-size: 9pt; }
h1 { font-size: 34pt; margin: 6px 0 4px; color: #0f172a; }
.tagline { font-size: 15pt; color: #475569; margin: 0 0 28px; }
.links a { color: #4f46e5; text-decoration: none; }
.meta { color: #64748b; font-size: 9.5pt; margin-top: 40px; }
h2 { font-size: 16pt; color: #0f172a; border-bottom: 2px solid #e0e7ff; padding-bottom: 4px; margin: 26px 0 10px; }
h3 { font-size: 12pt; margin: 16px 0 6px; color: #334155; }
p { margin: 0 0 10px; }
ul, ol { margin: 0 0 10px; padding-left: 20px; }
li { margin-bottom: 4px; }
table { width: 100%; border-collapse: collapse; margin: 6px 0 14px; font-size: 9pt; }
th { text-align: left; background: #eef2ff; color: #3730a3; padding: 6px 8px; }
td { border-bottom: 1px solid #e2e8f0; padding: 6px 8px; vertical-align: top; }
code { font-family: Consolas, monospace; font-size: 8.5pt; background: #f1f5f9; padding: 1px 4px; border-radius: 3px; }
figure { margin: 10px 0 18px; page-break-inside: avoid; }
figure img { width: 100%; border: 1px solid #cbd5e1; border-radius: 6px; }
figcaption { color: #64748b; font-size: 9pt; margin-top: 4px; }
.pill { display: inline-block; background: #eef2ff; color: #4338ca; border-radius: 999px; padding: 2px 10px; margin: 0 6px 6px 0; font-size: 9pt; }
"""


def build_html(state: ProjectState, run_dir: Path) -> str:
    plan, idea, pitch, links = state.plan, state.chosen, state.pitch, state.outputs
    e = escape

    def para(text: str) -> str:
        return "".join(f"<p>{e(chunk.strip())}</p>" for chunk in text.split("\n") if chunk.strip())

    def items(xs: list[str], tag: str = "ul") -> str:
        return f"<{tag}>" + "".join(f"<li>{e(x)}</li>" for x in xs) + f"</{tag}>"

    link_html = " &nbsp;·&nbsp; ".join(
        f'<a href="{e(u)}">{e(label)}: {e(u)}</a>'
        for label, u in (("Live app", links.deploy_url), ("Source code", links.repo_url))
        if u
    )

    screens = ""
    shots = {p.route: p.screenshot for p in (state.smoke.pages if state.smoke else []) if p.screenshot}
    for screen in plan.screens:
        shot = next((s for route, s in shots.items() if _route_matches(screen.route, route)), None)
        img = f'<img src="{_data_uri(run_dir / shot)}">' if shot and (run_dir / shot).exists() else ""
        screens += (
            f"<figure><h3>{e(screen.name)} <code>{e(screen.route)}</code></h3><p>{e(screen.purpose)}</p>{img}"
            f"<figcaption>Calls: {e(', '.join(screen.uses_endpoints) or 'nothing')}</figcaption></figure>"
        )

    models = "".join(
        f"<h3>{e(m.name)}</h3><p>{e(m.description)}</p><table><tr><th>Field</th><th>Type</th><th>Meaning</th></tr>"
        + "".join(f"<tr><td><code>{e(f.name)}</code></td><td>{e(f.type)}</td><td>{e(f.description)}</td></tr>" for f in m.fields)
        + "</table>"
        for m in plan.data_models
    )
    api = "<table><tr><th>Method</th><th>Path</th><th>What it does</th><th>Body</th><th>Returns</th></tr>" + "".join(
        f"<tr><td><b>{e(x.method)}</b></td><td><code>{e(x.path)}</code></td><td>{e(x.summary)}</td>"
        f"<td>{e(x.request_body or '-')}</td><td>{e(x.response)}</td></tr>"
        for x in plan.endpoints
    ) + "</table>"
    stack = "".join(f'<span class="pill">{e(t)}</span>' for t in ["FastAPI", "Python", "React 18", "Vite", "Tailwind CSS", *plan.python_packages, *plan.npm_packages])

    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>
<section class="cover">
  <div class="eyebrow">{e(state.hackathon)} · {e(idea.track)}</div>
  <h1>{e(plan.product_name)}</h1>
  <p class="tagline">{e(pitch.tagline)}</p>
  <div class="links">{link_html}</div>
  <div class="meta">App document · {date.today():%d %B %Y} · built in {state.team.hours} hours with Jackathon</div>
</section>

<h2>Overview</h2>{para(pitch.overview)}
<h2>The problem</h2><p>{e(idea.problem)}</p><p><b>Who it's for:</b> {e(idea.target_users)}</p>
<h2>Key features</h2>{items(idea.key_features)}
<p><b>The wow moment:</b> {e(idea.wow_moment)}</p>
<h2>How it works</h2>{para(pitch.how_it_works)}<p>{e(plan.architecture)}</p>
<h2>Using the app</h2>{items(pitch.user_journey, "ol")}
<h2>Screens</h2>{screens}
<h2>Data model</h2>{models}
<h2>API reference</h2>{api}
<p><b>Demo data:</b> {e(plan.seed_data)}</p>
{f"<p><b>AI features:</b> {e(plan.ai_features)}</p>" if plan.ai_features else ""}
<h2>Tech stack</h2><p>{stack}</p>
<h2>Running it locally</h2>
<ol><li>Backend: <code>cd backend</code>, <code>pip install -r requirements.txt</code>, <code>uvicorn app.main:app --reload</code></li>
<li>Frontend: <code>cd frontend</code>, <code>npm install</code>, <code>npm run dev</code>, then open <code>http://localhost:5173</code></li>
<li>Or everything in one container: <code>docker build -t app .</code> and <code>docker run -p 7860:7860 app</code></li></ol>
<h2>What's next</h2>{items(pitch.whats_next)}
{f"<h3>Left out on purpose (time limit)</h3>{items(plan.cut_scope)}" if plan.cut_scope else ""}
</body></html>"""


async def render_pdf(html: str, out: Path) -> Path:
    from playwright.async_api import async_playwright

    out.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.set_content(html, wait_until="load")
        await page.pdf(path=str(out), format="A4", print_background=True)
        await browser.close()
    return out


def _route_matches(pattern: str, route: str) -> bool:
    return re.fullmatch(re.sub(r":[^/]+", "[^/]+", pattern), route) is not None


def _data_uri(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()
