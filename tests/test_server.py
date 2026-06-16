"""FastAPI 服务层测试 —— 注入 ScriptedLLM, 无需 API key。

若环境未装 fastapi 则整文件跳过 (核心 44 测试不依赖任何第三方包)。
"""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from miniagent.llm import ScriptedLLM, llm_msg  # noqa: E402
from miniagent.server import LRUCache, create_app  # noqa: E402


def _client():
    # 假模型: 每次都直接给最终回答 "done"(不调工具), 让服务/缓存/存储管线可确定性测试
    def factory():
        return ScriptedLLM(rule=lambda msgs: llm_msg("done"))
    return TestClient(create_app(llm_factory=factory, db_path=":memory:"))


def test_healthz():
    assert _client().get("/healthz").json()["status"] == "ok"


def test_run_persists_and_is_retrievable():
    c = _client()
    r = c.post("/run", json={"task": "做点什么"}).json()
    assert r["answer"] == "done" and r["cached"] is False
    got = c.get(f"/runs/{r['id']}").json()
    assert got["task"] == "做点什么"
    assert "trace" in got and got["trace"]["summary"]["llm_calls"] >= 1


def test_cache_hit_on_identical_task():
    c = _client()
    first = c.post("/run", json={"task": "一样的任务"}).json()
    second = c.post("/run", json={"task": "一样的任务"}).json()
    assert first["cached"] is False
    assert second["cached"] is True            # 第二次同任务命中缓存
    stats = c.get("/stats").json()
    assert stats["cache_hits"] >= 1
    assert stats["cache_hit_rate"] > 0


def test_runs_list_and_stats_accumulate():
    c = _client()
    c.post("/run", json={"task": "t1"})
    c.post("/run", json={"task": "t2"})
    assert len(c.get("/runs").json()["runs"]) >= 2
    assert c.get("/stats").json()["total_runs"] >= 2


def test_missing_run_returns_404():
    assert _client().get("/runs/nope").status_code == 404


def test_lru_eviction_and_hit_rate():
    cache = LRUCache(maxsize=2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("c", 3)            # 淘汰最旧的 "a"
    assert cache.get("a") is None
    assert cache.get("b") == 2
    assert 0.0 < cache.hit_rate <= 1.0
