# miniagent

[![CI](https://github.com/Dave100259-star/miniagent/actions/workflows/ci.yml/badge.svg)](https://github.com/Dave100259-star/miniagent/actions/workflows/ci.yml)
![tests](https://img.shields.io/badge/tests-44%20passed-brightgreen)
![python](https://img.shields.io/badge/python-3.10%2B-blue)

> 一个 ~600 行、带**评测**与**可观测性**的极简 coding agent。
> 不是又一个 "用 200 行复刻 Claude Code" 的教程仓库 —— 重点不在循环本身，而在**怎么测量和保证它的质量**。

一个 coding agent 的核心就是一个循环：**LLM 决定调用工具 → 执行 → 把结果喂回去 → 再决策**，直到任务完成。这个循环本身不难，网上几百个复刻。本项目想证明的是工程能力的另一半：

- ✅ **评测套件 (eval)** —— 23 个真实任务上的通过率可量化，支持 `--repeat` 重复跑出 **pass@1 均值 + bootstrap 95% 置信区间**（LLM 非确定，单点数字会骗人）、跨配置**消融**与**跨模型矩阵**，还报**单位成功成本**，不是"我跑了一下感觉还行"
- ✅ **可观测性 (trace)** —— 每一步的工具调用、token、成本、耗时都可复盘、可落盘
- ✅ **可插拔执行后端 (Executor)** —— `run_command` 抽象为接口：`LocalExecutor`（宿主机护栏版）/ `DockerExecutor`（`--network none` + 只读根 + 仅挂工作区 + 资源限额），把"我知道正确答案是容器隔离"升级为"我实现了"
- ✅ **MCP host** —— 通过 stdio 连接任意 [MCP](https://modelcontextprotocol.io) server，自动发现远程工具 → 转 function schema → 命名空间注入主循环（`--mcp "..."`）。从"写了个 agent"升级为"搭了个能接入工具生态的 host"
- ✅ **可观测性 (trace) + 可视化** —— 每一步的工具调用 / token / 成本 / 耗时可落盘成 JSON，配单文件 `viewer/index.html` 渲染时间线与调用树
- ✅ **安全边界（诚实版）** —— 文件工具经 `Workspace` 做路径约束，越界即拦截；`run_command` 加危险命令护栏，并诚实说明本地版不是真隔离（真隔离用 `DockerExecutor`，见下文）
- ✅ **自我修正** —— 工具/命令失败（含测试跑挂）不会让流程崩溃，错误回灌给模型让它观察→重试→修复；该机制的价值由消融实验量化
- ✅ **上下文压缩** —— 历史过长时截断陈旧的工具输出、保留近期轮次，且不破坏 tool_call 配对
- ✅ **可测试** —— 用可注入的 `ScriptedLLM`，agent 主循环**无需真实 API key 即可被单元测试覆盖**（44 个测试，含自我修正开/关对照、bootstrap 统计、MCP 端到端、Executor 注入）
- ✅ **provider 无关** —— OpenAI 兼容，DeepSeek / 通义千问 / 智谱 GLM / OpenAI 改个环境变量即可

---

## 架构

```
            ┌── eval: 配置 {自我修正 on/off} × repeat ─▶ pass@1 + bootstrap 置信区间 + 单位成功成本 ──┐
            │                                                                                       │
 task ─▶ Agent 主循环 ─▶ LLM.chat(messages, tools)  (DeepSeek/Qwen/GLM  ◀▶  ScriptedLLM 测试桩)
            │                         │ 有 tool_calls? ─否─▶ 最终回答
            │                         ▼ 是
            ├─ 内建工具 ─▶ Workspace 路径沙箱 ─▶ Executor 接口
            │   (读/写/改/列/run)                  ├─ LocalExecutor  (黑名单护栏)
            │        ▲                             └─ DockerExecutor (禁网 / 只读根 / 仅挂工作区 / 限额)
            │        └── 结果(含 ERROR/exit≠0) 回灌, 触发自我修正
            └─ MCP client ─▶ 外部 MCP server (stdio, JSON-RPC; 工具自动发现 + 命名空间注入)

 全程每一步 ─▶ Trace (JSON: token/成本/耗时) ─▶ viewer/index.html (单页可视化)
```

| 模块 | 职责 |
|------|------|
| `miniagent/agent.py` | 主循环：决策→执行→回灌→终止，自我修正（观察失败→重试，可关，用于消融），上下文压缩 |
| `miniagent/llm.py` | LLM 抽象：`OpenAICompatLLM`(真实) + `ScriptedLLM`(测试用) + 成本估算 |
| `miniagent/tools.py` | 5 个工具(读/写/改/列/执行) + 注册表 + OpenAI function schema 生成 |
| `miniagent/executor.py` | 命令执行后端接口：`LocalExecutor`(护栏) / `DockerExecutor`(OS 级隔离) |
| `miniagent/mcp.py` | MCP stdio 客户端：握手 + tools/list + tools/call + 命名空间注入 |
| `miniagent/safety.py` | `Workspace` 沙箱，路径越界拦截 |
| `miniagent/trace.py` | 结构化轨迹：统计与落盘 |
| `eval/` | 23 个任务 + 检查器 + pass@1 置信区间 / 自我修正消融 / 跨模型聚合 (`aggregate.py`) / 单位成功成本 |
| `viewer/` | 单文件 trace 可视化 (`index.html` + `sample_run.json` 样例) |
| `examples/` | 可运行的 demo MCP server (`mcp_demo_server.py`) |
| `tests/` | 不依赖 key 的确定性单元测试 (44 个，含 MCP 端到端) |

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
python eval/run_eval.py --repeat 5            # 23 题各跑 5 次，报 pass@1 均值 + 95% 置信区间
python eval/run_eval.py --ablation --repeat 5 --json eval/results.json   # 量化"自我修正"的价值
# 跨模型矩阵: 换 --model 多跑几个, 再聚合
python eval/run_eval.py --ablation --repeat 5 --model qwen-plus --json eval/r_qwen.json
python eval/aggregate.py eval/results.json eval/r_qwen.json
```

```
🔬 消融对比: 自我修正 (观察失败→重试修复) 的价值
==================================================================
  pass@1 (均值):    ON  100%   OFF   61%   Δ +39%
  pass@1 95%CI:     ON [100%,100%]   OFF [52%,70%]   (n=115/配置)
  pass@5:           ON  100%   OFF   61%   Δ +39%
  总成本:           ON $0.22459 OFF $0.15527  ← ON 更贵 (+45%)
  单位成功成本:     ON $0.00195   OFF $0.00222  ← 但按"每次成功"摊, ON 反而更便宜
```

> 📌 **实测**（DeepSeek-chat，23 题 × 5 次，n=115/配置，2026-06，`eval/results.json` 可复现）：关闭自我修正使 pass@1 从 **100% 跌到 61%（95%CI [52%,70%]，Δ +39pp）**。
>
> **怎么读这个 Δ（诚实版）**：差值并非"凭空"——它精准**定位在那些"必须先观察到失败、再修复"的任务上**（修 traceback、双 bug、类状态共享等）；对于无需试错的任务（纯构建题、或模型靠静态推理直接改对的 `fix_bug`），ON 与 OFF **完全一致**。这正是消融的意义：**隔离并量化某机制究竟在哪里、贡献多少**，而非报一个笼统总分。
>
> **成本叙事（反直觉但诚实）**：自我修正让总成本高 45%（不轻易放弃），但按**单位成功成本**（总成本 ÷ 成功次数）衡量，ON \$0.00195 反而**低于** OFF \$0.00222——OFF 的"省钱"是用提前放弃、更低通过率换来的。同时报这两个数，避免"便宜"的错觉。
>
> **关于 ON 的饱和**：DeepSeek-chat 太强，开着自我修正能解全部 23 题（CI 退化为 [100%,100%]）。要让"量尺"在 ON 端也有刻度，正确做法是**跨模型**——在更弱的模型上 ON 会掉破 100%，并可验证 **Δ 是否随模型变强而缩小**（强模型靠静态推理、不依赖试错）。用 `aggregate.py` 出"模型 × 配置"矩阵即可。

### 5. 进阶：容器隔离 / MCP / 可视化

```bash
# 用 Docker 隔离执行命令 (禁网 + 只读根 + 仅挂工作区, 需本机有 docker)
python cli.py "跑一下 ls 看看" --executor docker

# 作为 MCP host 连接外部 server, 自动挂载其工具 (这里用自带 demo server)
python cli.py "用 mcp 工具把 12 和 30 相加，再把结果字符串倒过来" \
    --mcp "python examples/mcp_demo_server.py"

# 可视化 trace: 先跑出 run.json, 再用浏览器打开 viewer/index.html 载入它
python cli.py "创建并运行 hello.py" --trace run.json
#   → 打开 viewer/index.html, 选择 run.json (仓库已带 viewer/sample_run.json 样例)
```

---

## 几个值得一聊的设计决策

- **为什么把 `ScriptedLLM` 作为一等公民？** 因为 agent 最容易出 bug 的地方是循环控制流（终止条件、错误处理、消息拼装），而不是模型本身。把 LLM 抽象成接口、注入一个确定性假实现，循环逻辑就能被快速、免费、稳定地测试 —— 这是教程 clone 普遍缺失的一环。

- **工具/命令失败为什么不抛异常而是回灌文本？** 真实 agent 跑起来一定会遇到失败（文件不存在、命令报错、测试跑挂）。让 agent "看见"失败（含 `run_command` 的非零退出）并**观察→重试→修复**，比直接崩溃更接近生产形态，也是它 agentic 能力的核心。更进一步，这个机制是**可量化**的：`run_eval.py --ablation` 会在开/关自我修正两种配置下各跑一遍，直接报出它带来的通过率差值（Δ）；单测 `test_self_correct_continues_after_failing_command` 与 `test_no_self_correct_aborts_on_failing_command` 一正一反锁定行为。

- **关于安全边界（诚实版）。** 文件工具全部经 `Workspace.resolve()` 约束在工作区内，`../` 或绝对路径越界即 `ValueError`，这部分是真边界。而 `run_command` 走 `shell=True`，宿主机直跑**并不构成隔离** —— 黑名单护栏（拦 `rm -rf /`、fork bomb、`sudo` 等）只是 *defense-in-depth*，本质可绕过。**所以我没有止步于"知道答案"：把执行抽象成 `Executor` 接口，并实现了 `DockerExecutor`（`--network none` 禁网 + 只读根 + 仅挂工作区可写 + 内存/CPU/pids 限额），这才是约束"会跑 shell 的 agent"的正确形态。** 残余风险也写在 `executor.py` 注释里（docker 守护进程攻击面、未做 seccomp 自定义 profile）—— 自己挖的坑自己填、并说清没填完的部分，比假装"已经安全"更有说服力。

- **为什么 provider 无关？** 把 `LLM_BASE_URL / LLM_MODEL / LLM_API_KEY` 抽出来，换模型零改代码，也方便用便宜模型做 eval、贵模型做对比。

## 已知限制 / Roadmap

定位：不只是"带评测的极简 agent"，而是**每加一个机制，就量化一次它的价值**——消融是方法论，不是一次性卖点。

**已完成（v2）**
- ✅ eval 扩到 **23 个任务**（含多文件重构、双 bug、干扰文件等更难项）；headline 升级为 **pass@1 均值 + bootstrap 95% 置信区间**；新增**单位成功成本**与**跨模型聚合**（`aggregate.py`）。
- ✅ **`Executor` 接口** —— `LocalExecutor`（护栏）/ `DockerExecutor`（禁网 + 只读根 + 仅挂工作区 + 资源限额），命令执行隔离从"知道答案"变为"已实现"。
- ✅ **MCP host** —— stdio 连接任意 MCP server，工具自动发现 + 命名空间注入主循环；自带可运行 demo server 与端到端测试。
- ✅ **trace viewer** —— 单文件 HTML 载入 `run.json`，渲染概览卡片 / 决策·工具时间线 / 调用树。

**下一步**
- **上下文压缩 v2** —— 从"按消息条数截断"升级为"token 预算 + 旧轮摘要"，并作为**第二条消融轴**量化不同压缩策略。
- **跨模型矩阵** —— 在 Qwen / GLM 上各跑一遍消融，验证 **Δ 是否随模型变强而缩小**（"机制 × 能力"交互）。
- **外部锚点** —— 用官方 harness 跑若干 SWE-bench-lite 题，获得公认坐标系。

## License

MIT
