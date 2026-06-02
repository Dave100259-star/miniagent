# miniagent

> 一个 ~600 行、带**评测**与**可观测性**的极简 coding agent。
> 不是又一个 "用 200 行复刻 Claude Code" 的教程仓库 —— 重点不在循环本身，而在**怎么测量和保证它的质量**。

一个 coding agent 的核心就是一个循环：**LLM 决定调用工具 → 执行 → 把结果喂回去 → 再决策**，直到任务完成。这个循环本身不难，网上几百个复刻。本项目想证明的是工程能力的另一半：

- ✅ **评测套件 (eval)** —— agent 在一组真实任务上的通过率是可量化的，不是"我跑了一下感觉还行"
- ✅ **可观测性 (trace)** —— 每一步的工具调用、token、成本、耗时都可复盘、可落盘
- ✅ **沙箱安全** —— 文件读写被约束在工作区内，路径越界直接拦截
- ✅ **工具报错自恢复** —— 工具失败不会让流程崩溃，错误会回灌给模型让它自我修正
- ✅ **上下文压缩** —— 历史过长时截断陈旧的工具输出、保留近期轮次，避免顶到 token 上限（且不破坏 tool_call 配对）
- ✅ **可测试** —— 用可注入的 `ScriptedLLM`，agent 主循环**无需真实 API key 即可被单元测试覆盖**（18 个测试）
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
| `miniagent/agent.py` | 主循环：决策→执行→回灌→终止，工具错误自恢复，上下文压缩 |
| `miniagent/llm.py` | LLM 抽象：`OpenAICompatLLM`(真实) + `ScriptedLLM`(测试用) + 成本估算 |
| `miniagent/tools.py` | 5 个工具(读/写/改/列/执行) + 注册表 + OpenAI function schema 生成 |
| `miniagent/safety.py` | `Workspace` 沙箱，路径越界拦截 |
| `miniagent/trace.py` | 结构化轨迹：统计与落盘 |
| `eval/` | 评测任务 + 程序化检查器 + 通过率/成本报告 |
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

### 4. 评测

```bash
python eval/run_eval.py
```

```
🧪 评测 6 个任务
============================================================
✅ PASS  hello        steps=2  tok=...  $...
✅ PASS  add_func     steps=3  tok=...  $...
✅ PASS  fix_bug      steps=4  tok=...  $...
✅ PASS  wordcount    steps=3  tok=...  $...
✅ PASS  calc_module  steps=3  tok=...  $...
✅ PASS  json_cfg     steps=3  tok=...  $...
============================================================
📊 通过率 6/6 = 100%  |  总 token ...  |  总成本 $...  |  用时 ...s
```

> 评测结果取决于所用模型；上面是格式示意，跑 `python eval/run_eval.py` 生成你自己的数字填进来。

---

## 几个值得一聊的设计决策

- **为什么把 `ScriptedLLM` 作为一等公民？** 因为 agent 最容易出 bug 的地方是循环控制流（终止条件、错误处理、消息拼装），而不是模型本身。把 LLM 抽象成接口、注入一个确定性假实现，循环逻辑就能被快速、免费、稳定地测试 —— 这是教程 clone 普遍缺失的一环。

- **工具错误为什么不抛异常而是回灌文本？** 真实 agent 跑起来工具一定会失败（文件不存在、命令报错）。让 agent "看见"错误并自我修正，比直接崩溃更接近生产形态，也是它 agentic 能力的体现。`test_recovers_from_tool_error` 专门覆盖这一点。

- **为什么坚持沙箱？** 一个能 `run_command` 的 agent 不加边界就是安全隐患。`Workspace` 把所有路径约束在工作区内，越界即 `ValueError`。这是"判断力"而非"功能"。

- **为什么 provider 无关？** 把 `LLM_BASE_URL / LLM_MODEL / LLM_API_KEY` 抽出来，换模型零改代码，也方便用便宜模型做 eval、贵模型做对比。

## 已知限制 / Roadmap

- 上下文压缩目前是基于消息条数的简单策略 → 可升级为基于 token 计数 + 摘要
- 工具集仍偏小（5 个），可扩展 `grep` / `apply_patch` / 受控网络访问等
- eval 任务偏小（6 个 Python 任务），可扩成更大基准
- 暂为单 agent，未来可加 planner/executor 分工对比效果

## License

MIT
