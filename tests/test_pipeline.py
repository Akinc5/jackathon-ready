import asyncio

from jackathon.agents.base import SUBMIT_TOOL
from jackathon.core.events import EventBus
from jackathon.core.models import TeamProfile
from jackathon.core.pipeline import Pipeline
from jackathon.core.store import RunStore

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


def fake_llms():
    scripts = {
        "research": [call(SUBMIT_TOOL, **RESEARCH)],
        "strategist": [call(SUBMIT_TOOL, **STRATEGY)],
    }
    return lambda role: ScriptedLLM(scripts[role])


def test_full_run_ranks_by_score_and_saves(tmp_path):
    store = RunStore(tmp_path)
    state = store.create("TestHack", None, TeamProfile(skills=["python"]))
    seen = {}

    async def choose(strategy):
        seen["titles"] = [o.title for o in strategy.options]
        return 0

    result = asyncio.run(Pipeline(store, EventBus(), choose, llm_factory=fake_llms()).run(state))

    assert seen["titles"] == ["High", "Mid", "Low"]  # sorted by weighted total
    assert result.chosen.title == "High"
    assert result.strategy.options[0].scores.total == 6.2  # 9*.3 + 5*.25 + 5*.2 + 5*.15 + 5*.1

    reloaded = store.load(state.run_id)
    assert reloaded.chosen.title == "High"
    assert set(reloaded.usage) == {"research", "strategist"}


def test_resume_skips_finished_stages(tmp_path):
    store = RunStore(tmp_path)
    state = store.create("TestHack", "Health", TeamProfile())

    async def choose(strategy):
        raise KeyboardInterrupt  # user quits at the checkpoint

    try:
        asyncio.run(Pipeline(store, EventBus(), choose, llm_factory=fake_llms()).run(state))
    except KeyboardInterrupt:
        pass

    saved = store.load(state.run_id)
    assert saved.strategy is not None and saved.chosen_index is None

    def no_llm(role):
        raise AssertionError("finished stages must not call the model again")

    async def choose_second(strategy):
        return 1

    resumed = asyncio.run(Pipeline(store, EventBus(), choose_second, llm_factory=no_llm).run(saved))
    assert resumed.chosen.title == "Mid"
