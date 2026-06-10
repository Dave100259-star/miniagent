"""评测统计: bootstrap 置信区间 + 单位成功成本。

为什么需要它:
- LLM 非确定, 单点通过率会骗人。重复 n 次 + bootstrap 置信区间, 才能诚实地
  报告"通过率 = 均值 ± CI", 让读者看到不确定性而非一个虚假精确的数字。
- 单位成功成本 (cost / 成功次数) 把"自我修正更贵"这种表面结论翻转过来:
  贵是因为它不轻易放弃, 但按"每次成功"摊, 单价反而可能更低。

纯函数、零依赖, 因此可以脱离真实 API 被单元测试覆盖。
"""

import random
from statistics import mean


def bootstrap_ci(outcomes, confidence: float = 0.95,
                 n_resamples: int = 2000, seed: int = 0):
    """对一组 0/1 (失败/成功) 结果做 bootstrap 重采样, 返回 (均值, 下界, 上界)。

    outcomes: 每次运行的成功与否 (布尔或 0/1), 共 n 次 (跨所有任务 × 重复)。
    返回比例形式的均值与 confidence 置信区间。全 0 或全 1 时 CI 退化为点。
    """
    xs = [1.0 if o else 0.0 for o in outcomes]
    if not xs:
        return 0.0, 0.0, 0.0
    m = mean(xs)
    if len(set(xs)) == 1:                      # 全部相同 → 区间退化为该点
        return m, m, m
    rng = random.Random(seed)
    n = len(xs)
    boot = []
    for _ in range(n_resamples):
        boot.append(mean(xs[rng.randrange(n)] for _ in range(n)))
    boot.sort()
    alpha = (1 - confidence) / 2
    lo = boot[int(alpha * n_resamples)]
    hi = boot[min(n_resamples - 1, int((1 - alpha) * n_resamples))]
    return m, lo, hi


def cost_per_success(total_cost: float, successes: int):
    """单位成功成本 = 总成本 / 成功次数。无成功时返回 None (避免除零)。"""
    return (total_cost / successes) if successes else None
