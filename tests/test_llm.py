"""LLM 抽象层测试 —— 无需真实 API key。

覆盖:
- 成本估算 estimate_cost 对已知/未知模型的行为
- OpenAICompatLLM 在 key 缺失或仍是占位符时, 构造期就给出人话错误
  (否则占位符会漏到 HTTP 层, 以晦涩的 UnicodeEncodeError 崩溃)
"""

import pytest

from miniagent.llm import OpenAICompatLLM, estimate_cost


def test_estimate_cost_known_model():
    # deepseek: (0.27, 1.10) USD / 1M tokens
    cost = estimate_cost("deepseek-chat", 1_000_000, 1_000_000)
    assert cost == pytest.approx(0.27 + 1.10)


def test_estimate_cost_unknown_model_is_zero():
    assert estimate_cost("some-unknown-model", 1000, 1000) == 0.0


@pytest.mark.parametrize("bad_key", [
    "",                              # 缺失
    "sk-your-key-here",              # 英文占位符 (.env.example)
    "sk-在这里粘贴你的密钥",            # 中文占位符 (.env), 非 ASCII
])
def test_placeholder_or_missing_key_raises_clear_error(bad_key):
    with pytest.raises(RuntimeError, match="占位符|缺失"):
        OpenAICompatLLM(api_key=bad_key)
