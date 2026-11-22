import asyncio
import json

import pytest

from jackathon.core.models import BuildPlan, Endpoint
from jackathon.scaffold import scaffold
from jackathon.tools.base import ToolError
from jackathon.tools.checks import runtime_pitfalls, unreferenced_endpoints
from jackathon.tools.files import Workspace, file_tools
from jackathon.tools.runner import safe_env

from .test_pipeline import plan


def tools_by_name(ws):
    return {t.name: t for t in file_tools(ws)}


@pytest.mark.parametrize("bad", ["../escape.txt", "a/../../escape.txt", "/etc/passwd", "C:/Windows/x", "node_modules/x.js"])
def test_workspace_refuses_paths_outside_or_generated(tmp_path, bad):
    with pytest.raises(ToolError):
        Workspace(tmp_path).path(bad)


def test_file_tools_round_trip(tmp_path):
    ws = Workspace(tmp_path)
    tools = tools_by_name(ws)
    run = lambda name, **args: asyncio.run(tools[name].run(args))  # noqa: E731

    assert "Created src/App.jsx" in run("write_file", path="src/App.jsx", content="a\nb\n")
    assert run("read_file", path="src/App.jsx") == "a\nb\n"
    assert "src/App.jsx  (2 lines)" in run("list_files")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "huge.js").write_text("x")
    assert "node_modules" not in run("list_files")
    run("delete_file", path="src/App.jsx")
    assert run("list_files") == "(empty folder)"


def test_endpoint_paths_must_be_under_api():
    with pytest.raises(ValueError):
        Endpoint(method="GET", path="/items", summary="s", response="r")
    assert Endpoint(method="GET", path="/api/items/", summary="s", response="r").path == "/api/items"


def test_scaffold_copies_templates_and_adds_safe_packages(tmp_path):
    p = BuildPlan.model_validate(
        {**plan("X"), "python_packages": ["numpy", "evil; rm -rf /"], "npm_packages": ["recharts", "--global"]}
    )
    scaffold(tmp_path, p)
    assert (tmp_path / "frontend" / "src" / "api.js").exists()
    reqs = (tmp_path / "backend" / "requirements.txt").read_text().split()
    assert "numpy" in reqs and not any("rm" in r for r in reqs)
    deps = json.loads((tmp_path / "frontend" / "package.json").read_text())["dependencies"]
    assert "recharts" in deps and "--global" not in deps
    assert json.loads((tmp_path / ".jackathon" / "contract.json").read_text())[1] == ["GET", "/api/items/{item_id}"]


def test_unreferenced_endpoints(tmp_path):
    p = BuildPlan.model_validate(plan("X"))
    (tmp_path / "App.jsx").write_text('api(`/api/items/${id}`)')
    assert unreferenced_endpoints(tmp_path, p) == []
    (tmp_path / "App.jsx").write_text("nothing here")
    assert unreferenced_endpoints(tmp_path, p) == ["GET /api/items", "GET /api/items/{item_id}"]


def test_runtime_pitfalls_catches_a_second_router(tmp_path):
    (tmp_path / "main.jsx").write_text("<BrowserRouter><App /></BrowserRouter>")  # allowed here
    (tmp_path / "App.jsx").write_text("<Routes><Route path='/' /></Routes>")
    assert runtime_pitfalls(tmp_path) == []
    (tmp_path / "App.jsx").write_text("<BrowserRouter><Routes /></BrowserRouter>")
    assert "src/App.jsx renders a Router" in runtime_pitfalls(tmp_path)[0]


def test_safe_env_drops_secrets(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "secret")
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    env = safe_env()
    assert "GEMINI_API_KEY" not in env and "GITHUB_TOKEN" not in env
    assert "PATH" in env or "Path" in env
