"""用 ScriptedLLM 确定性地测 agent 主循环 —— 无需任何 API key。

这正是项目的卖点: agent 的核心逻辑 (循环、工具分发、错误回灌、终止条件)
全部可单元测试, 不依赖真实模型。
"""

from miniagent.agent import Agent
from miniagent.llm import ScriptedLLM, llm_msg, tool_call
from miniagent.safety import Workspace
from miniagent.tools import default_registry


def _agent(tmp_path, **kw):
    return Agent(kw.pop("llm"), Workspace(tmp_path), default_registry(),
                 model="deepseek-chat", **kw)


def test_writes_file_then_finishes(tmp_path):
    script = [
        llm_msg(tool_calls=[tool_call("write_file", {"path": "hi.txt", "content": "hello"}, "c1")]),
        llm_msg(content="完成, 已创建 hi.txt。"),
    ]
    res = _agent(tmp_path, llm=ScriptedLLM(script=script)).run("create hi.txt")
    assert res.success
    assert (tmp_path / "hi.txt").read_text(encoding="utf-8") == "hello"
    assert res.trace.tool_calls() == 1
    assert res.trace.llm_calls() == 2


def test_recovers_from_tool_error(tmp_path):
    # 先读一个不存在的文件 (工具返回 ERROR), 再写文件, 再收尾。
    script = [
        llm_msg(tool_calls=[tool_call("read_file", {"path": "missing.txt"}, "c1")]),
        llm_msg(tool_calls=[tool_call("write_file", {"path": "out.txt", "content": "ok"}, "c2")]),
        llm_msg(content="读取失败已绕过, 写好了 out.txt。"),
    ]
    res = _agent(tmp_path, llm=ScriptedLLM(script=script)).run("task")
    assert res.success
    assert (tmp_path / "out.txt").exists()
    assert res.trace.tool_calls() == 2


def test_hits_max_steps(tmp_path):
    # 永远只调 list_dir、从不收尾的模型 —— 必须被 max_steps 截断。
    def rule(messages):
        return llm_msg(tool_calls=[tool_call("list_dir", {"path": "."}, "c")])

    res = _agent(tmp_path, llm=ScriptedLLM(rule=rule), max_steps=3).run("loop")
    assert not res.success
    assert res.trace.llm_calls() == 3


def test_trace_accumulates_tokens(tmp_path):
    script = [
        llm_msg(tool_calls=[tool_call("list_dir", {"path": "."}, "c1")], pt=100, ct=20),
        llm_msg(content="done", pt=50, ct=10),
    ]
    res = _agent(tmp_path, llm=ScriptedLLM(script=script)).run("task")
    assert res.trace.total_tokens == 180
    assert res.trace.total_cost > 0
