# 用 Claude Code 或 Codex 启动 Trading Agent

这份指引讲的是：怎么让 TradingAgents 不走传统的 API Key，而是直接复用本机已经登录的
**Claude Code 订阅**（`claude` CLI）或 **OpenAI Codex 订阅**（`codex` CLI）来跑分析。

> 这两个 provider 是本 fork 新增的，**不在交互菜单里**。`tradingagents` 启动后那个
> "Select your LLM Provider" 列表只有 OpenAI / Anthropic / Google …，没有 `claude-code`
> 和 `codex`。它们只能通过环境变量 `TRADINGAGENTS_LLM_PROVIDER` 来选。这是使用它们的
> 唯一入口，下文所有方法都基于这一点。

---

## 一、先搞清楚两者的差别（很重要）

| | `claude-code` | `codex` |
|---|---|---|
| 底层 | `claude` CLI + `claude-agent-sdk`（进程内 SDK） | `codex exec` 子进程 |
| 鉴权 | `claude` CLI 的 OAuth（Pro/Max 订阅），**不需要** `ANTHROPIC_API_KEY` | `codex login` 的会话（ChatGPT 订阅或 `OPENAI_API_KEY`），由 CLI 自己管 |
| 工具调用（analyst 取数据） | **支持**，通过 SDK 的 MCP 桥接 | **不支持**，`bind_tools` 直接抛 `NotImplementedError` |
| 适合跑完整流程吗 | ✅ 可以跑默认 4 个分析师的完整 pipeline | ⚠️ 不行，见下方"Codex 的限制" |
| 结构化输出 | 不支持，自动降级为自由文本 | 不支持，自动降级为自由文本 |

**一句话结论**：想跑完整的交易分析流程，用 **`claude-code`**。`codex` 因为不能给分析师绑工具，
跑默认流程会在 market / news / fundamentals 分析师那一步崩溃，只适合受限用法（见第六节）。

---

## 二、前置准备

### 2.1 通用

```bash
cd /Users/liuxuheng/OwnDevWorkspaces/TradingAgentsFork
# 装好依赖（claude-agent-sdk 已是项目硬依赖）
pip install .
# 数据源还是要的（行情/财报/新闻走 yfinance，免 key；可选 Alpha Vantage）
export ALPHA_VANTAGE_API_KEY=...   # 可选
```

### 2.2 用 claude-code 时

```bash
# 1) 确认 claude CLI 已安装并登录（订阅态）
claude --version          # 例如 2.1.161 (Claude Code)
# 没登录就跑一次 claude，按提示用浏览器登录 Pro/Max 账号

# 2) 确认 SDK 在当前 Python 环境里
python -c "import claude_agent_sdk; print('ok')"   # 已是 pyproject 依赖
```

无需设置 `ANTHROPIC_API_KEY` —— 鉴权完全走 `claude` 的 OAuth 会话。

### 2.3 用 codex 时

```bash
# 1) 安装 codex CLI（任选其一）
npm install -g @openai/codex
#   或  brew install codex
codex --version           # 需要 0.136+，本指引按 0.136 的 CLI 行为编写

# 2) 登录（ChatGPT 订阅或 API Key 模式都行，由 CLI 自己存）
codex login
ls ~/.codex/auth.json     # 出现这个文件说明已登录
```

无需设置 `OPENAI_API_KEY` —— 端点和鉴权由 `codex` CLI 自己持有。TradingAgents 这一层不做 key 检查。

---

## 三、启动方式 A：写进 `.env`（推荐，可非交互）

把 provider 和模型写进 `.env`，启动时这几步就会被自动跳过（CLI 会打印 `✓ ... from environment`）。

### claude-code（完整流程）

在项目根目录 `.env` 里加：

```bash
TRADINGAGENTS_LLM_PROVIDER=claude-code
TRADINGAGENTS_DEEP_THINK_LLM=claude-opus-4-8     # 深度推理：研究经理 / 组合经理
TRADINGAGENTS_QUICK_THINK_LLM=claude-sonnet-4-6  # 快思考：分析师 / 辩论 / 交易员
```

可选模型（白名单见 `claude_code_client.py`）：
- deep：`claude-opus-4-8`、`claude-opus-4-7`、`claude-opus-4-6`、`claude-sonnet-4-6`
- quick：`claude-sonnet-4-6`、`claude-haiku-4-5`、`claude-sonnet-4-5`

然后启动：

```bash
tradingagents            # 已安装的命令
# 或   python -m cli.main
```

启动后只剩这些要手动选：**股票代码 → 分析日期 → 选哪些分析师 → 研究深度**。
Provider、模型、effort、语言都因为 env 已设而被跳过。

### codex（仅限非工具节点，见第六节）

```bash
TRADINGAGENTS_LLM_PROVIDER=codex
TRADINGAGENTS_DEEP_THINK_LLM=gpt-5.5             # 或 gpt-5.4
TRADINGAGENTS_QUICK_THINK_LLM=gpt-5.4-mini       # 或 gpt-5.4
```

> codex 在 ChatGPT 订阅下后端只放行固定几个 GPT-5.x：`gpt-5.5` / `gpt-5.4` / `gpt-5.4-mini`。
> 填别的（如 `gpt-5`、`o4-mini`）会被后端拒成 `invalid_request_error`。

---

## 四、启动方式 B：命令行临时设置（不落盘）

不想改 `.env`，就在启动命令前临时 `export`：

```bash
# claude-code
TRADINGAGENTS_LLM_PROVIDER=claude-code \
TRADINGAGENTS_DEEP_THINK_LLM=claude-opus-4-8 \
TRADINGAGENTS_QUICK_THINK_LLM=claude-sonnet-4-6 \
tradingagents
```

```bash
# codex
TRADINGAGENTS_LLM_PROVIDER=codex \
TRADINGAGENTS_DEEP_THINK_LLM=gpt-5.5 \
TRADINGAGENTS_QUICK_THINK_LLM=gpt-5.4-mini \
tradingagents
```

效果和方式 A 一样：provider/模型从环境读取，交互里不再问。

---

## 五、启动方式 C：Python API（写脚本跑）

直接在代码里指定 provider，跳过 CLI：

```python
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "claude-code"          # 或 "codex"
config["deep_think_llm"] = "claude-opus-4-8"    # codex 用 "gpt-5.5"
config["quick_think_llm"] = "claude-sonnet-4-6" # codex 用 "gpt-5.4-mini"

ta = TradingAgentsGraph(debug=True, config=config)
_, decision = ta.propagate("NVDA", "2026-01-15")
print(decision)
```

`main.py` 也会读 `TRADINGAGENTS_*` 环境变量，所以方式 A 的 `.env` 配好后直接
`python main.py` 也能用 claude-code / codex 跑。

---

## 六、Codex 的限制与建议（务必读）

codex 的适配器是一个 `codex exec` 子进程包装，**故意不实现 `bind_tools`**：codex CLI 跑的是
它自己内置的工具循环，没有办法把 LangChain 的工具描述符交给它。

而 TradingAgents 的三个分析师 —— **market / news / fundamentals** —— 会对 quick 模型直接调
`llm.bind_tools(tools)` 去取行情、新闻、财报。因为 quick 和 deep 共用同一个 `llm_provider`，
一旦 provider 是 codex，跑到这些分析师就会抛 `NotImplementedError` 崩掉。

所以：

- **要跑完整默认流程（含 market/news/fundamentals）→ 用 `claude-code`**，它有 MCP 工具桥接。
- **非要用 codex** 时，只能避开会绑工具的分析师：在 "选哪些分析师" 那一步**只勾
  `Social / sentiment`**（情绪分析师不绑工具），其余研究员、经理、交易员、风险辩论等非工具节点
  codex 都能跑。但这样数据面会很弱，仅适合做对话/推理实验。
- 想两全（codex 负责推理、key provider 负责取数）目前**做不到**：架构里 quick 和 deep 是同一个
  provider，不能分别指定。

---

## 七、验证与排错

**快速自检 provider 能否构造：**

```bash
python -c "from tradingagents.llm_clients import create_llm_client; \
print(create_llm_client('claude-code','claude-sonnet-4-6').get_llm()._llm_type)"
# 期望输出: claude-code

python -c "from tradingagents.llm_clients import create_llm_client; \
print(create_llm_client('codex','gpt-5.4-mini').get_llm()._llm_type)"
# 期望输出: codex （若报 'codex CLI ... on PATH' 说明没装/没在 PATH）
```

常见报错：

| 现象 | 原因 / 处理 |
|---|---|
| `The 'codex' provider requires the codex CLI on PATH` | 没装 codex 或不在 PATH：`npm i -g @openai/codex` 后重开终端 |
| `codex exited with code ...: model is not supported when using Codex with a ChatGPT account` | 模型不在白名单，改回 `gpt-5.5/5.4/5.4-mini` |
| `claude-agent-sdk is required for the 'claude-code' provider` | `pip install claude-agent-sdk`（或 `pip install .`） |
| 跑到 market/news/fundamentals 抛 `NotImplementedError ... bind_tools` | 你在用 codex 跑工具型分析师，见第六节，改用 claude-code 或只选 social |
| `claude-code returned error-only text on both attempts` | 上游 Claude API 抖动/限流，稍后重试；适配器已自动重试过一次 |
| 菜单里找不到 claude-code / codex | 正常，它们不在交互菜单，必须用 `TRADINGAGENTS_LLM_PROVIDER` 环境变量选 |

**观察用量**：claude-code 每次调用会打 `INFO` 日志 `claude-code usage: ...`（含 cache 命中），
codex 会打 `codex call: model=... output_len=...`，方便核对订阅消耗。

---

## 八、最小可跑示例（claude-code，复制即用）

```bash
cd /Users/liuxuheng/OwnDevWorkspaces/TradingAgentsFork
claude --version                       # 确认已登录
TRADINGAGENTS_LLM_PROVIDER=claude-code \
TRADINGAGENTS_DEEP_THINK_LLM=claude-opus-4-8 \
TRADINGAGENTS_QUICK_THINK_LLM=claude-sonnet-4-6 \
tradingagents
# 接着按提示输入：AAPL → 2026-01-15 → 选分析师 → 研究深度，回车开跑
```

结果与决策日志默认落在 `~/.tradingagents/`（`logs/`、`memory/trading_memory.md`）。
