"""LLM 抽象层。

- OpenAICompatLLM: 任意 OpenAI 兼容 provider (DeepSeek / Qwen / GLM / OpenAI...)。
- ScriptedLLM: 确定性"假"模型, 让测试和演示无需真实 key、无需烧 token。
  这是能让 agent 被单元测试覆盖的关键设计 —— 大多数教程 clone 都做不到。
"""

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)  # [{id, name, arguments(dict)}]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw: Any = None


# 各家近似单价 (USD / 1M tokens), 仅用于成本估算, 以官网为准。
PRICES = {
    "deepseek": (0.27, 1.10),
    "qwen": (0.40, 1.20),
    "glm": (0.10, 0.10),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.0),
}


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    m = (model or "").lower()
    for key, (pin, pout) in PRICES.items():
        if key in m:
            return round(prompt_tokens / 1e6 * pin + completion_tokens / 1e6 * pout, 6)
    return 0.0


class BaseLLM:
    def chat(self, messages: list[dict], tools: Optional[list] = None) -> LLMResponse:
        raise NotImplementedError


class OpenAICompatLLM(BaseLLM):
    """走 OpenAI 兼容接口的真实模型。默认读 LLM_*, 兼容旧的 DEEPSEEK_* 环境变量。"""

    def __init__(self, model: str = None, api_key: str = None, base_url: str = None,
                 temperature: float = 0.0):
        self.model = model or os.getenv("LLM_MODEL") or os.getenv("DEEPSEEK_MODEL") or "deepseek-chat"
        self.api_key = api_key or os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
        self.base_url = (base_url or os.getenv("LLM_BASE_URL")
                         or os.getenv("DEEPSEEK_API_URL") or "https://api.deepseek.com")
        self.temperature = temperature
        key = (self.api_key or "").strip()
        # 必须是真实 key: 非空、纯 ASCII、且不是占位符。
        # 占位符若漏到 HTTP 层会以一句晦涩的 UnicodeEncodeError 崩掉, 这里提前给人话。
        if not key or not key.isascii() or "your-key" in key or "在这里" in (self.api_key or ""):
            raise RuntimeError(
                "API key 缺失或仍是占位符。请在 .env 把 LLM_API_KEY 换成真实 key "
                "(形如 sk-xxxx)，保存后重试。无 key 想体验核心逻辑请跑: python -m pytest -q"
            )
        self.api_key = key
        from openai import OpenAI  # 延迟导入: 不用真实模型时无需安装 openai
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def chat(self, messages, tools=None) -> LLMResponse:
        kwargs = {"model": self.model, "messages": messages, "temperature": self.temperature}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        resp = self.client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        tool_calls = []
        for tc in (msg.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_calls.append({"id": tc.id, "name": tc.function.name, "arguments": args})
        usage = resp.usage
        return LLMResponse(
            content=msg.content or "",
            tool_calls=tool_calls,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            raw=resp,
        )


class ScriptedLLM(BaseLLM):
    """确定性假模型。

    两种用法:
      - script: 预设一串 LLMResponse, 按顺序播放。
      - rule:   函数 (messages)->LLMResponse, 根据对话动态决定下一步。
    """

    def __init__(self, script: list = None, rule: Callable = None):
        self.script = list(script or [])
        self.rule = rule
        self.calls = 0

    def chat(self, messages, tools=None) -> LLMResponse:
        self.calls += 1
        if self.rule is not None:
            return self.rule(messages)
        if self.script:
            return self.script.pop(0)
        return LLMResponse(content="(脚本已耗尽)")


# ── 构造 ScriptedLLM 响应的便捷函数 ──

def tool_call(name: str, arguments: dict, id: str = None) -> dict:
    return {"id": id or f"call_{name}", "name": name, "arguments": arguments}


def llm_msg(content: str = "", tool_calls: list = None,
            pt: int = 10, ct: int = 5) -> LLMResponse:
    return LLMResponse(content=content, tool_calls=tool_calls or [],
                       prompt_tokens=pt, completion_tokens=ct)
