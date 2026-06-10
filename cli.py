"""命令行入口。

用法:
    python cli.py "创建 hello.py 打印 Hello, Agent 并运行验证"
    python cli.py "修复 bug" --workspace ./workspace --max-steps 15 --trace run.json
"""

import argparse
import shlex
import sys

# Windows 控制台默认 GBK, 含 emoji / 中文会崩 —— 强制 UTF-8 (上个项目踩过的坑)。
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from miniagent.agent import Agent
from miniagent.llm import OpenAICompatLLM
from miniagent.safety import Workspace
from miniagent.tools import default_registry
from miniagent.trace import Step


def main():
    ap = argparse.ArgumentParser(description="miniagent — 极简 coding agent")
    ap.add_argument("task", help="要完成的任务 (自然语言)")
    ap.add_argument("--workspace", default="./workspace", help="沙箱工作区目录")
    ap.add_argument("--max-steps", type=int, default=12)
    ap.add_argument("--model", default=None, help="覆盖 LLM_MODEL")
    ap.add_argument("--trace", default=None, help="把完整 trace 存为 JSON 的路径")
    ap.add_argument("--executor", choices=["local", "docker"], default="local",
                    help="命令执行后端: local=宿主机护栏版 (默认); docker=容器隔离 (需 docker)")
    ap.add_argument("--mcp", action="append", default=[], metavar="CMD",
                    help="连接一个 MCP server 并挂载其工具 (可重复)。"
                         '例: --mcp "python examples/mcp_demo_server.py"')
    args = ap.parse_args()

    try:
        llm = OpenAICompatLLM(model=args.model)
    except RuntimeError as e:
        print(f"⚠️  {e}")
        sys.exit(1)
    ws = Workspace(args.workspace)
    if args.executor == "docker":
        from miniagent.executor import DockerExecutor
        reg = default_registry(executor=DockerExecutor())
    else:
        reg = default_registry()

    # 连接 MCP server, 把它们的工具挂载进 registry (miniagent 作为 MCP host)。
    mcp_clients = []
    for i, spec in enumerate(args.mcp):
        from miniagent.mcp import MCPClient, register_mcp_tools
        argv = shlex.split(spec)
        client = MCPClient(argv[0], argv[1:], name=f"s{i}").start()
        added = register_mcp_tools(reg, client)
        mcp_clients.append(client)
        print(f"🔌 MCP[{client.name}] 已挂载工具: {', '.join(added) or '(无)'}")

    def on_event(step: Step):
        if step.kind == "llm":
            tcs = step.detail.get("tool_calls") or []
            print(f"  🤖 {'→ ' + ', '.join(tcs) if tcs else '→ 收尾'}  ({step.tokens}tok)")
        else:
            print(f"  🔧 {step.name}  {str(step.detail.get('args'))[:80]}")

    print(f"🎯 任务: {args.task}")
    print(f"📁 工作区: {ws.root}  |  🧠 模型: {llm.model}\n")

    agent = Agent(llm, ws, reg, max_steps=args.max_steps, on_event=on_event)
    try:
        result = agent.run(args.task)
    finally:
        for c in mcp_clients:
            c.close()

    print("\n" + "=" * 56)
    print(result.answer)
    print("=" * 56)
    print("📊 " + str(agent.trace.summary()))
    if args.trace:
        agent.trace.save(args.trace)
        print(f"📝 trace 已保存: {args.trace}")


if __name__ == "__main__":
    main()
