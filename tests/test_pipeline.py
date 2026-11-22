import asyncio

from pptx import Presentation

from jackathon.agents.base import SUBMIT_TOOL
from jackathon.core.events import EventBus
from jackathon.core.models import PageCheck, SmokeReport, TeamProfile
from jackathon.core.pipeline import Pipeline, Toolbox
from jackathon.core.store import RunStore
from jackathon.tools.checks import CheckResult

from .fakes import ScriptedLLM, call

RESEARCH = {
    "hackathon": "TestHack",
    "summary": "A test hackathon.",
    "tracks": [{"name": "Health"}, {"name": "Climate"}],
    "judging_criteria": ["Impact 40%", "Execution 60%"],
}


def option(title: str, judge_fit: int) -> dict:
    return {
        "title": title,
        "track": "Health",
        "problem": "p",
        "target_users": "u",
        "solution": "s",
        "key_features": ["f"],
        "wow_moment": "w",
        "suggested_stack": ["python"],
        "why_it_wins": "y",
        "risks": ["r"],
        "scores": {"judge_fit": judge_fit, "impact": 5, "feasibility": 5, "novelty": 5, "demo_wow": 5},
    }


STRATEGY = {
    "options": [option("Low", 2), option("High", 9), option("Mid", 5)],
    "recommendation": "Pick High.",
}


def plan(name: str) -> dict:
    return {
        "product_name": name,
        "one_liner": "o",
        "architecture": "a",
        "data_models": [{"name": "Item", "fields": [{"name": "id", "type": "integer"}]}],
        "endpoints": [
            {"method": "GET", "path": "/api/items", "summary": "list", "response": "list[Item]"},
            {"method": "GET", "path": "/api/items/{item_id}", "summary": "get", "response": "Item"},
        ],
        "screens": [{"name": "Home", "route": "/", "purpose": "p", "uses_endpoints": ["GET /api/items"]}],
        "seed_data": "s",
        "demo_script": ["open"],
    }


REPORT = {"summary": "built it", "files": ["x"]}
SLIDE = {"title": "Problem", "bullets": ["a"], "notes": "n"}
PITCH = {
    "tagline": "t",
    "slides": [SLIDE] * 5,
    "overview": "o",
    "how_it_works": "h",
    "user_journey": ["1"],
    "whats_next": ["n"],
}


def fake_llms():
    # One scripted model per role, so a second run of the same agent continues the script.
    llms = {
        "research": ScriptedLLM([call(SUBMIT_TOOL, **RESEARCH)]),
        "strategist": ScriptedLLM([call(SUBMIT_TOOL, **STRATEGY)]),
        "architect": ScriptedLLM([call(SUBMIT_TOOL, **plan("First")), call(SUBMIT_TOOL, **plan("Revised"))]),
        "backend": ScriptedLLM([call(SUBMIT_TOOL, **REPORT)] * 3),
        "frontend": ScriptedLLM([call(SUBMIT_TOOL, **REPORT)] * 3),
        "storyteller": ScriptedLLM([call(SUBMIT_TOOL, **PITCH)]),
    }
    return llms


class FakeHuman:
    def __init__(self, choice=0, plan_feedback=(), app_feedback=()):
        self.choice, self.plan_feedback, self.app_feedback = choice, list(plan_feedback), list(app_feedback)
        self.seen = {"titles": [], "plans": [], "reviews": 0}

    async def choose(self, state):
        self.seen["titles"] = [o.title for o in state.strategy.options]
        return self.choice

    async def approve_plan(self, state):
        self.seen["plans"].append(state.plan.product_name)
        return self.plan_feedback.pop(0) if self.plan_feedback else None

    async def review_app(self, state):
        self.seen["reviews"] += 1
        return self.app_feedback.pop(0) if self.app_feedback else None


def fake_tools(smoke_results):
    calls = {"smoke": 0, "repo": 0, "pushes": 0, "deploys": 0}
    results = iter(smoke_results)

    async def verify(area, project, plan):
        return CheckResult(area == "backend", f"{area} checked")  # frontend "fails": our check decides, not the agent

    async def smoke(project, plan, shots_dir, run_dir):
        calls["smoke"] += 1
        ok = next(results)
        return SmokeReport(ok=ok, pages=[PageCheck(route="/", ok=ok, errors=[] if ok else ["Uncaught: boom"])])

    async def create_repo(project, name, description):
        calls["repo"] += 1
        assert (project / "README.md").exists()
        return "https://github.com/me/first"

    async def push_update(project, message):
        calls["pushes"] += 1

    async def deploy(project, name, title, tagline, repo_url):
        calls["deploys"] += 1
        return "me/" + name

    async def wait_deploy(space_id):
        return "running"

    async def render_pdf(html, out):
        out.write_bytes(b"%PDF fake")
        return out

    tools = Toolbox(verify, smoke, create_repo, push_update, deploy, wait_deploy, render_pdf, can_deploy=lambda: True)
    return tools, calls


def test_full_run(tmp_path):
    store = RunStore(tmp_path)
    state = store.create("TestHack", None, TeamProfile(skills=["python"]))
    human = FakeHuman(plan_feedback=["Add a search box."], app_feedback=["Make the header blue."])
    tools, calls = fake_tools([False, True, True])  # broken, fixed by the auto-repair, fine after changes
    llms = fake_llms()

    result = asyncio.run(Pipeline(store, EventBus(), human, llms.__getitem__, tools).run(state))

    assert human.seen["titles"] == ["High", "Mid", "Low"]  # sorted by weighted total
    assert result.strategy.options[0].scores.total == 6.2  # 9*.3 + 5*.25 + 5*.2 + 5*.15 + 5*.1
    assert human.seen["plans"] == ["First", "Revised"]  # plan feedback produced a new plan
    assert result.builds["backend"].verified and not result.builds["frontend"].verified
    assert calls["smoke"] == 3  # first test, re-test after the repair, re-test after the changes
    assert human.seen["reviews"] == 2 and result.change_requests == ["Make the header blue."]
    assert not llms["frontend"].replies and len(llms["backend"].replies) == 1  # frontend: build+repair+change

    out = result.outputs
    assert out.repo_url == "https://github.com/me/first" and calls["pushes"] == 1
    assert out.space_id == "me/revised" and out.deploy_url == "https://me-revised.hf.space"
    assert out.deploy_status == "running"
    run_dir = store.run_dir(state.run_id)
    deck = Presentation(run_dir / out.deck)
    assert len(deck.slides) == 7  # title + 5 + closing
    assert (run_dir / out.doc).read_bytes() == b"%PDF fake"
    readme = (store.project_dir(state.run_id) / "README.md").read_text(encoding="utf-8")
    assert "https://me-revised.hf.space" in readme

    reloaded = store.load(state.run_id)
    assert reloaded.outputs == out
    assert reloaded.usage["frontend"]["input_tokens"] == 30  # three runs, 10 tokens each: usage adds up


def test_resume_skips_finished_stages(tmp_path):
    store = RunStore(tmp_path)
    state = store.create("TestHack", "Health", TeamProfile())

    class QuitAtChoice(FakeHuman):
        async def choose(self, state):
            raise KeyboardInterrupt

    tools, _ = fake_tools([True])
    try:
        asyncio.run(Pipeline(store, EventBus(), QuitAtChoice(), fake_llms().__getitem__, tools).run(state))
    except KeyboardInterrupt:
        pass
    saved = store.load(state.run_id)
    assert saved.strategy is not None and saved.chosen_index is None

    llms = fake_llms()

    def only_new_stages(role):
        assert role not in {"research", "strategist"}, f"{role} already finished; it must not run again"
        return llms[role]

    tools, _ = fake_tools([True])
    resumed = asyncio.run(Pipeline(store, EventBus(), FakeHuman(choice=1), only_new_stages, tools).run(saved))
    assert resumed.chosen.title == "Mid"
    assert resumed.outputs.deck and resumed.outputs.repo_url


def test_deploy_is_skipped_without_a_token(tmp_path):
    store = RunStore(tmp_path)
    state = store.create("TestHack", None, TeamProfile())
    tools, calls = fake_tools([True])
    tools.can_deploy = lambda: False
    result = asyncio.run(Pipeline(store, EventBus(), FakeHuman(), fake_llms().__getitem__, tools).run(state))
    assert calls["deploys"] == 0 and result.outputs.deploy_status.startswith("skipped")
    assert result.outputs.repo_url and result.outputs.deck  # everything else still ships


def test_run_from_an_uploaded_flyer(tmp_path):
    store = RunStore(tmp_path)
    state = store.create("", None, TeamProfile(), "my flyer!.png", b"\x89PNG fake")
    assert state.source.file_name == "my_flyer_.png"  # upload names are sanitized
    llms = fake_llms()
    llms["intake"] = ScriptedLLM([call(SUBMIT_TOOL, name="FlyerHack 2026", themes=["AI"])])
    tools, _ = fake_tools([True])
    result = asyncio.run(Pipeline(store, EventBus(), FakeHuman(), llms.__getitem__, tools).run(state))
    assert result.hackathon == "FlyerHack 2026"  # the name came from the flyer
    first_message = llms["intake"].calls[0][0]
    assert first_message.images and first_message.images[0].media_type == "image/png"
    assert "FlyerHack 2026" in llms["research"].calls[0][0].content  # research got the brief
