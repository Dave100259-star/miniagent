"""可观测性: 把 agent 每一步 (LLM 调用 / 工具调用) 结构化记录下来。

这是和"教程 clone"拉开差距的关键之一 —— 不是 print 调试, 而是可复盘、
可统计 token/成本/耗时、可落盘成 JSON 的 trace。
"""

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class Step:
    kind: str                       # "llm" | "tool"
    name: str                       # 模型名 或 工具名
    detail: dict = field(default_factory=dict)
    tokens: int = 0
    cost: float = 0.0
    seconds: float = 0.0


def _short(v, n: int = 80) -> str:
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    return s if len(s) <= n else s[:n] + "…"


class Trace:
    """一次 agent 运行的完整轨迹。"""

    def __init__(self):
        self.steps: list[Step] = []
        self.start = time.time()

    def add(self, step: Step) -> None:
        self.steps.append(step)

    def llm_calls(self) -> int:
        return sum(1 for s in self.steps if s.kind == "llm")

    def tool_calls(self) -> int:
        return sum(1 for s in self.steps if s.kind == "tool")

    @property
    def total_tokens(self) -> int:
        return sum(s.tokens for s in self.steps)

    @property
    def total_cost(self) -> float:
        return round(sum(s.cost for s in self.steps), 6)

    @property
    def elapsed(self) -> float:
        return round(time.time() - self.start, 2)

    def summary(self) -> dict:
        return {
            "steps": len(self.steps),
            "llm_calls": self.llm_calls(),
            "tool_calls": self.tool_calls(),
            "tokens": self.total_tokens,
            "cost_usd": self.total_cost,
            "seconds": self.elapsed,
        }

    def pretty(self) -> str:
        lines = []
        for i, s in enumerate(self.steps, 1):
            if s.kind == "llm":
                tcs = s.detail.get("tool_calls") or []
                tag = f"→ 调用 {tcs}" if tcs else "→ 给出最终回答"
                lines.append(f"[{i:>2}] 🤖 LLM {tag}  ({s.tokens}tok · {s.seconds:.1f}s)")
            else:
                args = _short(s.detail.get("args", {}))
                lines.append(f"[{i:>2}] 🔧 {s.name}({args})  ({s.seconds:.1f}s)")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"summary": self.summary(), "steps": [asdict(s) for s in self.steps]}

    def save(self, path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
