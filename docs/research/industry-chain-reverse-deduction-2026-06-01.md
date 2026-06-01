# 第一性原理·产业链倒推 选股层 — 外部调研 Dossier

> 调研日期:2026-06-01 · 用途:Phase Q 规划会的外部输入(数据可得性 / 现成实现 / 方法论 / 知识图谱 schema)
> 范围:仅外部 web/GitHub 调研,**未触碰任何代码**。结论以"可落地的 fetchable 数据"为第一约束。
> 关联:`R0-two-line-rearch-...-2026-05-24.md`(单一构造点 / PIT 可复现);Phase Q 本地知识图谱(SQLite+NetworkX,双时态+SUPERSEDES);P0-8-amendment(Tushare Pro = 官方 SDK only,严禁 MCP 进运行时数据路径)。

---

## 0. 概念管线与本调研的映射

目标管线(owner 原话):
```
判断未来大方向(宏观/政策/技术拐点)
  → 圈定受益板块
  → 倒推落地所必需的产业链
  → 找"卡脖子"环节(断供→整链巨大负面;且当前未炒热=低关注/低估值但临界重要)
  → 挖该环节代表性上市公司
```

每一步对"可量化、可复现、可审计"的诉求,与本调研的对应:

| 管线步骤 | 可量化抓手 | 数据现状(本调研结论) |
|---|---|---|
| ① 大方向 | 政策事件 / 宏观指标拐点 | **半结构化**:政策走新闻 5 源 + MiroFish 事件;宏观走 Tushare 宏观接口。需 LLM 抽取 evidence,**不入数值订单字段**。 |
| ② 受益板块 | 概念/行业板块映射 | **结构化、fetchable**:Tushare `ths_index`/`dc_index`/`index_classify` + akshare `stock_board_concept_*`/`stock_board_industry_*`。 |
| ③ 倒推产业链 | 上游→下游 有向图 | **半开放**:`liuhuanyong/ChainKnowledgeGraph` 提供 A 股产业链 JSON(6 类关系);商业级在 iFinD/Wind(付费,不可程序化)。需自建+人工校。 |
| ④ 卡脖子环节 | 供应集中度 HHI / 单源依赖 / 国产化率 / 进口依赖度 | **多为非结构化**:在券商研报+政策文(慧博/新浪财经/政策全景图)里,**akshare/Tushare 几乎没有现成字段**。须 LLM 从研报抽取 + 人工 gate。 |
| ⑤ 未炒热 | 估值分位 + 关注度(换手/龙虎榜)+ 板块涨幅排名 | **结构化、fetchable**:Tushare `daily_basic`(pe/pb/ps/total_mv/turnover)历史回算分位;龙虎榜 akshare `stock_*_lhb_*`;板块涨幅 `dc_index`/板块行情。 |
| ⑥ 代表公司 | 板块成分 ∩ 产业链环节成分 | **结构化、fetchable**:`ths_member`/`index_member_all`/`stock_board_concept_cons_em`。 |

**一句话判断**:管线的**两端(②⑤⑥)数据扎实、可程序化复现**;**中间(③④)= 本项目最难、最稀缺的部分**,没有干净的开放结构化源,必须靠"半开放图谱 + 研报/政策 LLM 抽取 + 人工 gate"。这与项目"⑤红线:LLM 4 可写文本字段、永不进数值订单字段"和"Phase Q 人工 gate"天然契合。

---

## 1. 数据可得性矩阵(最重要)

### 1.1 Tushare Pro — 概念/行业/产业链(官方 Python SDK `ts.pro_api`)

| 维度 | 接口名 | 关键 输入 / 输出 | 积分门槛 | 备注 |
|---|---|---|---|---|
| 申万行业分类 L1/L2/L3 | **`index_classify`** | in:`level`(L1/L2/L3)、`parent_code`、`src`(SW2014/SW2021);out:`index_code,industry_name,parent_code,level,industry_code,is_pub,src` | 2000 | SW2021=31/134/346;SW2014=28/104/227。`is_pub` 标识成分<5 不发布。**树形父子关系 = 天然 KG 边来源**。 |
| 申万行业成分(分级) | **`index_member_all`** | 按 L3 取成分,或反查某 code 的分级归属 | 2000 | 个股→行业三级归属;doc_id=335。 |
| 申万行业指数日行情 | `sw_daily` / 指数日行情 | 板块层面涨幅/估值序列 | 2000 | doc_id=327。板块涨幅排名用。 |
| 同花顺概念&行业指数 | **`ths_index`** | in:`ts_code`、`exchange`(A/HK/US)、`type`(N概念/I行业/R地域/S特色/ST风格/TH主题/BB宽基);out:`ts_code,name,count,exchange,list_date,type` | 6000 | **概念/主题/行业 一张表**;`type=TH` 主题、`type=N` 概念。最贴"受益板块"。 |
| 同花顺概念成分 | **`ths_member`** | 概念指数→成分股 | 6000 | doc_id=261/259 体系。成分=KG"标的"边。 |
| 同花顺板块日行情 | `ths_daily` | 板块日线 | 6000 | 板块涨幅/拥挤度序列。 |
| 东方财富概念板块 | **`dc_index`** | in:`idx_type`(industry/concept/region,必填)、`name`、`trade_date`;out:`ts_code,name,leading,leading_code,pct_change,leading_pct,total_mv,turnover_rate,up_num,down_num,level` | — | doc_id=362。**直接给 板块涨幅排名 + 换手 + 涨跌家数**(炒热度抓手)。 |
| 通达信板块 | 通达信板块信息 | 另一套板块映射 | — | doc_id=376。交叉验证用。 |
| **个股每日基本面(估值)** | **`daily_basic`** | in:`ts_code`、`trade_date`;out:`turnover_rate,turnover_rate_f,volume_ratio,pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,dv_ttm,total_share,float_share,free_share,total_mv,circ_mv` | — | doc_id=32。**估值分位 / 换手率 / 量比 全部历史可回算**。"未炒热"核心源。 |

> **产业链上下游关系**:Tushare **没有**专门的"供应商-客户"或"上游材料-下游产品"有向关系接口。最接近的是申万/同花顺/东财的**层级板块树**(行业树父子=粗粒度上下游近似),以及个股的**主营构成**(下见 akshare `stock_zygc_em` 同源)。真·细粒度产业链需外部图谱(§1.3)。

### 1.2 akshare — 概念/行业/资金流/估值/关注度(东财/同花顺/新浪)

| 维度 | 函数名 | 用途 |
|---|---|---|
| 概念板块行情 | `stock_board_concept_name_em` | 东财全部概念板块(名称/代码 BKxxxx/涨跌/换手)。**注意:曾有 issue #5811 只返回 200 条,须核对当前版本分页**。 |
| 概念板块成分 | `stock_board_concept_cons_em` | 概念→成分股(支持名称或 BK 代码,1.16.23+)。 |
| 行业板块行情 | `stock_board_industry_name_em` | 东财行业板块。 |
| 行业板块成分 | `stock_board_industry_cons_em` | 行业→成分股。 |
| 板块资金流排名 | `stock_sector_fund_flow_rank` | **板块资金流排名 = 炒热度/资金拥挤抓手**。 |
| 行业资金流(个股) | `stock_sector_fund_flow_summary` | 某行业内个股资金流。 |
| 行业历史资金流 | `stock_sector_fund_flow_hist` | 行业资金流时序。 |
| 概念历史资金流 | `stock_concept_fund_flow_hist` | 概念资金流时序(拥挤度时序)。 |
| 个股资金流 | `stock_individual_fund_flow` | 个股层面。 |
| PE/PB(全市场) | `stock_a_pe` / `stock_a_pb` / `stock_a_pe_and_pb` / `stock_a_lg_indicator` | 估值;`stock_a_lg_indicator` 含个股 PE/PB/股息率序列。 |
| 龙虎榜 | `stock_sina_lhb_detail_daily` / `stock_sina_lhb_ggtj`(个股上榜统计)/ `stock_sina_lhb_jgzz`(机构席位)/ `stock_sina_lhb_jgmx` | **关注度/游资介入度抓手**。 |
| 主营构成 | **`stock_zygc_em`**(主营构成-东财) | 个股主营产品/地区构成 → **可作产业链环节归属的弱信号**(产品关键词 → 产业链节点匹配)。 |

> akshare 与 Tushare 同样:**没有现成"供应商/客户/大客户/上下游"company-to-company 关系接口**。`stock_zygc_em` 主营构成是最接近的"公司→产品"映射,可与产业链图谱的"产品"节点做关键词链接(正是 ChainKnowledgeGraph 的建图思路)。

### 1.3 结构化产业链/供应链 上下游图谱数据源 — 现状

| 源 | 性质 | 上下游关系? | 程序化可得? | 结论 |
|---|---|---|---|---|
| **`liuhuanyong/ChainKnowledgeGraph`**(GitHub) | **开放 JSON 数据集** | ✅ 产品上游原材料 56,824 + 下游产品 390 + 公司主营产品 + 公司所属行业 3,946 + 行业上级 480 + 产品小类 52,937 | ✅ repo 内含 7 个 JSON(company/industry/product + 关系文件) | **唯一开放、可直接 port 的 A 股产业链图谱底料**。4,654 家上市公司、511 行业、95,559 产品。无 license(见 §5 风险)。 |
| 同花顺 iFinD 产业洞察 | 商业终端 | ✅ 100+ 精选产业链、2 万+ 标准词、覆盖全部申万一级 | ❌ 终端/Excel 插件,**无开放程序化 API**(iFinD 数据接口需付费授权,且不属 Tushare 官方 SDK 路径) | 人工参考/校准用,**不可进运行时数据路径**。 |
| Wind 产业链与供应链数据库 | 商业 | ✅ 主营标准化 + 公告披露的客户/供应商全量 | ❌ 付费终端 | 同上,人工参考。 |
| Tushare/akshare | 开放 | ❌(仅行业树 + 主营构成弱信号) | ✅ | 不提供 company-to-company 供应链边。 |

**判断**:**A 股没有开放、干净、即取即用的"公司级供应商-客户有向图"结构化源**。可行路线 = `ChainKnowledgeGraph`(产品级上下游)作冷启动底料 + 申万/同花顺行业树(粗粒度链) + `stock_zygc_em` 主营构成(公司→产品链接) + 研报/公告 LLM 抽取增量 + **人工 gate 校准**(契合 Phase Q + 红线⑤)。

### 1.4 "卡脖子"量化数据 — 最稀缺

| 指标 | 含义 | akshare/Tushare 有? | 现实来源 |
|---|---|---|---|
| 供应集中度 HHI | 某环节供给者市占平方和 | ❌ 无现成字段 | 须自算(需各供应商产能/市占,多在研报) |
| 单源依赖 / single-supplier risk | 关键件唯一/寡头供给 | ❌ | 研报 + 公告 + 新闻 |
| 国产化率 | 国产份额 % | ❌(无接口) | **券商研报/政策文**(如半导体设备国产化率 35%、静电吸盘<1% 等数字均来自研报,非 API) |
| 进口依赖度 | 进口/总需求 | ⚠️ 间接 | 海关总署/Tushare 宏观进出口(宏观层,非环节级) |

**判断**:**"卡脖子"几乎全是非结构化情报**(慧博/新浪财经/政策"10 大产业 41 类卡脖子全景图"等)。这一步**无法纯靠 API 复现**,必须:
- LLM 从研报/政策/新闻抽取 → 写 `evidence_collection.content`(`NEWS-`/`MIROFISH-` 前缀,**display-only,不入 RiskCheckSummary、不入数值订单字段**);
- "criticality / 卡脖子度" 作为**人工维护 + 双时态可 SUPERSEDES 的图谱节点属性**,人工 gate 批准,不让 LLM 自动写决策。

### 1.5 "未炒热"量化 — 数据扎实

| 信号 | 实现 | 源 |
|---|---|---|
| 估值分位(低=未炒) | `daily_basic` 取 pe_ttm/pb 历史序列,算当前值在 N 年窗口的 percentile | Tushare(PIT-safe:用 `trade_date` 切片) |
| 关注度(低=未炒) | 换手率 `turnover_rate`、量比 `volume_ratio`(daily_basic);龙虎榜上榜频次(akshare lhb);概念资金流净额(`stock_concept_fund_flow_hist`) | Tushare + akshare |
| 板块涨幅排名(低=未炒) | `dc_index` pct_change 横截面排名;板块资金流排名 `stock_sector_fund_flow_rank` | Tushare/akshare |
| 搜索热度 | ❌ 无开放 A 股精确源(百度指数需爬,微博/雪球非结构化) | 不建议进运行时;可作人工辅助 |

---

## 2. 现成实现(GitHub)

| Repo | Stars/License | 是什么 | 可移植性 |
|---|---|---|---|
| **`liuhuanyong/ChainKnowledgeGraph`** | 无显式 license(见 §5) | **A 股产业链知识图谱**:3 类实体(公司/行业/产品)+ 6 类关系(公司所属行业、行业上级、产品上游材料、产品下游产品、公司主营产品、产品小类);7 个 JSON 数据文件 + `build_graph.py`。4654 公司/511 行业/95559 产品/56824 上游材料边。源=申万 swsindex + 上交所 + 深交所公告。 | ★★★★★ **数据可直接 port**(JSON→SQLite+NetworkX 节点边)。建图脚本写 Neo4j,但**数据与图无关**,可只取 JSON 喂 Phase Q 的 NetworkX。**license 缺失=只能内部参考/重建,不可直接再分发,建议人工复核+amendment 记录来源**。 |
| **`shiviancodes/nexus`** | MIT | 从 SEC filings 建**供应链知识图谱**(10-K 供应商/客户 1659 边、13F 持仓、DEF14A 董事联结)+ **5-agent AI debate**(NetworkAnalyst/EventMechanic/ContagionMonitor + RedTeam 挑战 + 综合 conviction)路由 8-K 事件交易。存 TimescaleDB(关系存储 + 自定义图遍历,非图库)。 | ★★★★ **架构镜像 QuantMind**(多 agent 辩论 + kill switch + PIT `assert_point_in_time_safe()` + HLZ 多重检验校正 M=400)。**美股+SEC,数据不可用**;但**方法论高度可借**:choke-point 信号发现的多重假设检验防过拟合、PIT 守门、null result 透明记录。 |
| `Jew-011/AI-Quant-Assistant-for-ETF-Rotation-Strategies` | (待核) | **LangGraph 多 agent 辩论 + 真实资金流因子 + decision-trace audit** 的 ETF 板块轮动研究助手,双语 Streamlit。 | ★★★ 与项目 LangGraph 编排 + 审计 + 板块轮动思路同构,可借编排/审计结构;非 A 股个股选股。 |
| `garroshub/Quant_Sector_Rotation_Strategy` | (待核) | 动量+波动率 ETF 板块轮动 + LLM 策略分析。 | ★★ 板块轮动信号模板。 |
| `Weizhi-Zhao-quant/Quantitative-Research-Projects` | (待核) | 板块轮动 + 多因子 + ML,含完整代码。 | ★★ 因子/轮动代码参考。 |
| `chriswangweb/KGData` | (待核) | 多行业知识图谱(含产业链/投资)关系抽取 + 数据清洗。 | ★★ 关系抽取 pipeline 参考(中文)。 |
| `crifan/ic_chip_industry_chain_summary` | (待核) | 芯片产业链总结(领域文本)。 | ★ 半导体链领域知识冷启动语料。 |
| `simonlin1212/a-stock-data` | (待核) | A 股全栈数据工具包(7 层/28 端点/13 源/零三方依赖)。 | ★★ 数据接入封装参考(但项目已锁 Tushare SDK + akshare 主备,慎引入新源破红线)。 |

> 通用"supply-chain knowledge graph"repo 多为 Neo4j+GDS(`vpakspace/supply-chain-kg` 做 centrality/最短路;`123qsa/supply-chain-kg` 中文产业链+Neo4j+LLM 双层)。**centrality/最短路算法可借,但 NetworkX 已原生支持**(degree/betweenness/PageRank),无需引第三方图库 — 与 Phase Q 选型(SQLite+NetworkX)一致。

---

## 3. 方法论(学术/业界)

### 3.1 自顶向下 主题/板块轮动 的可复现化 + 可审计化
- **板块轮动 = 收益 + "愿意付多少"(估值)双因子**:领导板块拥挤→估值拉伸→下一桶金外流(MSCI / Zorroh)。可复现做法 = 因子模型 + 基本面(arXiv 2401.00001《Sector Rotation by Factor Model and Fundamental Analysis》;宏观因子轮动 WUSTL Rotation.pdf 用宏观状态聚类切板块)。
- **可审计 = 信号 → 决策 全链留痕**:nexus 的 decision-trace + conviction;Jew-011 的 decision-trace audit。与 QuantMind 三层 reason 抽屉 + audit 34 类一致 → **每个"倒推"步骤(大方向/受益板块/卡脖子环节/标的)都应落成可回看的 evidence + provenance**。

### 3.2 "卡脖子/choke-point"环节的量化
- **供应集中度 HHI**:Σ(市占)²;>0.25 高度集中。**数据缺口**:环节级供给者市占 A 股无 API,须研报抽取。
- **供应链网络中心性**(关键节点识别)— 强学术支撑:
  - Cohen & Frazzini《Economic Links and Predictable Returns》:客户动量沿供应链传导;
  - Wu & Birge《Supply Chain Network Structure and Firm Returns》(SSRN 2385217):供应商动量 long-short 月度超额 ~56 bp;一阶(直连)+ 二阶(系统暴露);
  - Ahern《Network Centrality and the Cross Section of Stock Returns》:**中心性高的节点 = 风险/收益更高** → 对应"断供→整链巨大负面"的网络脆弱性;
  - FactSet:**edge betweenness centrality** 调节客户→供应商传导强度。
  - **映射**:choke-point ≈ **高 betweenness / 高 out-degree(被很多下游依赖)且替代少** 的产业链节点。NetworkX 可直接算 betweenness/degree/PageRank → 作"criticality"代理。
- **库存周期 / 单源风险**:库存周期看宏观+行业景气(Tushare 宏观);single-supplier 须公告/研报。

### 3.3 "临界重要但未被持有(critical-but-under-owned)"打分
组合三因子(全部可在 §1.5 + §3.2 量化或半量化):
```
score = w1·criticality(网络中心性/卡脖子度, 高好)
      × w2·(1 − crowding)(资金流/换手/龙虎榜分位, 低好)
      × w3·(1 − valuation_percentile)(pe/pb 历史分位, 低好)
```
- **crowding 度量**:用历史分位阈值判板块/个股是否拥挤(arXiv 2001.04185《Zooming In on Equity Factor Crowding》:用交易失衡波动 + 历史 quantile 阈值)。**项目可直接用 概念资金流净额分位 + 换手率分位 + 龙虎榜上榜频次** 作 crowding 代理。
- **criticality**:网络中心性(可算)× 卡脖子度(研报抽取 + 人工 gate)。
- **valuation_percentile**:`daily_basic` pe_ttm/pb 滚动窗 percentile(PIT-safe)。
- 三者乘积 = "高临界 × 低拥挤 × 低估值" = owner 想要的"还没炒热但临界重要"。**注意防伪信号**:低估值可能是价值陷阱、低关注可能是基本面差;须叠加基本面过滤 + 人工 gate。

---

## 4. 知识图谱 schema 草图(Phase Q:SQLite + NetworkX,双时态 + SUPERSEDES)

产业链天然是有向图,贴合 Phase Q。建议 schema:

**节点(node types)**
| type | 关键属性 | 来源 |
|---|---|---|
| `Trend`(大方向) | name, horizon, policy_refs[], confidence | 政策/MiroFish 事件(LLM 抽取 + 人工 gate) |
| `Sector`(板块/概念) | name, src(SW/THS/DC), code, level(L1/L2/L3) | `index_classify`/`ths_index`/`dc_index` |
| `ChainLink`(产业链环节) | name, layer(上游/中游/下游), **criticality**, **chokepoint_score**(betweenness/单源/国产化率), substitution_difficulty | ChainKnowledgeGraph 产品/材料 + 研报抽取 |
| `Product`(产品/材料) | name, category | ChainKnowledgeGraph product/小类 |
| `Stock`(标的) | ts_code, name, **crowding_pct**, **valuation_pct**, board | `daily_basic` + 板块成分 + 资金流 |

**边(edge types,有向)**
| edge | from→to | 属性 | 来源 |
|---|---|---|---|
| `DRIVES` | Trend→Sector | weight, evidence_id | LLM+人工 |
| `REQUIRES` | Sector→ChainLink | necessity | 倒推(人工/研报) |
| `UPSTREAM_OF` | ChainLink→ChainLink | (产品上游材料 56824 边底料) | ChainKnowledgeGraph |
| `SUPPLIES_PRODUCT` | Stock→Product | revenue_share | `stock_zygc_em` 主营构成 |
| `BELONGS_TO` | Stock→Sector | — | 板块成分接口 |
| `MEMBER_OF` | Product→ChainLink | — | 映射 |

**双时态 + SUPERSEDES**(沿用 Phase Q 既定):每条边/节点带 `valid_from/valid_to`(业务时间)+ `recorded_at`(系统时间);criticality/chokepoint_score 随研报更新走 `SUPERSEDES` 新版本而非原地改(契合全局不可变红线 + R0 PIT 可复现)。

**算法层**:NetworkX 上对 `UPSTREAM_OF` 子图算 betweenness/out-degree/PageRank → `chokepoint_score`;无需 Neo4j/图库。

**参考**:FinCaKG-Onto(因果 KG + FIBO 本体,边权=因果共现频次)可借"边带强度权重 + 本体对齐"思路给 `DRIVES` 边赋因果强度;nexus 的 typed-edge + PIT 守门给工程模板。

---

## 5. 诚实的"什么难 / 什么拿不到"

1. **company-to-company 供应链有向图:A 股无开放结构化源**。Tushare/akshare 都没有"供应商-客户"边。最现实 = ChainKnowledgeGraph 产品级上游材料 + 主营构成弱链接,**精度有限、需人工校**;商业级(Wind/iFinD)有但**不可程序化、不可进运行时**(且破 Tushare-SDK-only 红线)。
2. **"卡脖子"指标(HHI/国产化率/进口依赖/单源)基本无 API**。全在研报/政策文,**只能 LLM 抽取 + 人工 gate**,精度与时效靠人维护。这是管线第④步的根本瓶颈 — **不要假装能纯量化复现**。
3. **ChainKnowledgeGraph 无 license**:可内部参考/重建底料,**不可直接再分发**;建议落 amendment 记录数据来源与版权状态,并以"冷启动种子 + 自建增量"定位。
4. **akshare 概念接口稳定性**:`stock_board_concept_name_em` 历史有"只返 200 条"issue(#5811),板块全集抓取须核对版本+分页;akshare 接口随上游网页改版易碎(项目已有主备认知)。
5. **"搜索热度"无干净 A 股开放源**:百度指数/雪球需爬,非结构化、易反爬,**不建议进运行时数据路径**,至多人工辅助。
6. **"大方向判断"不可自动化下决策**:政策/技术拐点是 LLM evidence(display-only),**红线⑤:永不进数值订单字段**;倒推结论须经人工 gate + Phase Q 双时态留痕,**不能让 LLM 直接给方向决策**。
7. **价值陷阱/僵尸股风险**:"低估值×低关注"可能是基本面恶化而非"未炒热",纯三因子乘积会捞垃圾;须叠加基本面/景气过滤 + 人工复核。
8. **过拟合**:choke-point 信号样本少、研报驱动主观,易过拟合。借 nexus 的 **HLZ 多重检验校正 + PIT 守门 + null result 透明**,别让"卡脖子叙事"绕过统计纪律。

---

## 6. 给 Phase Q 规划会的可落地建议(浓缩)

- **能纯量化复现的**:②受益板块映射、⑤未炒热(估值分位+换手+资金流+板块涨幅排名)、⑥标的成分 → 直接用 Tushare `daily_basic`/`index_classify`/`ths_index`/`ths_member`/`dc_index` + akshare 资金流/龙虎榜,PIT-safe 切片。
- **半结构化、需图谱+抽取的**:③产业链 → port `ChainKnowledgeGraph` JSON 作 NetworkX 冷启动底料 + 主营构成链接;④卡脖子 → 研报/政策 LLM 抽取写 evidence + 人工 gate 维护 criticality/chokepoint_score(双时态+SUPERSEDES)。
- **打分**:`criticality × (1−crowding) × (1−valuation_pct)`,NetworkX betweenness 作 criticality 量化代理,加基本面过滤防价值陷阱。
- **守红线**:LLM 只写 4 文本字段、卡脖子叙事 display-only 不进订单;方向/criticality 走人工 gate;不引新数据源破 Tushare-SDK-only;NetworkX 足够,不引 Neo4j。

---

## Sources
- Tushare 申万行业分类 `index_classify`: https://tushare.pro/document/2?doc_id=181
- Tushare 申万行业成分 `index_member_all`: https://tushare.pro/document/2?doc_id=335
- Tushare 申万行业指数日行情: https://tushare.pro/document/2?doc_id=327
- Tushare 同花顺概念和行业指数 `ths_index`: https://tushare.pro/document/2?doc_id=259
- Tushare 同花顺行业概念成分 `ths_member`: https://tushare.pro/document/2?doc_id=261
- Tushare 东方财富概念板块 `dc_index`: https://tushare.pro/document/2?doc_id=362
- Tushare 通达信板块信息: https://tushare.pro/document/2?doc_id=376
- Tushare 每日指标 `daily_basic`: https://tushare.pro/document/2?doc_id=32
- akshare 股票数据字典: https://akshare.akfamily.xyz/data/stock/stock.html
- akshare 概念板块 issue #5811: https://github.com/akfamily/akshare/issues/5811
- liuhuanyong/ChainKnowledgeGraph: https://github.com/liuhuanyong/ChainKnowledgeGraph
- shiviancodes/nexus: https://github.com/shiviancodes/nexus
- Jew-011/AI-Quant-Assistant-for-ETF-Rotation-Strategies: https://github.com/Jew-011/AI-Quant-Assistant-for-ETF-Rotation-Strategies
- garroshub/Quant_Sector_Rotation_Strategy: https://github.com/garroshub/Quant_Sector_Rotation_Strategy
- 同花顺 iFinD 产业洞察: http://www.aifind.com/
- Wind 产业链与供应链数据库: https://www.modb.pro/db/481844
- Cohen & Frazzini, Economic Links and Predictable Returns (customer momentum)
- Wu & Birge, Supply Chain Network Structure and Firm Returns: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2385217
- Ahern, Network Centrality and the Cross Section of Stock Returns: https://marriott.byu.edu/upload/series/1/docs/45835.pdf
- FactSet, Supply Chain Signals (edge betweenness centrality): https://insight.factset.com/supply-chain-signals-enhancing-the-customer-momentum-strategy-with-network-centrality
- Sector Rotation by Factor Model and Fundamental Analysis (arXiv 2401.00001): https://arxiv.org/pdf/2401.00001
- Zooming In on Equity Factor Crowding (arXiv 2001.04185): https://arxiv.org/pdf/2001.04185
- Macroeconomic Factors for Sector Rotation (WUSTL): https://www.cse.wustl.edu/~yixin.chen/public/Rotation.pdf
- FinCaKG-Onto (causality KG + FIBO ontology): https://link.springer.com/article/10.1007/s10489-025-06247-1
- 半导体设备国产替代深度(慧博/知乎): https://zhuanlan.zhihu.com/p/711137905
- 10 大产业 41 类卡脖子技术国产替代全景图: https://www.hvchan.com/news/view.html?id=574
