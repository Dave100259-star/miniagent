"""MCP client 的端到端测试 —— 启动真实的 demo MCP server 子进程, 走完整 JSON-RPC 握手。

证明 miniagent 能作为 MCP host: 发现远程工具 → 命名空间注入本地 registry → 调用执行。
无需 API key (server 是本地玩具进程)。
"""

import sys
from pathlib import Path

from miniagent.agent import Agent
from miniagent.llm import ScriptedLLM, llm_msg, tool_call
from miniagent.mcp import MCPClient, register_mcp_tools
from miniagent.safety import Workspace
from miniagent.tools import ToolRegistry

SERVER = str(Path(__file__).parent.parent / "examples" / "mcp_demo_server.py")


def test_handshake_list_and_call():
    with MCPClient(sys.executable, [SERVER], name="demo") as c:
        names = [t["name"] for t in c.list_tools()]
        assert "add" in names and "reverse" in names
        assert c.call_tool("add", {"a": 2, "b": 3}) == "5"
        assert c.call_tool("reverse", {"text": "abc"}) == "cba"


def test_register_into_registry_with_namespace(tmp_path):
    reg = ToolRegistry()
    with MCPClient(sys.executable, [SERVER], name="demo") as c:
        added = register_mcp_tools(reg, c, namespace="demo")
        assert "mcp__demo__add" in added
        # 远程工具现在像本地工具一样可被 registry 分发执行
        out = reg.call("mcp__demo__add", {"a": 12, "b": 30}, Workspace(tmp_path))
        assert out == "42"


def test_remote_tool_error_is_marked():
    with MCPClient(sys.executable, [SERVER], name="demo") as c:
        # 未知工具 → server 回 isError → 客户端转成 ERROR 文本 (供 agent 自我修正)
        out = c.call_tool("nope", {})
        assert out.startswith("ERROR")


def test_agent_loop_dispatches_mcp_tool(tmp_path):
    # 全栈: 真实 agent 主循环 (ScriptedLLM 驱动) 调用一个挂载进来的 MCP 工具。
    reg = ToolRegistry()
    with MCPClient(sys.executable, [SERVER], name="demo") as c:
        register_mcp_tools(reg, c, namespace="demo")
        script = [
            llm_msg(tool_calls=[tool_call("mcp__demo__add", {"a": 40, "b": 2}, "c1")]),
            llm_msg(content="MCP 工具算出 42。"),
        ]
        agent = Agent(ScriptedLLM(script=script), Workspace(tmp_path), reg, model="test")
        res = agent.run("用 mcp 把 40 和 2 相加")
        assert res.success
        assert res.trace.tool_calls() == 1
