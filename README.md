# miniagent

[![CI](https://github.com/Dave100259-star/miniagent/actions/workflows/ci.yml/badge.svg)](https://github.com/Dave100259-star/miniagent/actions/workflows/ci.yml)
![tests](https://img.shields.io/badge/tests-30%20passed-brightgreen)
![python](https://img.shields.io/badge/python-3.10%2B-blue)

> 一个 ~600 行、带**评测**与**可观测性**的极简 coding agent。
> 不是又一个 "用 200 行复刻 Claude Code" 的教程仓库 —— 重点不在循环本身，而在**怎么测量和保证它的质量**。

一个 coding agent 的核心就是一个循环：**LLM 决定调用工具 → 执行 → 把结果喂回去 → 再决策**，直到任务完成。这个循环本身不难，网上几百个复刻。本项目想证明的是工程能力的另一半：

- ✅ **评测套件 (eval)** —— 18 个真实任务上的通过率可量化，支持 `--repeat` 跑 **pass@k**（LLM 非确定，单次数字不可信）与跨配置**消融**，不是"我跑了一下感觉还行"
- ✅ **可观测性 (trace)** —— 每一步的工具调用、token、成本、耗时都可复盘、可落盘
- ✅ **安全边界** —— 文件工具 (读/写/改/列) 经 `Workspace` 做路径约束，越界即拦截；`run_command` 另加危险命令护栏（并诚实说明它不是真隔离，见下文「安全边界」）
- ✅ **工具报错自恢复** —— 工具失败不会让流程崩溃，错误会回灌给模型让它自我修正
- ✅ **上下文压缩** —— 历史过长时截断陈旧的工具输出、保留近期轮次，避免顶到 token 上限（且不破坏 tool_call 配对）
- ✅ **可测试** —— 用可注入的 `ScriptedLLM`，agent 主循环**无需真实 API key 即可被单元测试覆盖**（30 个测试，含自我修正的开/关对照）
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
| `miniagent/agent.py` | 主循环：决策→执行→回灌→终止，自我修正（观察失败→重试，可关，用于消融），上下文压缩 |
| `miniagent/llm.py` | LLM 抽象：`OpenAICompatLLM`(真实) + `ScriptedLLM`(测试用) + 成本估算 |
| `miniagent/tools.py` | 5 个工具(读/写/改/列/执行) + 注册表 + OpenAI function schema 生成 |
| `miniagent/safety.py` | `Workspace` 沙箱，路径越界拦截 |
| `miniagent/trace.py` | 结构化轨迹：统计与落盘 |
| `eval/` | 18 个任务 + 程序化检查器 + pass@k / 自我修正消融 / 成本报告 |
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
python eval/run_eval.py                       # 18 个任务各跑一次
python eval/run_eval.py --repeat 3            # 每题跑 3 次，报 pass@3 与平均单次通过率
python eval/run_eval.py --ablation --repeat 3 --json eval/results.json   # 量化"自我修正"的价值
```

```
🧪 [自我修正 ON]  18 题 × 3 次  (recover_errors=True)
✅ 全部 18 题 3/3 通过
📊 pass@3 = 100%   平均单次通过率 = 100%   ~308k tok   $0.10

🧪 [自我修正 OFF] 18 题 × 3 次  (recover_errors=False)
❌ fix_traceback / fix_offbyone / fix_empty_crash / fix_keyerror /
   fix_int_div / fix_case_count / fix_paren_match  →  0/3
   (一看到测试失败 exit≠0 就放弃, 早停; 注意失败任务 token 仅 ON 的 ~1/3)
📊 pass@3 = 61%   平均单次通过率 = 61%   ~208k tok   $0.07

🔬 消融对比: 自我修正 (观察失败→重试修复) 的价值
==================================================================
  pass@3:          ON 100%   OFF  61%   Δ +39%
  平均单次通过率:   ON 100%   OFF  61%   Δ +39%
```

> 📌 **实测数据**（DeepSeek-chat，18 题 × 3 次，2026-06）。结果可复现：`python eval/run_eval.py --ablation --repeat 3`。
>
> **怎么读这个 +39%（诚实版）**：差值并非"凭空"——它精准地**定位在那 7 道"必须先观察到失败、再修复"的任务上**；而对于无需试错的任务（如纯构建题、以及模型靠静态推理就能直接改对的 `fix_bug`/`mutable_default`），ON 与 OFF **完全一致**。这正是消融实验的意义：**隔离并量化某个机制究竟在哪里、贡献多少**，而不是报一个笼统的总分。

---

## 几个值得一聊的设计决策

- **为什么把 `ScriptedLLM` 作为一等公民？** 因为 agent 最容易出 bug 的地方是循环控制流（终止条件、错误处理、消息拼装），而不是模型本身。把 LLM 抽象成接口、注入一个确定性假实现，循环逻辑就能被快速、免费、稳定地测试 —— 这是教程 clone 普遍缺失的一环。

- **工具/命令失败为什么不抛异常而是回灌文本？** 真实 agent 跑起来一定会遇到失败（文件不存在、命令报错、测试跑挂）。让 agent "看见"失败（含 `run_command` 的非零退出）并**观察→重试→修复**，比直接崩溃更接近生产形态，也是它 agentic 能力的核心。更进一步，这个机制是**可量化**的：`run_eval.py --ablation` 会在开/关自我修正两种配置下各跑一遍，直接报出它带来的通过率差值（Δ）；单测 `test_self_correct_continues_after_failing_command` 与 `test_no_self_correct_aborts_on_failing_command` 一正一反锁定行为。

- **关于安全边界（诚实版）。** 文件工具全部经 `Workspace.resolve()` 约束在工作区内，`../` 或绝对路径越界即 `ValueError`，这部分是真边界。但 `run_command` 走 `shell=True`，`cwd` 设在工作区**并不构成隔离** —— 它仍能 `cat /etc/passwd`、联网、动系统文件。我对它只做了一层 *defense-in-depth* 的危险命令护栏（拦 `rm -rf /`、fork bomb、`sudo` 等），并明确知道黑名单本质可绕过。**真正隔离一个会跑 shell 的 agent，正确答案是 OS 级隔离（容器 / seccomp / 只读挂载 / 禁网），不是黑名单。** 把这个 tradeoff 讲清楚，比假装"已经安全"更重要 —— 这正是工程判断力。

- **为什么 provider 无关？** 把 `LLM_BASE_URL / LLM_MODEL / LLM_API_KEY` 抽出来，换模型零改代码，也方便用便宜模型做 eval、贵模型做对比。

## 已知限制 / Roadmap

- **`run_command` 非真隔离** —— 目前只有黑名单护栏（defense-in-depth）。生产形态应换 OS 级隔离：Docker 容器 + 只读挂载 + 禁网 + 资源/超时限制。
- **上下文压缩偏简单** —— 现为基于消息条数的截断策略，可升级为基于 token 计数 + 旧轮次摘要（summarization）。
- **eval 可继续做大** —— 现有 18 个任务（含修 traceback、可变默认参数、多文件改名、词频排序、括号匹配等更难项），已支持 pass@k 重复与自我修正消融；后续可扩到更大基准、引入更多任务类型与 LLM-as-judge 检查。
- **暂为单 agent** —— 可加 planner/executor 分工并做对比实验。
- **可观测性可视化** —— trace 已落盘 JSON，可再加一个 trace viewer。

## License

MIT
