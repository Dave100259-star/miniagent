"""极简 MCP (Model Context Protocol) stdio 客户端 —— 让 miniagent 成为 MCP host。

MCP 是给 agent 接入外部工具/数据源的开放协议。让 agent 通过 stdio 连接任意 MCP server,
自动发现其工具 → 转成 OpenAI function schema → 带命名空间注入主循环。这一步把项目从
"我写了一个 agent"升级为"我搭了个能接入工具生态的 agent host"。

协议: JSON-RPC 2.0, 按行分隔 (newline-delimited), 走子进程 stdio。
本实现覆盖握手 (initialize + notifications/initialized) + tools/list + tools/call,
足以接入真实的 MCP server (如文件系统 / 数据库 / 自建知识库 server)。
"""

import json
import subprocess

from .tools import Tool, ToolRegistry

PROTOCOL_VERSION = "2024-11-05"


class MCPError(RuntimeError):
    pass


class MCPClient:
    """连接单个 MCP server (以子进程 stdio 通信) 的最小客户端。"""

    def __init__(self, command: str, args=None, name: str = "mcp", env=None):
        self.name = name
        self._argv = [command, *(args or [])]
        self._env = env
        self._id = 0
        self._proc = None

    # ── 生命周期 ──
    def start(self) -> "MCPClient":
        self._proc = subprocess.Popen(
            self._argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            bufsize=1, env=self._env,
        )
        self._handshake()
        return self

    def close(self) -> None:
        if not self._proc:
            return
        for step in (lambda: self._proc.stdin.close(),
                     lambda: self._proc.terminate(),
                     lambda: self._proc.wait(timeout=5)):
            try:
                step()
            except Exception:
                pass
        self._proc = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()

    # ── JSON-RPC ──
    def _send(self, obj: dict) -> None:
        self._proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()

    def _request(self, method: str, params=None) -> dict:
        self._id += 1
        rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        while True:                              # 读到匹配 id 的响应, 跳过通知/日志
            line = self._proc.stdout.readline()
            if not line:
                raise MCPError(f"MCP server 在等待 '{method}' 响应时关闭了")
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue                         # 忽略非 JSON 杂讯
            if msg.get("id") == rid:
                if "error" in msg:
                    raise MCPError(f"'{method}' 返回错误: {msg['error']}")
                return msg.get("result", {})

    def _notify(self, method: str, params=None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _handshake(self) -> None:
        self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "miniagent", "version": "0.1.0"},
        })
        self._notify("notifications/initialized")

    # ── 能力 ──
    def list_tools(self) -> list[dict]:
        return self._request("tools/list").get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> str:
        res = self._request("tools/call", {"name": name, "arguments": arguments or {}})
        parts = []
        for c in res.get("content", []):
            parts.append(c.get("text", "") if c.get("type") == "text"
                         else json.dumps(c, ensure_ascii=False))
        text = "\n".join(parts) if parts else json.dumps(res, ensure_ascii=False)
        return f"ERROR: {text}" if res.get("isError") else text


def register_mcp_tools(registry: ToolRegistry, client: MCPClient,
                       namespace: str = None) -> list[str]:
    """把一个 MCP server 暴露的工具全部注册进本地 registry, 带命名空间防撞名。

    工具名形如  mcp__{namespace}__{remote}, 与内建工具隔离;
    执行即远程 tools/call (workspace 参数被忽略, MCP 工具不碰本地沙箱)。返回新增的工具名。
    """
    ns = namespace or client.name
    added = []
    for t in client.list_tools():
        remote = t["name"]
        local = f"mcp__{ns}__{remote}"
        schema = t.get("inputSchema") or {"type": "object", "properties": {}}
        desc = (t.get("description") or f"MCP 工具 {remote}") + f" (来自 MCP server: {ns})"

        def _make(remote_name):
            def _call(ws, **args):
                return client.call_tool(remote_name, args)
            return _call

        registry.register(Tool(local, desc, schema, _make(remote)))
        added.append(local)
    return added
