"""上下文压缩逻辑的单元测试 —— 同样无需 key。"""

from miniagent.agent import Agent
from miniagent.llm import ScriptedLLM
from miniagent.safety import Workspace
from miniagent.tools import default_registry


def _agent(tmp_path, **kw):
    return Agent(ScriptedLLM(script=[]), Workspace(tmp_path), default_registry(),
                 model="deepseek-chat", **kw)


def _msgs(n_tool):
    m = [{"role": "system", "content": "sys"}, {"role": "user", "content": "task"}]
    for i in range(n_tool):
        m.append({"role": "tool", "tool_call_id": f"c{i}", "content": "X" * 500})
    return m


def test_compact_preserves_head_and_recent(tmp_path):
    a = _agent(tmp_path, compact_after=6, compact_keep_recent=2)
    msgs = _msgs(10)
    out = a._compact(msgs)
    # 不丢消息 (配对安全)
    assert len(out) == len(msgs)
    # 头部完整保留
    assert out[0]["content"] == "sys" and out[1]["content"] == "task"
    # 最近 2 条完整保留
    assert out[-1]["content"] == "X" * 500 and out[-2]["content"] == "X" * 500
    # 中间的陈旧工具输出被压缩
    assert any("已压缩" in m["content"] for m in out[2:-2])


def test_compact_is_noop_when_short(tmp_path):
    a = _agent(tmp_path, compact_after=50)
    msgs = _msgs(3)
    assert a._compact(msgs) == msgs


def test_compact_triggers_on_token_budget(tmp_path):
    # 消息条数没超 (compact_after 极大), 但内容远超 token 预算 → 仍应触发压缩。
    a = _agent(tmp_path, compact_after=999, compact_keep_recent=2, token_budget=100)
    msgs = _msgs(10)                                  # 内容约 5000 字符, 远超 100 token 预算
    out = a._compact(msgs)
    assert len(out) == len(msgs)                      # 仍不丢消息 (配对安全)
    assert any("已压缩" in m["content"] for m in out[2:-2])
