"""评测套件: 让 agent 在一组真实编程任务上跑, 用程序化检查器判定成败。

亮点不在"能跑", 而在**可测量、可复现、可做消融**:
  --repeat N  : 每题跑 N 次, 报 pass@1 均值 + bootstrap 95% 置信区间 (LLM 非确定, 单点数字会骗人)
  --ablation  : 同一套题在"开/关 自我修正"两种配置下各跑一遍, 量化"观察失败→重试修复"的价值
  --json PATH : 结构化结果落盘 (含 CI / 单位成功成本), 便于跨模型聚合 (见 aggregate.py)

每题在独立的临时工作区里运行, 互不污染。

用法 (需真实 key):
    python eval/run_eval.py --repeat 5
    python eval/run_eval.py --ablation --repeat 5 --json eval/results.json
    python eval/run_eval.py --ablation --repeat 5 --model qwen-plus --json eval/r_qwen.json
    # 跨模型矩阵: 多跑几个 --model 再 python eval/aggregate.py r_*.json
"""

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
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
from miniagent.stats import bootstrap_ci, cost_per_success
from miniagent.tools import default_registry

TASKS_FILE = Path(__file__).parent / "tasks.json"


def _usable_key():
    """返回可用的真实 key, 否则 None。能识别 .env / .env.example 里的占位符
    (含中文 → 非 ascii; 或英文模板 'your-key'), 避免拿假 key 去打 API 触发 401。"""
    k = (os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or "").strip()
    return k if (k and k.isascii() and "your-key" not in k) else None


def run_checks(checks, ws, answer):
    for c in checks:
        typ = c["type"]
        if typ == "file_exists":
            if not (ws.root / c["path"]).exists():
                return False, f"缺少文件 {c['path']}"
        elif typ == "file_contains":
            p = ws.root / c["path"]
            if not p.exists() or c["text"] not in p.read_text(encoding="utf-8", errors="replace"):
                return False, f"{c['path']} 不含期望文本 {c['text']!r}"
        elif typ == "file_not_contains":
            p = ws.root / c["path"]
            if p.exists() and c["text"] in p.read_text(encoding="utf-8", errors="replace"):
                return False, f"{c['path']} 仍含不该出现的文本 {c['text']!r}"
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


def run_one(llm_factory, task, max_steps, recover_errors, trace_path=None):
    """跑一题一次, 返回 (ok, reason, trace_summary)。每次用独立临时工作区, 跑完即清理。"""
    tmp = Path(tempfile.mkdtemp(prefix="eval_"))
    try:
        ws = Workspace(tmp)
        for fname, content in (task.get("setup_files") or {}).items():
            (ws.root / fname).write_text(content, encoding="utf-8")
        agent = Agent(llm_factory(), ws, default_registry(),
                      max_steps=max_steps, recover_errors=recover_errors)
        result = agent.run(task["task"])
        ok, reason = run_checks(task["checks"], ws, result.answer)
        if trace_path:
            agent.trace.save(trace_path)
        return ok, reason, agent.trace.summary()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_suite(llm_factory, tasks, max_steps, recover_errors, repeat, save_traces, label=""):
    print(f"\n🧪 {label}{len(tasks)} 题 × {repeat} 次  (recover_errors={recover_errors})")
    print("=" * 66)
    per_task, outcomes, tot_tok, tot_cost = [], [], 0, 0.0
    for task in tasks:
        passes, reason_last, toks = 0, "", []
        for r in range(repeat):
            tp = (Path(__file__).parent / "traces" / f"{task['id']}_r{r}_{int(recover_errors)}.json"
                  if save_traces else None)
            ok, reason, s = run_one(llm_factory, task, max_steps, recover_errors, tp)
            outcomes.append(bool(ok))            # 逐次结果, 供 bootstrap 置信区间用
            passes += int(ok)
            if not ok:
                reason_last = reason
            toks.append(s["tokens"])
            tot_tok += s["tokens"]
            tot_cost += s["cost_usd"]
        any_pass = passes > 0
        per_task.append({"id": task["id"], "passes": passes, "repeat": repeat,
                         "rate": passes / repeat, "any": any_pass,
                         "avg_tokens": round(statistics.mean(toks), 1),
                         "reason": "" if any_pass else reason_last})
        mark = "✅" if any_pass else "❌"
        tail = "" if any_pass else f"  ← {reason_last}"
        print(f"{mark} {task['id']:<16} {passes}/{repeat} ({passes / repeat:>4.0%})  "
              f"~{round(statistics.mean(toks))}tok{tail}")
    n = len(outcomes)
    successes = sum(outcomes)
    pass_at_k = sum(t["any"] for t in per_task) / len(per_task)
    mean_p1, lo, hi = bootstrap_ci(outcomes)             # pass@1 均值 ± 95% CI
    cps = cost_per_success(tot_cost, successes)
    print("-" * 66)
    print(f"📊 pass@1 = {mean_p1:.0%}  95%CI [{lo:.0%}, {hi:.0%}]  (n={n})   "
          f"pass@{repeat} = {pass_at_k:.0%}")
    cps_s = f"${cps:.5f}" if cps is not None else "—"
    print(f"   总 {tot_tok} tok   ${round(tot_cost, 5)}   单位成功成本 {cps_s}/次")
    return {"recover_errors": recover_errors, "repeat": repeat, "pass_at_k": pass_at_k,
            "avg_pass1": mean_p1, "ci_lo": lo, "ci_hi": hi, "n_runs": n,
            "successes": successes, "total_tokens": tot_tok,
            "total_cost": round(tot_cost, 6),
            "cost_per_success": (round(cps, 6) if cps is not None else None),
            "tasks": per_task}


def main():
    ap = argparse.ArgumentParser(description="miniagent 评测: pass@k / 消融实验")
    ap.add_argument("--model", default=None)
    ap.add_argument("--base-url", default=None, help="覆盖 LLM_BASE_URL (跨 provider 出矩阵用)")
    ap.add_argument("--api-key", default=None, help="覆盖 LLM_API_KEY (跨 provider 出矩阵用)")
    ap.add_argument("--max-steps", type=int, default=15)
    ap.add_argument("--repeat", type=int, default=1, help="每题重复次数 (pass@k / 方差)")
    ap.add_argument("--ablation", action="store_true", help="对比 开/关 自我修正 的通过率")
    ap.add_argument("--save-traces", action="store_true", help="每次运行的 trace 存到 eval/traces/")
    ap.add_argument("--json", dest="json_out", default=None, help="结构化结果输出路径")
    args = ap.parse_args()

    cli_key = (args.api_key or "").strip()
    cli_key_ok = bool(cli_key and cli_key.isascii() and "your-key" not in cli_key)
    if not (cli_key_ok or _usable_key()):
        print("⚠️  评测需要真实 LLM key (检测到缺失或仍是占位符)。请在 .env 填入真实 LLM_API_KEY，")
        print("   或用 --api-key/--base-url 直接传入。无 key 想验证核心逻辑请跑: python -m pytest -q")
        sys.exit(1)

    from miniagent.llm import OpenAICompatLLM

    def llm_factory():
        return OpenAICompatLLM(model=args.model, base_url=args.base_url, api_key=args.api_key)

    tasks = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    t0 = time.time()
    report = {"model": args.model or os.getenv("LLM_MODEL") or os.getenv("DEEPSEEK_MODEL"),
              "runs": []}

    if args.ablation:
        on = run_suite(llm_factory, tasks, args.max_steps, True, args.repeat,
                       args.save_traces, "[自我修正 ON]  ")
        off = run_suite(llm_factory, tasks, args.max_steps, False, args.repeat,
                        args.save_traces, "[自我修正 OFF] ")
        report["runs"] = [on, off]
        def _cps(run):
            c = run.get("cost_per_success")
            return f"${c:.5f}" if c is not None else "—"

        print("\n🔬 消融对比: 自我修正 (观察失败→重试修复) 的价值")
        print("=" * 66)
        print(f"  pass@1 (均值):    ON {on['avg_pass1']:>5.0%}   OFF {off['avg_pass1']:>5.0%}"
              f"   Δ {on['avg_pass1'] - off['avg_pass1']:+.0%}")
        print(f"  pass@1 95%CI:     ON [{on['ci_lo']:.0%},{on['ci_hi']:.0%}]   "
              f"OFF [{off['ci_lo']:.0%},{off['ci_hi']:.0%}]   (n={on['n_runs']}/配置)")
        print(f"  pass@{args.repeat}:           ON {on['pass_at_k']:>5.0%}   OFF {off['pass_at_k']:>5.0%}"
              f"   Δ {on['pass_at_k'] - off['pass_at_k']:+.0%}")
        print(f"  总成本:           ON ${on['total_cost']:<7.5f} OFF ${off['total_cost']:<7.5f}"
              f"  ← ON 更贵")
        print(f"  单位成功成本:     ON {_cps(on)}   OFF {_cps(off)}"
              f"   ← 但按'每次成功'摊, 自我修正未必更贵 (OFF 的省钱靠提前放弃换)")
    else:
        report["runs"] = [run_suite(llm_factory, tasks, args.max_steps, True,
                                    args.repeat, args.save_traces)]

    print(f"\n⏱  总用时 {time.time() - t0:.1f}s")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
        print(f"📝 结果已保存: {args.json_out}")


if __name__ == "__main__":
    main()
