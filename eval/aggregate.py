"""把多个 --json 结果文件聚合成"模型 × 配置"矩阵。

跨模型消融的用法 (各几块钱):
    python eval/run_eval.py --ablation --repeat 5 --model deepseek-chat --json eval/r_deepseek.json
    python eval/run_eval.py --ablation --repeat 5 --model qwen-plus     --json eval/r_qwen.json
    python eval/run_eval.py --ablation --repeat 5 --model glm-4-flash   --json eval/r_glm.json
    python eval/aggregate.py eval/r_deepseek.json eval/r_qwen.json eval/r_glm.json

看点: Δ(自我修正带来的提升) 是否随模型变强而缩小 —— 强模型靠静态推理直接改对、
不那么依赖"观察失败→重试"。无论结论朝哪边, 都是一个"机制 × 能力"的交互发现。
"""

import json
import sys
from pathlib import Path


def _run(report, recover):
    for r in report.get("runs", []):
        if r.get("recover_errors") is recover:
            return r
    return None


def main(paths):
    rows = []
    for p in paths:
        rep = json.loads(Path(p).read_text(encoding="utf-8"))
        on, off = _run(rep, True), _run(rep, False)
        if not on:
            continue
        rows.append((rep.get("model", "?"), on, off))

    print(f"\n{'模型':<16} {'自我修正ON':>16} {'自我修正OFF':>16} {'Δ pass@1':>9} {'单位成功成本(ON)':>16}")
    print("-" * 80)
    for model, on, off in rows:
        on_s = f"{on['avg_pass1']:.0%}[{on['ci_lo']:.0%},{on['ci_hi']:.0%}]"
        if off:
            off_s = f"{off['avg_pass1']:.0%}[{off['ci_lo']:.0%},{off['ci_hi']:.0%}]"
            delta = f"{on['avg_pass1'] - off['avg_pass1']:+.0%}"
        else:
            off_s, delta = "—", "—"
        cps = on.get("cost_per_success")
        cps_s = f"${cps:.5f}" if cps is not None else "—"
        print(f"{model:<16} {on_s:>16} {off_s:>16} {delta:>9} {cps_s:>16}")
    print("\n提示: 关注最右的 Δ 列是否随模型能力变化 —— 这就是'机制 × 能力'交互。")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python eval/aggregate.py <结果1.json> [结果2.json ...]")
        sys.exit(1)
    main(sys.argv[1:])
