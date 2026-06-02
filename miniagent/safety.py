"""沙箱: 把 agent 的一切文件操作限制在一个工作区目录内。

为什么重要: 一个会读写文件、跑 shell 命令的 agent, 如果不做路径约束, 模型
一旦失控就能读到 ../../ 之外的任意文件。把"安全边界"显式化, 是工程判断力的体现。
"""

from pathlib import Path


class Workspace:
    """限制在 root 目录内的工作区。所有路径都相对 root 解析, 越界即报错。"""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, path: str) -> Path:
        """把相对路径解析为绝对路径, 并确保它仍在 root 之内。"""
        target = (self.root / path).resolve()
        try:
            target.relative_to(self.root)
        except ValueError:
            raise ValueError(f"路径越界, 被沙箱拦截: {path}")
        return target

    def __repr__(self) -> str:
        return f"Workspace({self.root})"
