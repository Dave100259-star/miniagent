"""miniagent — 一个带评测与可观测性的极简 coding agent。

公开 API:
    Agent            — 智能体主循环
    Workspace        — 沙箱工作区
    default_registry — 默认工具集 (read/write/list/run)
    OpenAICompatLLM  — OpenAI 兼容的真实 LLM 客户端 (DeepSeek/Qwen/GLM...)
    ScriptedLLM      — 确定性假 LLM, 用于测试 / 无 key 演示
"""

from .agent import Agent, AgentResult
from .safety import Workspace
from .tools import default_registry, Tool, ToolRegistry
from .trace import Trace, Step
from .llm import OpenAICompatLLM, ScriptedLLM, LLMResponse, estimate_cost

__all__ = [
    "Agent", "AgentResult", "Workspace", "default_registry", "Tool",
    "ToolRegistry", "Trace", "Step", "OpenAICompatLLM", "ScriptedLLM",
    "LLMResponse", "estimate_cost",
]
