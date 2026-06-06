# 设计:为 dataflows 增加 Financial Modeling Prep (FMP) 数据源

**日期:** 2026-06-06
**状态:** 待实现
**作者:** Xuheng Liu (with Claude)

## 背景与目标

`tradingagents/dataflows` 现有一套 vendor 路由层([interface.py](../../../tradingagents/dataflows/interface.py)):核心数据工具(OHLCV、技术指标、基本面、新闻、内部人交易)按类别/单工具配置 vendor,目前注册了 `yfinance`(默认)与 `alpha_vantage`(可选),路由层 `route_to_vendor` 提供"限流/无数据自动 fallback 到下一个 vendor"的能力。

目标:新增 **第三个 vendor `fmp`**(Financial Modeling Prep),覆盖全部五类数据,作为可选项接入,不改变默认数据源。

## 已确认的范围决策

| 决策点 | 结论 |
|------|------|
| 覆盖范围 | **五类全覆盖**,与 `alpha_vantage` 对等 |
| 默认配置 | **仅作为可选项**,`data_vendors` 默认值保持 yfinance 不变 |
| FMP 账号 | **免费计划**(美股、~250 请求/天;新闻/内部人交易/季度报表可能 Premium 受限) |
| 技术指标实现 | **本地 stockstats 计算**(用 FMP 拉的 OHLCV 喂给 stockstats,与 yfinance 路径同一套数学) |

## 总体架构

完全套用现有 `alpha_vantage_*` 的文件拆分模式。新增文件:

| 新文件 | 职责 |
|------|------|
| `fmp_common.py` | API 客户端 `_make_api_request`、`FMP_API_KEY` 鉴权、错误类型、symbol 规整、空结果→`NoMarketDataError`、JSON→DataFrame/CSV 辅助 |
| `fmp_stock.py` | `get_stock(symbol, start_date, end_date)` → OHLCV CSV;`load_fmp_ohlcv(symbol, curr_date)` → 清洗后的 DataFrame(供指标复用) |
| `fmp_indicator.py` | `get_indicator(symbol, indicator, curr_date, look_back_days, ...)` → 复用共享指标窗口逻辑 |
| `fmp_fundamentals.py` | `get_fundamentals` / `get_balance_sheet` / `get_cashflow` / `get_income_statement`,带 `curr_date` 防未来过滤 |
| `fmp_news.py` | `get_news` / `get_global_news` / `get_insider_transactions` |
| `fmp.py` | 派发器,re-export 上述函数(对应 [alpha_vantage.py](../../../tradingagents/dataflows/alpha_vantage.py)) |

复用/重构的共享文件:

| 新建/改动 | 职责 |
|------|------|
| `vendor_errors.py`(新) | 共享错误基类 `VendorRateLimitError`,供 AV/FMP 共同继承 |
| `indicators_common.py`(新) | 从 [y_finance.py](../../../tradingagents/dataflows/y_finance.py) 抽出 vendor 无关的指标窗口逻辑(`best_ind_params` 描述表、bulk 计算、窗口字符串拼装),对外暴露 `indicator_window_from_frame(ohlcv_df, symbol, indicator, curr_date, look_back_days)`(调用方先把 OHLCV DataFrame 备好再传入) |

改动现有文件:
- `interface.py`:`VENDOR_LIST` 追加 `"fmp"`;`VENDOR_METHODS` 每个方法补 `"fmp": <impl>` 映射;`except AlphaVantageRateLimitError` 改为 `except VendorRateLimitError`
- `alpha_vantage_common.py`:`AlphaVantageRateLimitError` 改为继承 `VendorRateLimitError`(保留类名,向后兼容)
- `y_finance.py` / `stockstats_utils.py`:把指标窗口与 bulk 计算逻辑替换为调用 `indicators_common`(行为不变)
- `default_config.py`:仅更新注释,把各类别 `# Options:` 补上 `fmp`(**不改默认值**)

## 关键设计点

### 1. FMP API 客户端与鉴权(`fmp_common.py`)

- Base URL:`https://financialmodelingprep.com/stable/`(FMP 当前推荐的 stable API)。
- API key:`os.getenv("FMP_API_KEY")`,与 AV 直接读环境变量一致(**不**走 `_ENV_OVERRIDES`)。缺失时抛 `FMPNotConfiguredError(ValueError)`。
- `_make_api_request(endpoint, params)`:拼 `apikey`,GET,解析 JSON。
- FMP 免费档常见端点(stable):
  - 行情:`/historical-price-eod/full?symbol=&from=&to=`
  - 画像:`/profile?symbol=`
  - 财报:`/income-statement`、`/balance-sheet-statement`、`/cash-flow-statement`(`?symbol=&period=annual|quarter&limit=`)
  - 比率:`/ratios`、`/key-metrics`(用于补全 fundamentals overview)
  - 新闻:`/news/stock?symbols=`、`/news/general-latest`
  - 内部人交易:`/insider-trading/search?symbol=`

### 2. 免费档优雅降级(核心健壮性)

免费档对**新闻、内部人交易、季度报表**很可能返回 Premium 拦截或额度耗尽。`fmp_common` 检测以下情况并抛 `FMPRateLimitError(VendorRateLimitError)`:
- HTTP 状态码 `429`(额度耗尽)、`401/402/403`(鉴权/Premium 受限,但**有 key** 时按"此 vendor 跳过"处理)
- JSON 响应含 `"Error Message"` 且文本匹配 `limit reach` / `premium` / `exclusive endpoint` / `upgrade`

路由层 [interface.py:162](../../../tradingagents/dataflows/interface.py) 把 `except AlphaVantageRateLimitError` 改为 `except VendorRateLimitError`。这样任意 vendor 的"限流/受限"都静默跳到下一个源,**不会**被记成 `first_error`(即不污染真正的主源失败信息)。

> 设计取舍:引入 `vendor_errors.VendorRateLimitError` 共享基类,而非在路由层写 `except (A, B)`。理由:为后续接入第 4 个 vendor(Finnhub 等)留出干净扩展点;`AlphaVantageRateLimitError` 保留原名仅改父类,现有导入与测试零影响。

### 3. 防未来函数(look-ahead bias)

FMP 财报端点返回带 `date` 字段的周期列表。`fmp_fundamentals` 按 `date <= curr_date` 过滤,等价于 [alpha_vantage_fundamentals._filter_reports_by_date](../../../tradingagents/dataflows/alpha_vantage_fundamentals.py:4)。`curr_date=None` 时不过滤(与 AV 一致)。

### 4. Symbol 处理

免费档仅美股。FMP 用普通 ticker(`AAPL`)。**不复用** `symbol_utils.normalize_symbol`(那是 Yahoo 的 `BTC-USD`/`EURUSD=X` 约定,对 FMP 错误)。`fmp_common` 只做:`strip()` + 大写 + 去掉券商 `+` 后缀。FMP 返回空列表时抛 `NoMarketDataError(symbol, canonical)`,复用路由的"无数据"汇总路径。

### 5. OHLCV 输出格式一致性

`load_fmp_ohlcv` 返回与 yfinance 清洗后**同列**的 DataFrame(`Date, Open, High, Low, Close, Volume`),从而:
- `fmp_stock.get_stock` 复用与 [get_YFin_data_online](../../../tradingagents/dataflows/y_finance.py:10) 相同的序列化(header 注释 + `Date`-indexed CSV);为避免重复,将该序列化抽成 `ohlcv` 辅助函数共享。
- `fmp_indicator` 直接把该 DataFrame 喂给共享指标逻辑。

### 6. 技术指标(本地 stockstats)

指标窗口逻辑里唯一与 vendor 绑定的是 `load_ohlcv` 那一行;`best_ind_params` 描述、`wrap(df)` 后的 bulk 计算、窗口字符串拼装都是 vendor 无关的。

重构:把这部分抽进 `indicators_common.py`,签名约为:
```
indicator_window_from_frame(ohlcv_df, symbol, indicator, curr_date, look_back_days) -> str
```
- yfinance 的 `get_stock_stats_indicators_window` 改为:`load_ohlcv(...)` 后调用该函数(行为/输出不变)。
- FMP 的 `get_indicator` 改为:`load_fmp_ohlcv(...)` 后调用同一函数。

FMP `get_indicator` 的签名镜像 AV(`symbol, indicator, curr_date, look_back_days, interval="daily", time_period=14, series_type="close"`),仅用前四个参数,其余接受并忽略,以兼容调用方实际传参。

## 数据流

```
toolkit/agent 调 get_xxx
        │
        ▼
interface.route_to_vendor(method, *args)
        │  按 data_vendors/tool_vendors 解析 vendor + fallback 链
        ▼
VENDOR_METHODS[method]["fmp"] ──► fmp_*.py
        │                              │ fmp_common._make_api_request
        │                              ▼
        │                      FMP stable API
        │   限流/Premium → FMPRateLimitError(VendorRateLimitError) → 路由静默跳到 yfinance
        │   空数据         → NoMarketDataError → 路由汇总为 NO_DATA_AVAILABLE 哨兵
        ▼
返回字符串(OHLCV: CSV;基本面/新闻: JSON 字符串;指标: 窗口文本)
```

## 错误处理矩阵

| 情况 | 抛出/返回 | 路由层行为 |
|------|------|------|
| 无 `FMP_API_KEY` | `FMPNotConfiguredError(ValueError)` | 记为 `first_error`,继续下一 vendor |
| 额度耗尽 / Premium 受限 | `FMPRateLimitError(VendorRateLimitError)` | 静默跳到下一 vendor,不污染 first_error |
| 符号无数据/空列表 | `NoMarketDataError` | 记 `last_no_data`,最终输出 `NO_DATA_AVAILABLE` 哨兵 |
| 网络/解析异常 | 原始异常 | 记为 `first_error`,继续下一 vendor |

## 测试策略

新增 `tests/test_fmp_vendor.py`(`@pytest.mark.unit`,全部 mock HTTP,不打真实网络):
1. `_make_api_request` 对 429 / 402 / `"Error Message": limit reach` 正确抛 `FMPRateLimitError`;无 key 抛 `FMPNotConfiguredError`。
2. `get_stock` 把 FMP 历史价 JSON 转成预期 CSV 列;空列表抛 `NoMarketDataError`。
3. 财报 `get_balance_sheet/cashflow/income` 的 `curr_date` 过滤正确剔除未来周期。
4. `get_indicator` 用一段构造 OHLCV 经 `indicators_common` 产出窗口字符串(验证与 yfinance 路径同格式)。
5. **路由集成**:`get_vendor` 配 `fmp`,mock FMP 抛 `FMPRateLimitError`,断言 `route_to_vendor` 透明 fallback 到 yfinance。
6. `symbol` 规整:`aapl+` → `AAPL`。

`indicators_common` 抽取后,跑现有 yfinance 指标相关测试(若有)确保行为不回归。

回归验证命令:
```
cd /Users/liuxuheng/OwnDevWorkspaces/TradingAgentsFork && uv run pytest -q 2>&1 | tail -4
ruff check tradingagents/dataflows/
```

## 非目标(YAGNI)

- 不做 FMP 的 forex/crypto symbol 映射(免费档美股-only)。
- 不接 FMP 原生 technical-indicators 端点(覆盖不全且费额度)。
- 不改任何默认 vendor;不改 agent/toolkit 调用方代码。
- 不为 FMP 加独立磁盘缓存(免费档够用;`load_fmp_ohlcv` 走内存即可,后续需要再说)。

## 实现顺序(供 plan 阶段拆分)

1. `vendor_errors.py` + 让 `AlphaVantageRateLimitError` 继承基类 + 路由层改 `except`(纯重构,先跑通现有测试)
2. `indicators_common.py` 抽取 + yfinance 改为调用它(纯重构,跑通现有测试)
3. `fmp_common.py`(客户端/错误/symbol/JSON 辅助)
4. `fmp_stock.py` + `load_fmp_ohlcv` + OHLCV 序列化共享
5. `fmp_indicator.py`
6. `fmp_fundamentals.py`(含 look-ahead 过滤)
7. `fmp_news.py`
8. `fmp.py` 派发器 + `interface.py` 注册 + `default_config.py` 注释
9. `tests/test_fmp_vendor.py` + 全量回归
