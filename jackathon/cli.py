"""Command-line interface. Try:  jackathon run "HackMIT" --track "Healthcare"

This file only handles input and display. All the real work happens in
`core.pipeline`, so the web UI (Phase 5) can reuse the exact same pipeline.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import IntPrompt, Prompt
from rich.table import Table
from rich.text import Text

from .agents import AgentError, IntakeError
from .config import PROVIDERS, RUNS_DIR, model_for
from .core.events import Event, EventBus
from .core.models import (
    BuildPlan,
    BuildRecord,
    ProblemStatement,
    ProjectState,
    ResearchReport,
    StrategyReport,
    TeamProfile,
)
from .core.pipeline import Pipeline
from .core.store import RunStore
from .llm import LLMError, create_llm
from .tools.checks import ensure_backend_env, ensure_frontend_deps, venv_python
from .tools.runner import kill_tree, safe_env

app = typer.Typer(help="Jackathon: AI agents that help you win hackathons.", no_args_is_help=True)
console = Console()
ROLES = ["intake", "research", "strategist", "architect", "backend", "frontend", "storyteller"]
AGENT_COLORS = {"Backend": "blue", "Frontend": "magenta"}


# --- commands ----------------------------------------------------------------


@app.command()
def run(
    hackathon: str = typer.Argument("", help="Name of the hackathon, e.g. 'Smart India Hackathon 2026'"),
    file: Path = typer.Option(None, "--file", "-f", help="A flyer (PNG/JPG) or PDF about the hackathon", exists=True),
    track: str = typer.Option(None, "--track", "-t", help="Track you want to compete in"),
    skills: str = typer.Option("", "--skills", "-s", help="Team skills, comma separated"),
    hours: int = typer.Option(36, "--hours", help="How long you have to build"),
    team_size: int = typer.Option(None, "--team-size", help="Number of people on the team"),
):
    """Start a new run: research, pick an idea, plan, build, test, publish, deploy, pitch."""
    if not hackathon and not file:
        console.print("Give a hackathon name, or a flyer/PDF with --file.")
        raise typer.Exit(1)
    _check_models()
    store = RunStore(RUNS_DIR)
    team = TeamProfile(
        skills=[s.strip() for s in skills.split(",") if s.strip()], hours=hours, team_size=team_size
    )
    state = store.create(hackathon, track, team, file.name if file else None, file.read_bytes() if file else None)
    console.print(f"[dim]Run id: {state.run_id}  (resume with: jackathon resume {state.run_id})[/]")
    _execute(store, state)


@app.command()
def web(port: int = typer.Option(8080, help="Port for the web interface")):
    """Open the web interface."""
    import uvicorn
    import webbrowser

    from .web.server import create_app

    _check_models()
    url = f"http://localhost:{port}"
    console.print(Panel(f"Jackathon is running at [bold]{url}[/]\n[dim]Press Ctrl+C to stop.[/]", border_style="magenta"))
    webbrowser.open(url)
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")


@app.command()
def resume(run_id: str):
    """Continue a run from where it stopped."""
    store = RunStore(RUNS_DIR)
    _execute(store, store.load(run_id))


@app.command()
def show(run_id: str):
    """Show the results of a run."""
    state = RunStore(RUNS_DIR).load(run_id)
    if state.research:
        render_research(state.research)
    if state.strategy:
        render_strategy(state.strategy)
    if state.chosen:
        render_choice(state.chosen)
    if state.plan:
        render_plan(state.plan)
    if state.builds:
        render_builds(state)
    render_outputs(state, RunStore(RUNS_DIR))


@app.command()
def serve(run_id: str):
    """Start the generated app (backend on :8000, frontend on :5173)."""
    project = RunStore(RUNS_DIR).project_dir(run_id).resolve()
    backend, frontend = project / "backend", project / "frontend"
    if not backend.exists() or not frontend.exists():
        console.print("[red]Nothing has been built for this run yet.[/] Run: jackathon resume " + run_id)
        raise typer.Exit(1)

    with console.status("Installing dependencies (first time only)..."):
        installs = asyncio.run(_install_all(backend, frontend))
    for name, result in zip(("backend", "frontend"), installs):
        if not result.ok:
            console.print(f"[red]Installing {name} dependencies failed:[/]\n{result.output}")
            raise typer.Exit(1)

    procs = [
        subprocess.Popen(
            [str(venv_python(backend)), "-m", "uvicorn", "app.main:app", "--port", "8000"],
            cwd=backend,
            env=safe_env(),
        ),
        subprocess.Popen([shutil.which("npm") or "npm", "run", "dev"], cwd=frontend, env=safe_env()),
    ]
    console.print(
        Panel(
            "[bold]App:[/]      http://localhost:5173\n[bold]API docs:[/] http://localhost:8000/docs\n\n"
            "[dim]Press Ctrl+C to stop.[/]",
            title="Your app is running",
            border_style="green",
        )
    )
    try:
        while all(p.poll() is None for p in procs):
            time.sleep(0.5)
        console.print("[red]A server stopped unexpectedly. See its output above.[/]")
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            kill_tree(p.pid)


async def _install_all(backend, frontend):
    return await asyncio.gather(ensure_backend_env(backend), ensure_frontend_deps(frontend))


@app.command("runs")
def list_runs():
    """List your previous runs."""
    states = RunStore(RUNS_DIR).list()
    if not states:
        console.print("No runs yet. Start one with: jackathon run \"<hackathon name>\"")
        return
    table = Table("Run id", "Hackathon", "Track", "Stage", "Chosen")
    for s in states:
        stage = (
            "built" if len(s.builds) == 2 else "planned" if s.plan_approved else "picked" if s.chosen
            else "ranked" if s.strategy else "researched" if s.research else "started"
        )
        table.add_row(s.run_id, s.hackathon, s.track or "-", stage, s.chosen.title if s.chosen else "-")
    console.print(table)


@app.command()
def doctor():
    """Check which model each agent uses and whether its API key is set."""
    table = Table("Agent", "Model", "Key")
    for role in ROLES:
        spec = model_for(role)
        info = PROVIDERS.get(spec.partition(":")[0])
        if info is None:
            status = "[red]unknown provider[/]"
        elif info.key_env is None:
            status = "[green]no key needed[/]"
        elif os.getenv(info.key_env):
            status = f"[green]{info.key_env} set[/]"
        else:
            status = f"[red]{info.key_env} missing[/]"
        table.add_row(role, spec, status)
    console.print(table)


# --- running the pipeline ----------------------------------------------------------


def _check_models() -> None:
    """Fail fast, before creating a run, if any agent's model can't be set up."""
    for role in ROLES:
        try:
            create_llm(model_for(role))
        except (LLMError, ValueError) as e:
            console.print(f"[red bold]Can't start:[/] {e}  [dim](see: jackathon doctor)[/]")
            raise typer.Exit(1)


def _execute(store: RunStore, state: ProjectState) -> None:
    bus = EventBus()
    bus.subscribe(render_event)
    pipeline = Pipeline(store, bus, TerminalHuman())
    try:
        state = asyncio.run(pipeline.run(state))
    except (LLMError, AgentError, IntakeError) as e:
        console.print(f"\n[red bold]Stopped:[/] {e}")
        console.print(f"[dim]Progress is saved. Fix the problem, then: jackathon resume {state.run_id}[/]")
        raise typer.Exit(1)
    except KeyboardInterrupt:
        console.print(f"\n[yellow]Interrupted.[/] Resume with: jackathon resume {state.run_id}")
        raise typer.Exit(130)

    render_outputs(state, store)
    total_in = sum(u["input_tokens"] for u in state.usage.values())
    total_out = sum(u["output_tokens"] for u in state.usage.values())
    console.print(f"[dim]Tokens used: {total_in:,} in / {total_out:,} out[/]")


class TerminalHuman:
    """Asks the team's questions in the terminal."""

    async def choose(self, state: ProjectState) -> int:
        if state.research:
            render_research(state.research)
        render_strategy(state.strategy)
        n = len(state.strategy.options)
        pick = IntPrompt.ask("Which problem statement do you want to build?", choices=[str(i) for i in range(1, n + 1)], default=1)
        return pick - 1

    async def approve_plan(self, state: ProjectState) -> str | None:
        render_plan(state.plan)
        answer = Prompt.ask(
            "[bold]Press Enter to approve this plan[/], or type the changes you want", default="", show_default=False
        )
        return answer.strip() or None

    async def review_app(self, state: ProjectState) -> str | None:
        render_builds(state)
        console.print(f"[dim]Try it first in another terminal: jackathon serve {state.run_id}[/]")
        answer = Prompt.ask(
            "[bold]Press Enter to ship it[/] (GitHub, deploy, pitch deck), or type the changes you want",
            default="",
            show_default=False,
        )
        return answer.strip() or None


# --- display -----------------------------------------------------------------


def render_event(e: Event) -> None:
    # Two builders run at once, so tag their lines with a colored name.
    color = AGENT_COLORS.get(e.agent or "")
    tag = f"[{color}]{e.agent:>8}[/] " if color else "  "
    if e.kind == "stage_started":
        console.rule(f"[bold magenta]{e.message}")
    elif e.kind == "agent_started":
        console.print(f"[bold cyan]{e.agent}[/] agent working [dim]({e.data.get('model')})[/]")
    elif e.kind == "agent_thought":
        text = e.message if len(e.message) <= 300 else e.message[:299] + "…"
        console.print(Text.from_markup(tag) + Text(text, style="italic dim"))
    elif e.kind == "tool_call":
        console.print(f"{tag}[yellow]→[/] {e.message}")
    elif e.kind == "tool_result" and e.data.get("error"):
        console.print(f"{tag}  [red]✗ {e.message}[/]")
    elif e.kind == "tool_result" and e.data.get("tool", "").startswith("run_"):
        style = "green" if e.message.startswith("PASSED") else "red"
        console.print(f"{tag}  [{style}]{e.message}[/]")
    elif e.kind == "agent_finished":
        d = e.data
        console.print(
            f"[green]✓[/] {e.message} [dim]· {d['input_tokens']:,} in / {d['output_tokens']:,} out tokens[/]"
        )
    elif e.kind == "build_verified":
        mark = "[green]✓ verified[/]" if e.data["ok"] else "[red]✗ checks failed[/]"
        console.print(f"{tag}{mark} [dim](we re-ran the checks ourselves)[/]")
    elif e.kind == "smoke_result":
        for p in e.data.get("pages", []):
            console.print(f"  {'[green]✓' if p['ok'] else '[red]✗'} {p['route']}[/]  " + "; ".join(p["errors"])[:200])
        if e.data.get("problem"):
            console.print(f"  [red]{e.data['problem'][:500]}[/]")
    elif e.kind == "output":
        console.print(f"[green]✓ {e.message}[/] {e.data.get('url') or ''}")
    elif e.kind == "notice":
        console.print(f"[yellow]{e.message}[/]")
    elif e.kind == "error":
        console.print(f"[red]✗ {e.message}[/]")


def render_research(r: ResearchReport) -> None:
    facts = "\n".join(
        f"[bold]{label}:[/] {value}"
        for label, value in [("Edition", r.edition), ("Dates", r.dates), ("Format", r.format), ("Website", r.official_url)]
        if value
    )
    console.print(Panel(f"{r.summary}\n\n{facts}".strip(), title=f"[bold]{r.hackathon}", border_style="cyan"))

    if r.tracks:
        table = Table("Track", "Prizes", "Problem statements", title="Tracks", show_lines=True)
        for t in r.tracks:
            table.add_row(t.name, "\n".join(t.prizes) or "-", "\n".join(t.problem_statements) or "-")
        console.print(table)
    if r.judging_criteria:
        console.print("[bold]Judging criteria:[/] " + "; ".join(r.judging_criteria))
    if r.past_winners:
        table = Table("Past winner", "Year", "What it did", "Why it won", title="Past winners", show_lines=True)
        for w in r.past_winners:
            table.add_row(w.project, w.year, w.what_it_did, w.why_it_won)
        console.print(table)
    if r.gaps:
        console.print("[dim]Couldn't find: " + "; ".join(r.gaps) + "[/]")


def render_strategy(s: StrategyReport) -> None:
    table = Table("#", "Project", "Track", "Judge fit", "Impact", "Feasible", "Novel", "Wow", "Score", title="Ranked problem statements", show_lines=True)
    for i, o in enumerate(s.options, 1):
        sc = o.scores
        table.add_row(
            str(i), f"[bold]{o.title}[/]\n{o.solution}", o.track,
            *(str(v) for v in (sc.judge_fit, sc.impact, sc.feasibility, sc.novelty, sc.demo_wow)),
            f"[bold green]{sc.total}[/]",
        )
    console.print(table)
    console.print(Panel(s.recommendation, title="Strategist's recommendation", border_style="green"))


def render_choice(p: ProblemStatement | None) -> None:
    if p is None:
        return
    body = (
        f"[bold]Problem:[/] {p.problem}\n[bold]For:[/] {p.target_users}\n[bold]Solution:[/] {p.solution}\n\n"
        f"[bold]Key features:[/]\n" + "\n".join(f"  • {f}" for f in p.key_features) + "\n\n"
        f"[bold]Wow moment:[/] {p.wow_moment}\n[bold]Stack:[/] {', '.join(p.suggested_stack)}\n"
        + (f"[bold]Sponsor tech:[/] {', '.join(p.sponsor_tech_used)}\n" if p.sponsor_tech_used else "")
        + f"\n[bold]Why it wins:[/] {p.why_it_wins}\n\n[bold]Risks:[/]\n" + "\n".join(f"  • {r}" for r in p.risks)
    )
    console.print(Panel(body, title=f"[bold green]Chosen: {p.title}  ({p.track})", border_style="green"))


def render_plan(plan: BuildPlan) -> None:
    console.print(
        Panel(
            f"[italic]{plan.one_liner}[/]\n\n{plan.architecture}\n\n[bold]Seed data:[/] {plan.seed_data}"
            + (f"\n[bold]AI:[/] {plan.ai_features}" if plan.ai_features else ""),
            title=f"[bold]Build plan: {plan.product_name}",
            border_style="cyan",
        )
    )
    models = Table("Data model", "Fields", title="Data models", show_lines=True)
    for m in plan.data_models:
        models.add_row(m.name, ", ".join(f"{f.name}: {f.type}" for f in m.fields))
    console.print(models)

    api = Table("Method", "Path", "What it does", "Returns", title="API contract", show_lines=True)
    for e in plan.endpoints:
        api.add_row(e.method, e.path, e.summary, e.response)
    console.print(api)

    screens = Table("Screen", "Route", "Purpose", title="Screens", show_lines=True)
    for s in plan.screens:
        screens.add_row(s.name, s.route, s.purpose)
    console.print(screens)

    console.print("[bold]Demo script:[/]\n" + "\n".join(f"  {i}. {step}" for i, step in enumerate(plan.demo_script, 1)))
    if plan.cut_scope:
        console.print("[bold]Cut to fit the time:[/] " + "; ".join(plan.cut_scope))
    extras = plan.python_packages + plan.npm_packages
    if extras:
        console.print("[bold]Extra packages:[/] " + ", ".join(extras))


def render_builds(state: ProjectState) -> None:
    for area, record in state.builds.items():
        render_build(area, record)


def render_outputs(state: ProjectState, store: RunStore) -> None:
    out, run_dir = state.outputs, store.run_dir(state.run_id)
    rows = [
        ("Live app", out.deploy_url and f"{out.deploy_url}  [dim]({out.deploy_status})[/]" or f"[dim]{out.deploy_status}[/]"),
        ("GitHub repo", out.repo_url or "[dim]not created[/]"),
        ("Pitch deck", str(run_dir / out.deck) if out.deck else "[dim]not created[/]"),
        ("App document", str(run_dir / out.doc) if out.doc else "[dim]not created[/]"),
    ]
    console.print(Panel("\n".join(f"[bold]{k:<13}[/] {v}" for k, v in rows), title="Your hackathon kit", border_style="green"))


def render_build(area: str, record: BuildRecord) -> None:
    status = "[green]✓ checks pass[/]" if record.verified else "[red]✗ checks fail[/]"
    body = record.report.summary
    if record.report.known_issues:
        body += "\n\n[bold]Known issues:[/]\n" + "\n".join(f"  • {i}" for i in record.report.known_issues)
    if not record.verified:
        tail = record.check_output[-1500:]
        body += f"\n\n[bold]Last check output:[/]\n[dim]{tail}[/]"
    console.print(Panel(body, title=f"[bold]{area.title()}  {status}", border_style="green" if record.verified else "red"))


if __name__ == "__main__":
    app()
