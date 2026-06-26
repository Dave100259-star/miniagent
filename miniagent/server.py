"""FastAPI 服务层 —— 把 miniagent 从"脚本"升级为"后端服务",带缓存与持久化存储。

接口:
  POST /run          提交任务, 同步执行 (或命中缓存直接返回), 结果落库
  GET  /runs         列出历史运行 (摘要)
  GET  /runs/{id}    取单次运行 (含完整 trace)
  GET  /stats        聚合统计 (总运行数 / 总 token / 总成本 / 缓存命中率)
  GET  /healthz      健康检查

- 缓存 (LRUCache): 相同任务 (task + max_steps 的哈希) 直接复用上次结果,
  省去重复的 LLM 调用与费用 —— 体现后端工程里的成本意识。
- 存储 (SQLite, 零额外依赖): 持久化每次运行的回答 / trace / token / 成本 / 时间。
- LLM 工厂可注入 (默认连真实模型; 测试注入 ScriptedLLM), 因此服务层无需 key 即可测。

启动:  uvicorn --factory miniagent.server:create_app
"""

import hashlib
import json
import shutil
import sqlite3
import tempfile
import threading
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

from .agent import Agent
from .safety import Workspace
from .tools import default_registry


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key(task: str, max_steps: int) -> str:
    return hashlib.sha256(f"{max_steps}\x00{task}".encode("utf-8")).hexdigest()


class LRUCache:
    """带命中统计的最小 LRU 缓存。"""

    def __init__(self, maxsize: int = 128):
        self.maxsize = maxsize
        self._d: OrderedDict = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, k):
        if k in self._d:
            self._d.move_to_end(k)
            self.hits += 1
            return self._d[k]
        self.misses += 1
        return None

    def put(self, k, v):
        self._d[k] = v
        self._d.move_to_end(k)
        if len(self._d) > self.maxsize:
            self._d.popitem(last=False)

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return round(self.hits / total, 4) if total else 0.0


class RunStore:
    """运行记录的 SQLite 持久化。"""

    def __init__(self, db_path: str = "miniagent_runs.db"):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()           # 串行化写入 (FastAPI 同步端点跑在线程池)
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS runs(
                 id TEXT PRIMARY KEY, task TEXT, answer TEXT, success INTEGER,
                 steps INTEGER, tokens INTEGER, cost REAL, cached INTEGER,
                 trace_json TEXT, created_at TEXT)"""
        )
        self.conn.commit()

    def save(self, rec: dict) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO runs VALUES (:id,:task,:answer,:success,:steps,:tokens,"
                ":cost,:cached,:trace_json,:created_at)",
                {**rec, "trace_json": json.dumps(rec.get("trace"), ensure_ascii=False),
                 "success": int(rec["success"]), "cached": int(rec["cached"])},
            )
            self.conn.commit()

    def get(self, run_id: str):
        r = self.conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["success"], d["cached"] = bool(d["success"]), bool(d["cached"])
        d["trace"] = json.loads(d.pop("trace_json") or "null")
        return d

    def list(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id,task,success,steps,tokens,cost,cached,created_at "
            "FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["success"], d["cached"] = bool(d["success"]), bool(d["cached"])
            out.append(d)
        return out

    def stats(self) -> dict:
        r = self.conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(tokens),0) tok, COALESCE(SUM(cost),0) cost, "
            "COALESCE(SUM(cached),0) cached FROM runs"
        ).fetchone()
        return {"total_runs": r["n"], "total_tokens": r["tok"],
                "total_cost": round(r["cost"], 6), "served_from_cache": r["cached"]}


def create_app(llm_factory=None, db_path: str = "miniagent_runs.db", cache_size: int = 128):
    """构造 FastAPI 应用。llm_factory: ()->BaseLLM, 默认连真实模型, 测试可注入 ScriptedLLM。"""
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel

    if llm_factory is None:
        from .llm import OpenAICompatLLM
        def llm_factory():
            return OpenAICompatLLM()

    app = FastAPI(title="miniagent service", version="0.1.0")
    store = RunStore(db_path)
    cache = LRUCache(cache_size)

    class RunReq(BaseModel):
        task: str
        max_steps: int = 12

    def _public(rec: dict) -> dict:
        return {k: rec[k] for k in
                ("id", "task", "answer", "success", "steps", "tokens", "cost", "cached")}

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.post("/run")
    def run(req: RunReq):
        key = _key(req.task, req.max_steps)
        hit = cache.get(key)
        if hit is not None:
            rec = {**hit, "id": str(uuid.uuid4()), "cached": True, "created_at": _now()}
            store.save(rec)
            return _public(rec)

        tmp = Path(tempfile.mkdtemp(prefix="srv_"))
        try:
            agent = Agent(llm_factory(), Workspace(tmp), default_registry(),
                          max_steps=req.max_steps)
            result = agent.run(req.task)
            summ = agent.trace.summary()
            rec = {"id": str(uuid.uuid4()), "task": req.task, "answer": result.answer,
                   "success": result.success, "steps": result.steps,
                   "tokens": summ["tokens"], "cost": summ["cost_usd"], "cached": False,
                   "trace": agent.trace.to_dict(), "created_at": _now()}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        # 缓存可复用字段 (不含每次都不同的 id/时间)
        cache.put(key, {k: rec[k] for k in
                        ("task", "answer", "success", "steps", "tokens", "cost", "trace")})
        store.save(rec)
        return _public(rec)

    @app.get("/runs")
    def runs(limit: int = 50):
        return {"runs": store.list(limit)}

    @app.get("/runs/{run_id}")
    def get_run(run_id: str):
        r = store.get(run_id)
        if not r:
            raise HTTPException(status_code=404, detail="run not found")
        return r

    @app.get("/stats")
    def stats():
        s = store.stats()
        s.update(cache_hits=cache.hits, cache_misses=cache.misses,
                 cache_hit_rate=cache.hit_rate)
        return s

    return app
