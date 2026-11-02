from pydantic import BaseModel

from jackathon.core.models import StrategyReport
from jackathon.llm import Message, ToolCall
from jackathon.llm.anthropic_llm import _to_anthropic
from jackathon.llm.openai_llm import _to_openai
from jackathon.tools import json_schema
from jackathon.tools.base import ToolError
from jackathon.tools.web import _check_url, html_to_text

import pytest

CONVERSATION = [
    Message.user("research HackMIT"),
    Message(role="assistant", content="Searching.", tool_calls=[
        ToolCall("a", "web_search", {"query": "HackMIT"}),
        ToolCall("b", "fetch_page", {"url": "https://hackmit.org"}),
    ]),
    Message.tool_result("a", "results..."),
    Message.tool_result("b", "404", is_error=True),
    Message.user("You are out of steps."),
]


def test_anthropic_groups_tool_results_into_one_user_message():
    out = _to_anthropic(CONVERSATION)
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert [b["type"] for b in out[1]["content"]] == ["text", "tool_use", "tool_use"]
    last = out[2]["content"]
    assert [b["type"] for b in last] == ["tool_result", "tool_result", "text"]
    assert last[1]["is_error"] is True


def test_anthropic_replays_raw_content_verbatim():
    raw = [{"type": "thinking", "thinking": "", "signature": "sig"}, {"type": "text", "text": "hi"}]
    out = _to_anthropic([Message.user("x"), Message(role="assistant", content="hi", raw=raw, raw_provider="anthropic")])
    assert out[1]["content"] is raw


def test_openai_format():
    out = _to_openai(CONVERSATION)
    assert [m["role"] for m in out] == ["user", "assistant", "tool", "tool", "user"]
    assert out[1]["tool_calls"][0]["function"]["arguments"] == '{"query": "HackMIT"}'
    assert out[3]["content"].startswith("ERROR:")


def test_json_schema_has_no_refs():
    schema = json_schema(StrategyReport)
    assert "$defs" not in schema and "$ref" not in str(schema)
    scores = schema["properties"]["options"]["items"]["properties"]["scores"]
    assert "judge_fit" in scores["properties"]
    assert "total" not in scores["properties"]  # computed in code, not asked of the model


def test_html_to_text_strips_scripts_and_nav():
    title, text = html_to_text(
        "<html><head><title>HackX</title><script>evil()</script></head>"
        "<body><nav>menu</nav><h1>Tracks</h1><p>AI for Good</p></body></html>"
    )
    assert title == "HackX"
    assert "Tracks" in text and "AI for Good" in text
    assert "evil" not in text and "menu" not in text


@pytest.mark.parametrize("url", ["http://localhost:8000", "http://127.0.0.1/", "http://192.168.1.1", "file:///etc/passwd"])
def test_fetch_blocks_local_urls(url):
    with pytest.raises(ToolError):
        _check_url(url)


def test_fetch_allows_public_urls():
    _check_url("https://devpost.com/hackathons")
