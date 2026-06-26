"""Agent 主循环。

这就是整个项目的"发动机": LLM → 决定调哪个工具 → 执行 → 把结果喂回去 → 再决策,
直到模型不再调用工具 (给出最终文本回答) 或达到步数上限。

亮点:
- 工具报错不会让流程崩溃, 而是把错误文本回灌给模型, 让它自我修正 (self-recovery)。
- 每一步都记进 Trace, 可统计 token / 成本 / 耗时, 可落盘复盘。
"""

import json
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .llm import BaseLLM, estimate_cost
from .prompts import SYSTEM_PROMPT
from .safety import Workspace
from .tools import ToolRegistry
from .trace import Step, Trace


def _looks_like_failure(result: str) -> bool:
    """工具结果是否表示"失败"。
    涵盖两类: ① 工具抛出的硬异常/错误 (以 ERROR 开头);
    ② run_command 的非零退出 (返回形如 'exit=1\\n...', 测试跑挂即属此类)。
    用于消融实验里判断"是否该触发自我修正"。"""
    if result.startswith("ERROR"):
        return True
    if result.startswith("exit=") and not result.startswith("exit=0"):
        return True
    return False


@dataclass
class AgentResult:
    answer: str
    steps: int
    success: bool          # True=正常给出最终回答; False=撞到 max_steps
    trace: Trace


class Agent:
    def __init__(self, llm: BaseLLM, workspace: Workspace, registry: ToolRegistry,
                 max_steps: int = 12, system: str = SYSTEM_PROMPT,
                 model: str = None, on_event: Optional[Callable[[Step], None]] = None,
                 compact_after: int = 30, compact_keep_recent: int = 8,
                 token_budget: int = 8000, loop_guard: int = 4,
                 recover_errors: bool = True):
        self.llm = llm
        self.workspace = workspace
        self.registry = registry
        self.max_steps = max_steps
        self.system = system
        self.model = model or getattr(llm, "model", "unknown")
        self.on_event = on_event
        self.compact_after = compact_after          # 消息数超过此值就压缩
        self.compact_keep_recent = compact_keep_recent
        self.token_budget = token_budget            # 近似 token 预算, 超过也触发压缩
        self.loop_guard = loop_guard                # 连续重复相同工具调用达此次数即判定卡死
        # recover_errors=True: 观察到失败 (异常/测试跑挂) 后回灌错误、让模型自我修正;
        # False: 一遇失败就终止 (消融实验用, 量化"自我修正"回路的价值)。
        self.recover_errors = recover_errors
        self.trace = Trace()

    def _emit(self, step: Step) -> None:
        self.trace.add(step)
        if self.on_event:
            self.on_event(step)

    def _assistant_message(self, resp) -> dict:
        msg = {"role": "assistant", "content": resp.content or ""}
        if resp.tool_calls:
            msg["tool_calls"] = [
                {"id": tc["id"], "type": "function",
                 "function": {"name": tc["name"],
                              "arguments": json.dumps(tc["arguments"], ensure_ascii=False)}}
                for tc in resp.tool_calls
            ]
        return msg

    def _compact(self, messages: list[dict]) -> list[dict]:
        """上下文压缩 (bounded context)。

        历史消息过长时, 截断更早的工具输出, 只保留 system + 首条任务 + 最近若干轮。
        关键: 只修改 content 字符串、绝不删除消息, 以保证 assistant 的 tool_calls
        与对应 tool 结果的配对不被破坏 (否则 OpenAI 兼容接口会报错)。
        """
        # 触发条件: 消息条数过多 或 近似 token 占用超预算 (二者满足其一)。
        # token 近似: 按内容字符数估算 (中文≈1 token/字, 英文≈1/4), 取 /3 作折中。
        approx_tokens = sum(len(str(m.get("content") or "")) for m in messages) // 3
        if len(messages) <= self.compact_after and approx_tokens <= self.token_budget:
            return messages
        head = messages[:2]                          # system + 首条 user 任务
        body = messages[2:]
        keep = self.compact_keep_recent
        older, recent = body[:-keep], body[-keep:]
        compacted = []
        for m in older:
            if m.get("role") == "tool" and len(m.get("content", "")) > 160:
                compacted.append({**m, "content": m["content"][:120] + " …[历史工具输出已压缩]"})
            else:
                compacted.append(m)
        return head + compacted + recent

    def run(self, task: str) -> AgentResult:
        messages = [
            {"role": "system", "content": self.system},
            {"role": "user", "content": task},
        ]
        schemas = self.registry.schemas()
        last_sig, repeats = None, 0          # 无进展检测: 连续相同的工具调用签名

        for i in range(self.max_steps):
            messages = self._compact(messages)
            t0 = time.time()
            resp = self.llm.chat(messages, tools=schemas)
            self._emit(Step(
                kind="llm", name=self.model,
                detail={"content": (resp.content or "")[:500],
                        "tool_calls": [tc["name"] for tc in resp.tool_calls]},
                tokens=resp.prompt_tokens + resp.completion_tokens,
                cost=estimate_cost(self.model, resp.prompt_tokens, resp.completion_tokens),
                seconds=round(time.time() - t0, 3),
            ))
            messages.append(self._assistant_message(resp))

            # 没有工具调用 = 模型给出了最终回答, 结束。
            if not resp.tool_calls:
                return AgentResult(resp.content, i + 1, True, self.trace)

            # 无进展检测: 模型反复发出完全相同的工具调用 (同名同参) → 判定卡死, 提前止损,
            # 避免"改→错→改回→又错"或同一失败调用空转烧 token。
            sig = json.dumps([[tc["name"], tc["arguments"]] for tc in resp.tool_calls],
                             sort_keys=True, ensure_ascii=False)
            repeats = repeats + 1 if sig == last_sig else 0
            last_sig = sig
            if repeats >= self.loop_guard:
                return AgentResult(
                    "(检测到重复且无进展的工具调用, 已提前终止)", i + 1, False, self.trace)

            # 依次执行工具调用, 把结果 (含错误) 回灌。
            for tc in resp.tool_calls:
                t1 = time.time()
                try:
                    result = self.registry.call(tc["name"], tc["arguments"], self.workspace)
                except Exception as e:  # 工具崩了也不让 agent 崩 —— 回灌错误让它自救
                    result = f"ERROR: {type(e).__name__}: {e}"
                result = str(result)
                self._emit(Step(
                    kind="tool", name=tc["name"],
                    detail={"args": tc["arguments"], "result": result[:500]},
                    seconds=round(time.time() - t1, 3),
                ))
                # 消融开关: 关闭自我修正时, 一遇到失败 (硬异常 或 命令非零退出/测试跑挂)
                # 就终止 —— 用来量化"观察失败 → 重试修复"这条自我修正回路的价值。
                if not self.recover_errors and _looks_like_failure(result):
                    return AgentResult(
                        f"(自我修正已关闭: 工具 {tc['name']} 返回失败, 终止)",
                        i + 1, False, self.trace)
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})

        return AgentResult("(已达到最大步数, 任务可能未完成)", self.max_steps, False, self.trace)
