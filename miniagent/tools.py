"""工具集: agent 能调用的"手脚"。

每个工具 = 名字 + 描述 + JSON Schema 参数 + 一个 (workspace, **args)->str 的函数。
所有文件/命令操作都经过 Workspace 沙箱。
"""

import subprocess
from dataclasses import dataclass
from typing import Callable

from .safety import Workspace


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict        # JSON Schema
    func: Callable          # (workspace, **args) -> str


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self) -> list[dict]:
        """转成 OpenAI function-calling 的 tools 格式。"""
        return [
            {"type": "function",
             "function": {"name": t.name, "description": t.description,
                          "parameters": t.parameters}}
            for t in self._tools.values()
        ]

    def call(self, name: str, args: dict, workspace: Workspace) -> str:
        if name not in self._tools:
            return f"ERROR: 未知工具 {name}"
        return self._tools[name].func(workspace, **(args or {}))


# ── 默认工具实现 ──

def _read_file(ws: Workspace, path: str) -> str:
    p = ws.resolve(path)
    if not p.exists():
        return f"ERROR: 文件不存在: {path}"
    if p.is_dir():
        return f"ERROR: 这是目录不是文件: {path}"
    return p.read_text(encoding="utf-8", errors="replace")


def _write_file(ws: Workspace, path: str, content: str) -> str:
    p = ws.resolve(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"已写入 {len(content)} 字符到 {path}"


def _edit_file(ws: Workspace, path: str, old: str, new: str) -> str:
    p = ws.resolve(path)
    if not p.exists():
        return f"ERROR: 文件不存在: {path}"
    text = p.read_text(encoding="utf-8", errors="replace")
    n = text.count(old)
    if n == 0:
        return "ERROR: 未找到要替换的内容 (old 不在文件中)"
    if n > 1:
        return f"ERROR: old 在文件中出现 {n} 次, 不唯一; 请提供更长、更具体的上下文"
    p.write_text(text.replace(old, new), encoding="utf-8")
    return f"已替换 {path} 中 1 处"


def _list_dir(ws: Workspace, path: str = ".") -> str:
    p = ws.resolve(path)
    if not p.exists():
        return f"ERROR: 路径不存在: {path}"
    items = sorted(x.name + ("/" if x.is_dir() else "") for x in p.iterdir())
    return "\n".join(items) if items else "(空目录)"


def _run_command(ws: Workspace, command: str, timeout: int = 20) -> str:
    try:
        r = subprocess.run(
            command, shell=True, cwd=str(ws.root),
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: 命令超时 (>{timeout}s): {command}"
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    body = out + (f"\n[stderr]\n{err}" if err else "")
    return f"exit={r.returncode}\n{body[:4000]}"


def default_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(Tool(
        "read_file", "读取工作区内某个文件的全部内容。",
        {"type": "object", "properties": {"path": {"type": "string", "description": "相对工作区的路径"}},
         "required": ["path"]},
        _read_file,
    ))
    reg.register(Tool(
        "write_file", "把内容写入文件 (覆盖)。父目录会自动创建。",
        {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}},
         "required": ["path", "content"]},
        _write_file,
    ))
    reg.register(Tool(
        "edit_file", "对文件做一处精确替换: 把 old 文本替换为 new。old 必须在文件中唯一出现。",
        {"type": "object", "properties": {
            "path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}},
         "required": ["path", "old", "new"]},
        _edit_file,
    ))
    reg.register(Tool(
        "list_dir", "列出某个目录下的文件与子目录。",
        {"type": "object", "properties": {"path": {"type": "string", "description": "默认当前目录 ."}}},
        _list_dir,
    ))
    reg.register(Tool(
        "run_command", "在工作区内执行一条 shell 命令并返回退出码与输出 (有超时)。",
        {"type": "object", "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "integer", "description": "秒, 默认 20"}},
         "required": ["command"]},
        _run_command,
    ))
    return reg
