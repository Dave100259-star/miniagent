"""一个最小的可运行 MCP server (stdio, JSON-RPC) —— 演示 miniagent 作为 MCP host 接入外部工具。

暴露两个工具: add(a,b) 与 reverse(text)。
连接方式:
    python cli.py "用 mcp 工具算 12 加 30, 再把结果倒过来" --mcp "python examples/mcp_demo_server.py"

这是个教学用的玩具 server; 换成真实 server (文件系统 / 数据库 / 知识库) 时, miniagent 侧零改动。
"""

import json
import sys

TOOLS = [
    {"name": "add", "description": "返回两个数之和",
     "inputSchema": {"type": "object",
                     "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                     "required": ["a", "b"]}},
    {"name": "reverse", "description": "把字符串反转",
     "inputSchema": {"type": "object",
                     "properties": {"text": {"type": "string"}},
                     "required": ["text"]}},
]


def _send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _result(rid, result):
    _send({"jsonrpc": "2.0", "id": rid, "result": result})


def _call(name, args):
    if name == "add":
        return str(args["a"] + args["b"])
    if name == "reverse":
        return str(args["text"])[::-1]
    raise KeyError(name)


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params", {})

        if method == "initialize":
            _result(mid, {"protocolVersion": "2024-11-05",
                          "capabilities": {"tools": {}},
                          "serverInfo": {"name": "demo", "version": "0.1.0"}})
        elif method == "notifications/initialized":
            pass                                   # 通知, 无需响应
        elif method == "tools/list":
            _result(mid, {"tools": TOOLS})
        elif method == "tools/call":
            try:
                text = _call(params.get("name"), params.get("arguments", {}))
                _result(mid, {"content": [{"type": "text", "text": text}]})
            except Exception as e:
                _result(mid, {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}],
                              "isError": True})
        elif mid is not None:
            _send({"jsonrpc": "2.0", "id": mid,
                   "error": {"code": -32601, "message": f"method not found: {method}"}})


if __name__ == "__main__":
    main()
