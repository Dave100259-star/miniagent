"""命令执行后端 (Executor) —— 把"在哪里、以多强的隔离执行命令"抽象成可替换接口。

为什么要这层抽象:
- `run_command` 的真正风险是**隔离**, 而非命令文本。把执行后端做成接口, 就能在
  "零依赖的本地护栏版"和"OS 级隔离的容器版"之间切换, 而 agent 主循环完全不变。
- 这把 README 安全章节从"我知道正确答案是容器隔离"升级为"正确答案我实现了,
  残余风险与缓解是 X" —— 自己挖的坑自己填, 比从没挖过更有说服力。

LocalExecutor : 直接在宿主机 subprocess 跑, 配合 tools.py 的危险命令黑名单。
                这是 defense-in-depth, **不是真隔离**, 仅供演示/测试 (零依赖)。
DockerExecutor: 一次性容器内执行 —— 禁网 + 只读根 + 仅挂工作区可写 + 资源/超时限额,
                才是约束"会跑 shell 的 agent"的正确形态 (需本机有 docker)。
"""

import subprocess
from dataclasses import dataclass


@dataclass
class ExecResult:
    exit_code: int
    output: str          # stdout 与 stderr 合并后的文本


def _combine(stdout: str, stderr: str) -> str:
    out = (stdout or "").strip()
    err = (stderr or "").strip()
    return out + (f"\n[stderr]\n{err}" if err else "")


class Executor:
    """执行一条 shell 命令, 返回 (退出码, 合并输出)。超时应抛 subprocess.TimeoutExpired。"""

    def run(self, command: str, cwd: str, timeout: int = 20) -> ExecResult:
        raise NotImplementedError

    @property
    def label(self) -> str:
        return type(self).__name__


class LocalExecutor(Executor):
    """宿主机直接执行。无隔离, 仅靠上层黑名单护栏 (defense-in-depth)。"""

    def run(self, command: str, cwd: str, timeout: int = 20) -> ExecResult:
        r = subprocess.run(
            command, shell=True, cwd=cwd, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
        return ExecResult(r.returncode, _combine(r.stdout, r.stderr))


class DockerExecutor(Executor):
    """在一次性容器里执行, 提供真正的 OS 级隔离。

    安全标志 (每一条都对应一类风险):
      --network none           禁止联网      → 堵外联 / 数据外泄 / 在线装包
      --read-only              根文件系统只读 → 防篡改系统文件
      --tmpfs /tmp             /tmp 可写内存盘 → 只读根下程序仍能用临时文件
      -v {cwd}:/work           仅工作区可写挂载, 容器内 cwd
      --memory/--cpus/--pids   资源上限      → 防 fork bomb / 撑爆内存
      --rm                     退出即销毁     → 无状态残留

    残余风险 (诚实): 仍受限于宿主 docker 守护进程的攻击面与镜像本身的可信度;
    未做 user namespace remap / seccomp 自定义 profile, 生产环境应再加固。
    """

    def __init__(self, image: str = "python:3.12-slim", memory: str = "256m",
                 cpus: str = "1.0", pids_limit: int = 128):
        self.image = image
        self.memory = memory
        self.cpus = cpus
        self.pids_limit = pids_limit

    def run(self, command: str, cwd: str, timeout: int = 20) -> ExecResult:
        docker_cmd = [
            "docker", "run", "--rm",
            "--network", "none",
            "--read-only",
            "--tmpfs", "/tmp",
            "--memory", self.memory,
            "--cpus", self.cpus,
            "--pids-limit", str(self.pids_limit),
            "-v", f"{cwd}:/work",
            "-w", "/work",
            self.image,
            "sh", "-c", command,
        ]
        # 容器启动有固定开销, 给 docker 进程本身比命令多留 10s 余量。
        r = subprocess.run(
            docker_cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout + 10,
        )
        return ExecResult(r.returncode, _combine(r.stdout, r.stderr))
