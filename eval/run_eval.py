"""评测套件: 让 agent 在一组真实编程任务上跑, 用程序化检查器判定成败。

这是把"我做了个 agent"升级成"我测量并改进了 agent"的关键。
每个任务在独立的临时工作区里运行, 互不污染。

用法 (需真实 key):
    python eval/run_eval.py
    python eval/run_eval.py --model qwen-plus --max-steps 15
"""

import sys
import os
import json
import time
import shutil
import tempfile
import argparse
import subprocess
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from miniagent.agent import Agent
from miniagent.safety import Workspace
from miniagent.tools import default_registry

TASKS_FILE = Path(__file__).parent / "tasks.json"


def run_checks(checks: list, ws: Workspace, answer: str) -> tuple[bool, str]:
    for c in checks:
        typ = c["type"]
        if typ == "file_exists":
            if not (ws.root / c["path"]).exists():
                return False, f"缺少文件 {c['path']}"
        elif typ == "file_contains":
            p = ws.root / c["path"]
            if not p.exists() or c["text"] not in p.read_text(encoding="utf-8", errors="replace"):
                return False, f"{c['path']} 不含期望文本"
        elif typ == "command_ok":
            try:
                r = subprocess.run(c["command"], shell=True, cwd=str(ws.root),
                                   capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=c.get("timeout", 30))
            except subprocess.TimeoutExpired:
                return False, f"命令超时: {c['command']}"
            if r.returncode != 0:
                return False, f"命令失败 ({c['command']}): {(r.stderr or '')[:150]}"
            if "stdout_contains" in c and c["stdout_contains"] not in (r.stdout or ""):
                return False, f"输出不含 '{c['stdout_contains']}'"
        elif typ == "answer_contains":
            if c["text"] not in answer:
                return False, "最终回答不含期望文本"
    return True, "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None)
    ap.add_argument("--max-steps", type=int, default=15)
    ap.add_argument("--save-traces", action="store_true", help="把每题 trace 存到 eval/traces/")
    args = ap.parse_args()

    if not (os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY")):
        print("⚠️  评测需要真实 LLM key。请设 LLM_API_KEY (或 DEEPSEEK_API_KEY) / 配 .env。")
        print("   想验证 agent 核心逻辑、无需 key, 请跑:  python -m pytest -q")
        sys.exit(1)

    from miniagent.llm import OpenAICompatLLM
    tasks = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    print(f"🧪 评测 {len(tasks)} 个任务\n" + "=" * 60)

    rows, passed = [], 0
    t_start = time.time()
    for task in tasks:
        tmp = Path(tempfile.mkdtemp(prefix="eval_"))
        try:
            ws = Workspace(tmp)
            for fname, content in (task.get("setup_files") or {}).items():
                (ws.root / fname).write_text(content, encoding="utf-8")

            llm = OpenAICompatLLM(model=args.model)
            agent = Agent(llm, ws, default_registry(), max_steps=args.max_steps, model=llm.model)
            result = agent.run(task["task"])
            ok, reason = run_checks(task["checks"], ws, result.answer)

            if args.save_traces:
                agent.trace.save(Path(__file__).parent / "traces" / f"{task['id']}.json")

            passed += ok
            s = agent.trace.summary()
            rows.append((task["id"], ok, reason, s["llm_calls"], s["tokens"], s["cost_usd"]))
            mark = "✅ PASS" if ok else "❌ FAIL"
            print(f"{mark}  {task['id']:<10}  steps={s['llm_calls']:<2} tok={s['tokens']:<5} "
                  f"${s['cost_usd']:<8} {'' if ok else '← ' + reason}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    total_tok = sum(r[4] for r in rows)
    total_cost = round(sum(r[5] for r in rows), 6)
    print("=" * 60)
    print(f"📊 通过率 {passed}/{len(tasks)} = {passed / len(tasks):.0%}  |  "
          f"总 token {total_tok}  |  总成本 ${total_cost}  |  "
          f"用时 {time.time() - t_start:.1f}s")


if __name__ == "__main__":
    main()
