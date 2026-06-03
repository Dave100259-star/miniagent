"""命令行入口。

用法:
    python cli.py "创建 hello.py 打印 Hello, Agent 并运行验证"
    python cli.py "修复 bug" --workspace ./workspace --max-steps 15 --trace run.json
"""

import argparse
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
    args = ap.parse_args()

    try:
        llm = OpenAICompatLLM(model=args.model)
    except RuntimeError as e:
        print(f"⚠️  {e}")
        sys.exit(1)
    ws = Workspace(args.workspace)
    reg = default_registry()

    def on_event(step: Step):
        if step.kind == "llm":
            tcs = step.detail.get("tool_calls") or []
            print(f"  🤖 {'→ ' + ', '.join(tcs) if tcs else '→ 收尾'}  ({step.tokens}tok)")
        else:
            print(f"  🔧 {step.name}  {str(step.detail.get('args'))[:80]}")

    print(f"🎯 任务: {args.task}")
    print(f"📁 工作区: {ws.root}  |  🧠 模型: {llm.model}\n")

    agent = Agent(llm, ws, reg, max_steps=args.max_steps, on_event=on_event)
    result = agent.run(args.task)

    print("\n" + "=" * 56)
    print(result.answer)
    print("=" * 56)
    print("📊 " + str(agent.trace.summary()))
    if args.trace:
        agent.trace.save(args.trace)
        print(f"📝 trace 已保存: {args.trace}")


if __name__ == "__main__":
    main()
