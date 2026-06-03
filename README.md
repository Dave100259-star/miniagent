# miniagent

<!-- 推到 GitHub 后把 OWNER/REPO 换成你的仓库，badge 会自动变绿 -->
[![CI](https://github.com/OWNER/REPO/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/REPO/actions/workflows/ci.yml)
![tests](https://img.shields.io/badge/tests-21%20passed-brightgreen)
![python](https://img.shields.io/badge/python-3.10%2B-blue)

> 一个 ~600 行、带**评测**与**可观测性**的极简 coding agent。
> 不是又一个 "用 200 行复刻 Claude Code" 的教程仓库 —— 重点不在循环本身，而在**怎么测量和保证它的质量**。

一个 coding agent 的核心就是一个循环：**LLM 决定调用工具 → 执行 → 把结果喂回去 → 再决策**，直到任务完成。这个循环本身不难，网上几百个复刻。本项目想证明的是工程能力的另一半：

- ✅ **评测套件 (eval)** —— 12 个真实任务上的通过率可量化，支持 `--repeat` 跑 **pass@k**（LLM 非确定，单次数字不可信）与跨配置**消融**，不是"我跑了一下感觉还行"
- ✅ **可观测性 (trace)** —— 每一步的工具调用、token、成本、耗时都可复盘、可落盘
- ✅ **安全边界** —— 文件工具 (读/写/改/列) 经 `Workspace` 做路径约束，越界即拦截；`run_command` 另加危险命令护栏（并诚实说明它不是真隔离，见下文「安全边界」）
- ✅ **工具报错自恢复** —— 工具失败不会让流程崩溃，错误会回灌给模型让它自我修正
- ✅ **上下文压缩** —— 历史过长时截断陈旧的工具输出、保留近期轮次，避免顶到 token 上限（且不破坏 tool_call 配对）
- ✅ **可测试** —— 用可注入的 `ScriptedLLM`，agent 主循环**无需真实 API key 即可被单元测试覆盖**（21 个测试，含错误自恢复的开/关对照）
- ✅ **provider 无关** —— OpenAI 兼容，DeepSeek / 通义千问 / 智谱 GLM / OpenAI 改个环境变量即可

---

## 架构

```
        ┌──────────────────────────── Agent.run(task) ────────────────────────────┐
        │                                                                          │
  task ─┤  messages ──▶ LLM.chat(messages, tools) ──▶ 有 tool_calls? ──否──▶ 最终回答 │
        │      ▲                                          │是                      │
        │      │                                          ▼                        │
        │      └──── tool 结果 (含 ERROR) 回灌 ◀── ToolRegistry.call() ◀── Workspace  │
        │                                              (沙箱: read/write/list/run)   │
        └────────────────────── 每一步都记入 Trace (token/成本/耗时) ────────────────┘
```

| 模块 | 职责 |
|------|------|
| `miniagent/agent.py` | 主循环：决策→执行→回灌→终止，工具错误自恢复（可关，用于消融），上下文压缩 |
| `miniagent/llm.py` | LLM 抽象：`OpenAICompatLLM`(真实) + `ScriptedLLM`(测试用) + 成本估算 |
| `miniagent/tools.py` | 5 个工具(读/写/改/列/执行) + 注册表 + OpenAI function schema 生成 |
| `miniagent/safety.py` | `Workspace` 沙箱，路径越界拦截 |
| `miniagent/trace.py` | 结构化轨迹：统计与落盘 |
| `eval/` | 12 个任务 + 程序化检查器 + pass@k / 错误自恢复消融 / 成本报告 |
| `tests/` | 不依赖 key 的确定性单元测试 |

---

## 快速开始

### 1. 无需 key —— 验证核心逻辑

```bash
pip install pytest
python -m pytest -q
```

agent 主循环、工具分发、错误回灌、沙箱、上下文压缩、终止条件全部用 `ScriptedLLM` 确定性覆盖。

### 2. 配置一个真实模型（可选）

```bash
pip install -r requirements.txt
cp .env.example .env        # 填入你自己的 key（DeepSeek / Qwen / GLM 均可）
```

### 3. 跑起来

```bash
python cli.py "创建 hello.py 打印 Hello, Agent!，然后运行它确认输出"
python cli.py "修复 buggy.py 让测试通过" --workspace ./workspace --trace run.json
```

### 4. 评测（pass@k + 消融）

```bash
python eval/run_eval.py                       # 12 个任务各跑一次
python eval/run_eval.py --repeat 3            # 每题跑 3 次，报 pass@3 与平均单次通过率
python eval/run_eval.py --ablation --repeat 3 --json eval/results.json   # 量化"错误自恢复"的价值
```

```
🧪 12 题 × 3 次  (recover_errors=True)
==================================================================
✅ hello             3/3 (100%)  ~420tok
✅ fix_traceback     3/3 (100%)  ~1180tok
🟡 mutable_default   2/3 ( 67%)  ~1520tok
❌ rename_refactor   0/3 (  0%)  ~2100tok  ← geo.py 仍含不该出现的文本 'area_of_circle'
   ...
------------------------------------------------------------------
📊 pass@3 = 92%   平均单次通过率 = 78%   总 ... tok   $...

🔬 消融对比: 错误自恢复 (self-recovery) 的价值
==================================================================
  pass@3:          ON  92%   OFF  67%   Δ +25%
  平均单次通过率:   ON  78%   OFF  51%   Δ +27%
```

> ⚠️ 上面是**格式示意，不是真实跑分** —— 具体数字取决于所用模型。跑 `python eval/run_eval.py --ablation --repeat 3` 生成你自己的数字，再把"失败任务 → 失败原因 → 改进后通过率提升"写进简历——这正是 agent 岗最该展示的「测量→定位→改进」闭环。

---

## 几个值得一聊的设计决策

- **为什么把 `ScriptedLLM` 作为一等公民？** 因为 agent 最容易出 bug 的地方是循环控制流（终止条件、错误处理、消息拼装），而不是模型本身。把 LLM 抽象成接口、注入一个确定性假实现，循环逻辑就能被快速、免费、稳定地测试 —— 这是教程 clone 普遍缺失的一环。

- **工具错误为什么不抛异常而是回灌文本？** 真实 agent 跑起来工具一定会失败（文件不存在、命令报错）。让 agent "看见"错误并自我修正，比直接崩溃更接近生产形态，也是它 agentic 能力的体现。更进一步，这个机制是**可量化**的：`run_eval.py --ablation` 会在开/关错误自恢复两种配置下各跑一遍，直接报出它带来的通过率差值（Δ）；单测 `test_recovers_from_tool_error` 与 `test_no_recovery_aborts_on_tool_error` 一正一反锁定行为。

- **关于安全边界（诚实版）。** 文件工具全部经 `Workspace.resolve()` 约束在工作区内，`../` 或绝对路径越界即 `ValueError`，这部分是真边界。但 `run_command` 走 `shell=True`，`cwd` 设在工作区**并不构成隔离** —— 它仍能 `cat /etc/passwd`、联网、动系统文件。我对它只做了一层 *defense-in-depth* 的危险命令护栏（拦 `rm -rf /`、fork bomb、`sudo` 等），并明确知道黑名单本质可绕过。**真正隔离一个会跑 shell 的 agent，正确答案是 OS 级隔离（容器 / seccomp / 只读挂载 / 禁网），不是黑名单。** 把这个 tradeoff 讲清楚，比假装"已经安全"更重要 —— 这正是工程判断力。

- **为什么 provider 无关？** 把 `LLM_BASE_URL / LLM_MODEL / LLM_API_KEY` 抽出来，换模型零改代码，也方便用便宜模型做 eval、贵模型做对比。

## 已知限制 / Roadmap

- **`run_command` 非真隔离** —— 目前只有黑名单护栏（defense-in-depth）。生产形态应换 OS 级隔离：Docker 容器 + 只读挂载 + 禁网 + 资源/超时限制。
- **上下文压缩偏简单** —— 现为基于消息条数的截断策略，可升级为基于 token 计数 + 旧轮次摘要（summarization）。
- **eval 可继续做大** —— 现有 12 个任务（含修 traceback、可变默认参数、多文件改名、词频排序等更难项），已支持 pass@k 重复与错误自恢复消融；后续可扩到更大基准、引入更多任务类型与 LLM-as-judge 检查。
- **暂为单 agent** —— 可加 planner/executor 分工并做对比实验。
- **可观测性可视化** —— trace 已落盘 JSON，可再加一个 trace viewer。

## License

MIT
