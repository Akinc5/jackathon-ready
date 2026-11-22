import time

from fastapi.testclient import TestClient

from jackathon.core.pipeline import Pipeline
from jackathon.core.store import RunStore
from jackathon.web.server import create_app

from .test_pipeline import fake_llms, fake_tools


class NoPreview:
    def __init__(self, project):
        self.url, self.error = "http://preview.test", ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass


def make_client(tmp_path):
    store = RunStore(tmp_path)
    factory = lambda s, bus, human: Pipeline(s, bus, human, fake_llms().__getitem__, fake_tools([True])[0])  # noqa: E731
    return TestClient(create_app(store, factory, preview=NoPreview, check_models=False)), store


def wait_for(client, run_id, predicate, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f"/api/runs/{run_id}").json()
        if predicate(data):
            return data
        time.sleep(0.05)
    raise AssertionError(f"timed out; last status: {data['status']}, question: {data['question']}")


def test_web_run_end_to_end(tmp_path):
    client, store = make_client(tmp_path)
    with client:
        assert client.get("/").status_code == 200
        run_id = client.post("/api/runs", data={"text": "TestHack"}).json()["run_id"]

        data = wait_for(client, run_id, lambda d: d["question"] and d["question"]["kind"] == "choose")
        assert [o["title"] for o in data["state"]["strategy"]["options"]] == ["High", "Mid", "Low"]
        client.post(f"/api/runs/{run_id}/answer", json={"index": 0})

        wait_for(client, run_id, lambda d: d["question"] and d["question"]["kind"] == "plan")
        client.post(f"/api/runs/{run_id}/answer", json={"feedback": None})

        data = wait_for(client, run_id, lambda d: d["question"] and d["question"]["kind"] == "review")
        assert data["question"]["preview_url"] == "http://preview.test"
        client.post(f"/api/runs/{run_id}/answer", json={"feedback": None})

        data = wait_for(client, run_id, lambda d: d["status"] == "done")
        out = data["state"]["outputs"]
        assert out["repo_url"] and out["deploy_url"] and out["deck"] and out["doc"]
        deck = client.get(f"/api/runs/{run_id}/file/{out['deck']}")
        assert deck.status_code == 200 and deck.content[:2] == b"PK"  # a .pptx is a zip file
        assert client.get(f"/api/runs/{run_id}/file/state.json").status_code == 404  # only deliverables are served
        assert client.get(f"/api/runs/{run_id}/file/../../secret").status_code == 404
        assert client.post(f"/api/runs/{run_id}/answer", json={}).status_code == 409  # nothing to answer now


def test_web_requires_input(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        assert client.post("/api/runs", data={"text": "  "}).status_code == 400
