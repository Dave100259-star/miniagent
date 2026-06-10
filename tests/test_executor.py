"""Executor 抽象的测试 —— LocalExecutor 用真实子进程, 注入路径用假后端。

DockerExecutor 需本机 docker, 不在单测覆盖范围 (属集成测试)。
"""

from miniagent.executor import ExecResult, Executor, LocalExecutor
from miniagent.safety import Workspace
from miniagent.tools import default_registry


def test_local_executor_success(tmp_path):
    res = LocalExecutor().run('python -c "print(123)"', cwd=str(tmp_path))
    assert res.exit_code == 0
    assert "123" in res.output


def test_local_executor_nonzero_exit(tmp_path):
    res = LocalExecutor().run('python -c "import sys; sys.exit(3)"', cwd=str(tmp_path))
    assert res.exit_code == 3


class _FakeExecutor(Executor):
    def run(self, command, cwd, timeout=20):
        return ExecResult(0, f"FAKE:{command}")


def test_registry_uses_injected_executor(tmp_path):
    # 证明 run_command 的执行后端是可替换的 (Docker 可由此注入)。
    reg = default_registry(executor=_FakeExecutor())
    out = reg.call("run_command", {"command": "echo hi"}, Workspace(tmp_path))
    assert out.startswith("exit=0")
    assert "FAKE:echo hi" in out


def test_default_registry_still_runs_locally(tmp_path):
    # 不传 executor → 默认 LocalExecutor, 行为与重构前一致。
    out = default_registry().call("run_command", {"command": 'python -c "print(7)"'},
                                  Workspace(tmp_path))
    assert out.startswith("exit=0")
    assert "7" in out