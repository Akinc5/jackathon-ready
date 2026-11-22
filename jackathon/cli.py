"""Command-line interface. Try:  jackathon run "HackMIT" --track "Healthcare"

This file only handles input and display. All the real work happens in
`core.pipeline`, so the web UI (Phase 5) can reuse the exact same pipeline.
"""

from __future__ import annotations

import asyncio
import os

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import IntPrompt
from rich.table import Table
from rich.text import Text

from .agents import AgentError
from .config import PROVIDERS, RUNS_DIR, model_for
from .core.events import Event, EventBus
from .core.models import ProblemStatement, ProjectState, ResearchReport, StrategyReport, TeamProfile
from .core.pipeline import Pipeline
from .core.store import RunStore
from .llm import LLMError, create_llm

app = typer.Typer(help="Jackathon: AI agents that help you win hackathons.", no_args_is_help=True)
console = Console()
ROLES = ["research", "strategist"]


# --- commands ----------------------------------------------------------------


@app.command()
def run(
    hackathon: str = typer.Argument(help="Name of the hackathon, e.g. 'Smart India Hackathon 2026'"),
    track: str = typer.Option(None, "--track", "-t", help="Track you want to compete in"),
    skills: str = typer.Option("", "--skills", "-s", help="Team skills, comma separated"),
    hours: int = typer.Option(36, "--hours", help="How long you have to build"),
    team_size: int = typer.Option(None, "--team-size", help="Number of people on the team"),
):
    """Start a new run: research the hackathon and pick a problem statement."""
    _check_models()
    store = RunStore(RUNS_DIR)
    team = TeamProfile(
        skills=[s.strip() for s in skills.split(",") if s.strip()], hours=hours, team_size=team_size
    )
    state = store.create(hackathon, track, team)
    console.print(f"[dim]Run id: {state.run_id}  (resume with: jackathon resume {state.run_id})[/]")
    _execute(store, state)


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


@app.command("runs")
def list_runs():
    """List your previous runs."""
    states = RunStore(RUNS_DIR).list()
    if not states:
        console.print("No runs yet. Start one with: jackathon run \"<hackathon name>\"")
        return
    table = Table("Run id", "Hackathon", "Track", "Stage", "Chosen")
    for s in states:
        stage = "picked" if s.chosen else "ranked" if s.strategy else "researched" if s.research else "started"
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
    pipeline = Pipeline(store, bus, choose=ask_user_to_choose)
    try:
        state = asyncio.run(pipeline.run(state))
    except (LLMError, AgentError) as e:
        console.print(f"\n[red bold]Stopped:[/] {e}")
        console.print(f"[dim]Progress is saved. Fix the problem, then: jackathon resume {state.run_id}[/]")
        raise typer.Exit(1)
    except KeyboardInterrupt:
        console.print(f"\n[yellow]Interrupted.[/] Resume with: jackathon resume {state.run_id}")
        raise typer.Exit(130)

    render_choice(state.chosen)
    total_in = sum(u["input_tokens"] for u in state.usage.values())
    total_out = sum(u["output_tokens"] for u in state.usage.values())
    console.print(f"[dim]Tokens used: {total_in:,} in / {total_out:,} out[/]")
    console.print("[dim]Next up (Phase 2): the Architect agent turns this into a build plan.[/]")


async def ask_user_to_choose(state: ProjectState) -> int:
    if state.research:
        render_research(state.research)
    render_strategy(state.strategy)
    n = len(state.strategy.options)
    pick = IntPrompt.ask("Which problem statement do you want to build?", choices=[str(i) for i in range(1, n + 1)], default=1)
    return pick - 1


# --- display -----------------------------------------------------------------


def render_event(e: Event) -> None:
    if e.kind == "stage_started":
        console.rule(f"[bold magenta]{e.message}")
    elif e.kind == "agent_started":
        console.print(f"[bold cyan]{e.agent}[/] agent working [dim]({e.data.get('model')})[/]")
    elif e.kind == "agent_thought":
        text = e.message if len(e.message) <= 300 else e.message[:299] + "…"
        console.print(Text(f"  {text}", style="italic dim"))
    elif e.kind == "tool_call":
        console.print(f"  [yellow]→[/] {e.message}")
    elif e.kind == "tool_result" and e.data.get("error"):
        console.print(f"    [red]✗ {e.message}[/]")
    elif e.kind == "agent_finished":
        d = e.data
        console.print(
            f"[green]✓[/] {e.message} [dim]· {d['input_tokens']:,} in / {d['output_tokens']:,} out tokens[/]"
        )


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


if __name__ == "__main__":
    app()
