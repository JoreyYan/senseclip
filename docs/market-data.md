# 宏观与市场数据采集

引擎服务(`SERVICE_MODE=engine`)启动后常驻 `MarketCollector`,从官方免费接口定时拉取数据,
全部**无需 API 密钥**。代码:`apps/api/api/market_data.py`;表结构:`supabase/migrations/20260918000000_market_data.sql`。

按「四层」组织:判断经济大趋势看第一层,判断利率与配置看第二层,判断短期方向看第三层,判断系统性风险看第四层。

## 数据源

| 源 | 接口 | 内容 | 检查间隔 |
|---|---|---|---|
| FRED(圣路易斯联储) | `fred.stlouisfed.org/graph/fredgraph.csv?id=<序列>` | 就业、通胀、零售、美债收益率、期限溢价、信用利差、油价、美元 | 4 小时 |
| 美国财政部 Fiscal Data | `api.fiscaldata.treasury.gov/.../auctions_query` | 国债拍卖日历(已公布)与结果 | 3 小时 |
| CFTC | `publicreporting.cftc.gov/resource/gpe5-46if.json` | 金融期货持仓(Traders in Financial Futures) | 12 小时 |
| CBOE | `cdn.cboe.com/api/global/us_indices/daily_prices/*_History.csv` | VIX、SKEW | 6 小时 |

数据本身是日更、周更或月更,检查间隔只决定多快发现新值。每次只写回看窗口内的点,吸收官方修订;
某个源失败不影响其他源,下个周期自动重试。

## 指标映射

### 第一层:硬数据

| 指标 | 序列 | 优先级 | 读法 |
|---|---|---|---|
| 非农就业 | `PAYEMS` | 必看 | 取环比增加(千人) |
| 平均时薪 | `CES0500000003` | 必看 | 取同比 |
| 初请失业金 | `ICSA`(周四) | 必看 | 周频,最早的劳动力恶化信号 |
| JOLTS 职位空缺 | `JTSJOL` | 必看 | 领先非农 |
| ADP 就业 | `ADPMNUSNERSA` | 辅助 | 取环比增加 |
| 失业率 | `UNRATE` | 辅助 | 与非农冲突时以非农为准 |
| PCE / 核心 PCE | `PCEPI` / `PCEPILFE` | 必看 | 取同比,联储政策锚 |
| CPI / 核心 CPI | `CPIAUCSL` / `CPILFESL` | 必看 | 取同比 |
| 零售销售 | `RSAFS` | 必看 | 取环比 |
| 实际 GDP | `A191RL1Q225SBEA` | 季度复盘 | 环比折年率 |
| 消费者信心(软数据) | `UMCSENT` | 辅助 | 只影响短期情绪 |

### 第二层:利率与国债

| 指标 | 来源 | 优先级 |
|---|---|---|
| 10 年 / 2 年 / 5 年 / 30 年美债收益率 | `DGS10` `DGS2` `DGS5` `DGS30` | 必看 |
| 10年-2年利差 | `T10Y2Y` | 必看 |
| 期限溢价 | `THREEFYTP10`(Kim-Wright 模型) | 必看 |
| 国债拍卖:发行量、中标利率、认购倍数、间接投标占比 | 表 `treasury_auctions` | 必看 |
| 未来 30 天已公布的附息债供给 | 快照 `treasury.coupon_supply_next_30d` | 必看 |
| 联邦基金利率 | `DFF` | 辅助 |
| 月度财政赤字 | `MTSDS133FMS` | 季度复盘 |

拍卖日历只包含财政部已正式公布的场次,通常提前约一周公布。

### 第三层:仓位与波动

| 指标 | 来源 | 说明 |
|---|---|---|
| VIX | `VIX_CBOE`(`VIXCLS` 备份) | 恐慌温度计 |
| SKEW | `SKEW_CBOE` | 尾部风险定价 |
| 期货净持仓 | `COT_<合约>_<交易者>_NET`,表 `cot_positions` | 合约:ES 标普、NQ 纳指、TY 10 年美债、TU 2 年美债、VX VIX;交易者:杠杆基金、资产管理人、交易商、其他 |

### 第四层:信用与跨市场

| 指标 | 序列 | 说明 |
|---|---|---|
| 高收益债信用利差 | `BAMLH0A0HYM2` | CDS 的公开替代 |
| 投资级信用利差 | `BAMLC0A0CM` | |
| 原油 | `DCOILWTICO` `DCOILBRENTEU` | |
| 美元 | `DTWEXBGS` | 联储广义美元指数,DXY 的公开替代 |

## 拿不到的数据

| 指标 | 原因 | 替代 |
|---|---|---|
| CTA 仓位 | 投行模型估算 | VIX、CFTC 杠杆基金净持仓 |
| Gamma 头寸 | 需期权持仓明细,付费 | VIX、SKEW |
| 盘口深度 | 交易所逐笔数据,付费 | 无 |
| Prime Broker 对冲基金仓位 | 投行内部 | CFTC 杠杆基金净持仓(周频) |
| 杠杆 ETF 资金流 | 无稳定免费源 | 暂无 |
| 公司回购窗口与执行 | 无结构化免费源 | 暂无 |
| CDS | 付费 | 高收益债 / 投资级信用利差 |
| ISM / PMI | 授权数据 | 未采集 |
| 对冲基金 13F | SEC EDGAR 可取,尚未实现 | 规划中 |

## 接口

| 接口 | 说明 |
|---|---|
| `GET /api/market/snapshot` | 按四层分组的最新值,含环比/同比派生值、拍卖日历与近期结果、不可得清单(10 分钟缓存) |
| `GET /api/market/series/{id}?since=2020-01-01` | 单个序列的历史观测值 |
| `GET /api/admin/market/status` | 采集器与每个序列的健康状态(需 `X-Admin-Key`) |
| `POST /api/admin/market/run {"source": "fred"}` | 手动触发一次采集,`source` 为空则全部 |

环境变量:`MARKET_COLLECTOR_ENABLED`(默认 `true`)、`MARKET_HISTORY_START`(首次回填起点,默认 `2000-01-01`)。
