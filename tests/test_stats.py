"""评测统计的单元测试 —— 纯函数, 无需 API key。"""

from miniagent.stats import bootstrap_ci, cost_per_success


def test_all_pass_ci_is_point_at_one():
    m, lo, hi = bootstrap_ci([True] * 20)
    assert m == 1.0 and lo == 1.0 and hi == 1.0


def test_all_fail_ci_is_point_at_zero():
    m, lo, hi = bootstrap_ci([False] * 20)
    assert m == 0.0 and lo == 0.0 and hi == 0.0


def test_empty_outcomes():
    assert bootstrap_ci([]) == (0.0, 0.0, 0.0)


def test_mixed_mean_and_interval_bounds():
    outcomes = [True, False] * 25            # 50% 成功, n=50
    m, lo, hi = bootstrap_ci(outcomes)
    assert abs(m - 0.5) < 1e-9
    assert 0.0 <= lo <= m <= hi <= 1.0       # CI 包住均值且落在 [0,1]
    assert lo < hi                           # 有混合 → 区间非退化


def test_deterministic_with_seed():
    outcomes = [True, True, False, True, False, False, True]
    assert bootstrap_ci(outcomes, seed=42) == bootstrap_ci(outcomes, seed=42)


def test_cost_per_success():
    assert cost_per_success(1.0, 4) == 0.25
    assert cost_per_success(0.10072, 0) is None    # 无成功 → None, 不除零
