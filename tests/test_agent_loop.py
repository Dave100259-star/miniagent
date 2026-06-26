"""用 ScriptedLLM 确定性地测 agent 主循环 —— 无需任何 API key。

这正是项目的卖点: agent 的核心逻辑 (循环、工具分发、错误回灌、终止条件)
全部可单元测试, 不依赖真实模型。
"""

from miniagent.agent import Agent, _looks_like_failure
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


def test_no_recovery_aborts_on_tool_error(tmp_path):
    # 消融对照: recover_errors=False 时, 第一个工具错误 (读不存在的文件) 就应终止失败,
    # 第二次 LLM 决策根本不会发生。与上面的 test_recovers_from_tool_error 正好对比。
    script = [
        llm_msg(tool_calls=[tool_call("read_file", {"path": "missing.txt"}, "c1")]),
        llm_msg(content="不应到达这里"),
    ]
    res = _agent(tmp_path, llm=ScriptedLLM(script=script), recover_errors=False).run("task")
    assert not res.success
    assert res.trace.tool_calls() == 1
    assert res.trace.llm_calls() == 1   # 第二次 LLM 不会被调用


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


def test_looks_like_failure_classification():
    # 硬异常 与 命令非零退出 都算失败; 正常输出与 exit=0 不算。
    assert _looks_like_failure("ERROR: 文件不存在")
    assert _looks_like_failure("exit=1\nTraceback ...")
    assert not _looks_like_failure("exit=0\nOK")
    assert not _looks_like_failure("已写入 12 字符到 a.txt")


def test_no_self_correct_aborts_on_failing_command(tmp_path):
    # 关键: run_command 跑挂 (非零退出) 现在也算失败。
    # 自我修正关闭时, 第一条失败命令就该终止 —— 第二次 LLM 决策不该发生。
    fail_cmd = 'python -c "import sys; sys.exit(3)"'
    script = [
        llm_msg(tool_calls=[tool_call("run_command", {"command": fail_cmd}, "c1")]),
        llm_msg(content="不应到达这里"),
    ]
    res = _agent(tmp_path, llm=ScriptedLLM(script=script), recover_errors=False).run("task")
    assert not res.success
    assert res.trace.llm_calls() == 1


def test_self_correct_continues_after_failing_command(tmp_path):
    # 同样的失败命令, 自我修正开启时应回灌错误并继续到收尾。
    fail_cmd = 'python -c "import sys; sys.exit(3)"'
    script = [
        llm_msg(tool_calls=[tool_call("run_command", {"command": fail_cmd}, "c1")]),
        llm_msg(content="看到命令失败, 已处理。"),
    ]
    res = _agent(tmp_path, llm=ScriptedLLM(script=script), recover_errors=True).run("task")
    assert res.success
    assert res.trace.llm_calls() == 2


def test_loop_guard_stops_repeated_identical_calls(tmp_path):
    # 模型反复发出完全相同的工具调用 → 无进展检测应在 max_steps 之前止损。
    def rule(messages):
        return llm_msg(tool_calls=[tool_call("list_dir", {"path": "."}, "c")])

    res = _agent(tmp_path, llm=ScriptedLLM(rule=rule), max_steps=20, loop_guard=3).run("loop")
    assert not res.success
    assert "无进展" in res.answer
    assert res.trace.llm_calls() < 20            # 远早于 max_steps


def test_on_event_callback_fires_for_llm_and_tool(tmp_path):
    # 实时事件回调 (CLI 据此打印进度) 应在 LLM 决策和工具执行两类步骤上都触发。
    script = [
        llm_msg(tool_calls=[tool_call("list_dir", {"path": "."}, "c1")]),
        llm_msg(content="done"),
    ]
    events = []
    _agent(tmp_path, llm=ScriptedLLM(script=script), on_event=events.append).run("task")
    kinds = [e.kind for e in events]
    assert "llm" in kinds and "tool" in kinds
