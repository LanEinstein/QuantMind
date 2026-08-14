# M2 阶段 C batch-044 中途接手说明：observation/bundle/case/event 已写，hypothesis revision 与封板未做

> 日期：2026-08-14
>
> 工作目录：`/home/ps/papers/QuantMind`
>
> 分支：`agent/m2-evidence-reconstruction`
>
> 唯一恢复点：**`M2-C-batch-044`（固定序号 1076—1088）的 13 份 observation（含 1083 unavailable 最小记录）、7 对 bundle、3 个 case、3 个 event 已全部写盘并通过校验；10 条 hypothesis revision 与封板（含 worklog 追加）未做。**

本文件接续 `M2-phase-C-batch-043-midway-handoff-2026-08-14.md`。总目标不变：完成 batch-044 封板后，追加 1001—1088 综合与全语料冲突审查，最终 worklog 写 `resume_from=M2-C-complete-awaiting-G2-owner-review`，只宣布"可提交 G2 owner 审查"。

## 1. 已完成且不要重做

### batch-043 已封板（本 session 完成）

`data/yeren_research/worklog.jsonl` 最后一条：`work_unit=M2-C-batch-043`，`status=completed`，`resume_from=M2-C-batch-044`。封板时计数：observation 工件 1099、唯一 aweme 1075、hypothesis 331、bundle 518、case 119、event 87；25/25 schema + 146 span 逐字校验通过；pytest 20 passed。

batch-043 关键校正已入工件：量能链条六日自洽（-5488/+2173/+4515/-1319/+1354/-1444 亿，五日口播量级吻合）；三档数值化（2500—3000/3500/5000）与形容词口径冲突（8-07 +1354 亿被称"温和放量"低于自设下限）；仓位口径四段漂移（打满→一成+九成→"原话八成"→三至五成→八成）；周一预期相继冲突（1067 延续修复 vs 1071 回踩需求，8-10 普涨使前者命中后者反证，并列封存）；寒武纪财报事实层六数字与 PIT 全吻合（事件 `cambricon-h1-2026-report-2026-08-08.json`）；宇树 IPO 口播（8-10 申购、二百多 PE）与官方发行公告全项吻合（219.23 倍，事件 `unitree-ipo-subscription-2026-08-10.json`），破发预测待上市日。

### batch-044 已写盘并通过校验（本 session 完成，**hypothesis revision 与封板未做**）

- **observation 13/13**（1076—1088；1083 `7672964034287880290` 为首个 `transcript_status=unavailable` 最小记录，零 evidence 零 statement，ledger 终态"作品已删除、隐藏或当前不可见"），Pydantic + 65 span 逐字逐时戳校验通过。当前计数：observation 工件 1112、唯一 aweme 1088（全语料 observation 覆盖完毕）。
- **bundle 7 对**（全部 cutoff 隔离校验通过，bundle 总数 532）：
  - `batch044-aug10-key-node-4000-toll-feedback-2026-08-11`（1076，cutoff 8-10 16:46）；
  - `batch044-aug10-storage-catchup-feedback-2026-08-11`（1077，cutoff 8-10 22:54）；
  - `batch044-aug11-intraday-4000-gate-feedback-2026-08-11`（1080，盘中 cutoff 13:22，当日日线属 outcome）；
  - `batch044-aug11-benign-dip-repair-feedback-2026-08-12`（1081，cutoff 8-11 15:15）；
  - `batch044-aug12-no-volume-branch-feedback-2026-08-12`（1084，盘中 cutoff 11:40）；
  - `batch044-aug12-full-position-4000-watch-feedback-2026-08-13`（1085+1086，cutoff 8-12 16:32）；
  - `batch044-cpi-pboc-finance-gapup-feedback-2026-08-13`（1087+1088，cutoff 8-12 20:45）。
- **case 3 个**（case 总数 122）：`batch044-4000-toll-gate-and-volume-gate-chain-2026-08-10-to-2026-08-13`、`batch044-position-climax-profit-cushion-and-locked-exit-2026-08-10-to-2026-08-12`、`batch044-hightech-rebound-end-and-event-avoidance-2026-08-10-to-2026-08-12`。
- **event 3 个**（event 总数 90，全部经官方/公开来源核对）：
  - `cz7a-zhongxing4b-launch-failure-2026-08-10.json`（长征七号改/中星4B，8-10 20:02 发射失利，新华社通报；失利火箭为国家队型号，回避 vindication 只到题材情绪层）；
  - `us-cpi-july-2026-in-line-2026-08-12.json`（7月 CPI 同比 3.4%/核心 2.5% 全部符合预期；1087 数据前判断与 1088 发布后 15 分钟确认两步命中）；
  - `pboc-q2-monetary-policy-report-2026-08-12.json`（央行二季度货政报告 8-12 晚发布，适度宽松+逆周期加力；"放水前奏、降准降息一至两个月"为作者解读层，窗口超出 PIT 覆盖保持开放）。

### batch-044 已读 12 条的核心判断（复核用摘要；工件本身是权威）

1. **1076**（8-10 收盘后期指）：信爷加六千多手多单，纯做多 vs 对冲不可识别；4000 点收费站叙事（过一下扣两百）；变盘后四天小阳中阳线（自我纠正口径）；明天向上攻须大科技+大金融同时发力缺一不可；八成多仓偏防守。
2. **1077**（8-10 晚加更）：高位科技反弹进度表（CPO 约两成、PCB 两三成、光纤已过、半导体存储未反弹）；存储补涨需求（等外围企稳入场）；4000 点需量能+题材双条件；**利润垫逻辑首次显式化**（七月少亏、八月回本浮盈，大不了利润回掉）；先手叙事（T+1）。
3. **1078**（8-11 00:05）：火箭炸了评论区转述+回避 vindication 修辞；恐慌盘出来则商业航天阶段性见底（条件分支）。
4. **1079**（8-11 午间）：存储补涨吃到肉邀功、余温尾声预告；缩量想摸 4000 不现实、下午放量可能性非常小；"平量震荡"定性（全日实际缩 -2027 亿，判断口径不准按数值结算）；手上不乱动。
5. **1080**（8-11 午后 20 秒）：机器人异动堆量（宇树申购次日事件窗口）；大金融异动；不放量冲高回落切记。
6. **1081**（8-11 收盘）：**缩量两千亿口播与 PIT -2027 亿精确吻合**；良性回踩→明天修复预期（8-12 4128/1280 命中）；大概率肯定去摸四千点（人性博弈：不摸怎么套人扣费）；盘面乱利好抱团；MLCC=PCB 细分太小不扎堆、快到双头做减法；保持原始仓位。
7. **1082**（8-11 晚）：卖点教学（系统未稳定前固定卖点，如到了 30% 无条件减仓 30% 或一半——基准未明）；**大科技反弹接近尾声、以月为单位调整**；存储补涨周期结束；不要想卖到最高点；责任切割（卖飞怪我/讲错别怪）。
8. **1083**：unavailable，最小记录。
9. **1084**（8-12 盘中）：缩量太厉害、修复太弱；下午不放量则冲高回落（当日缩量 -1690 亿但广度强 4128/1280，门禁分支首个反例日）；锁仓叙事第三日复述；按系统执行 vs 赌博二分；75a 开始有溢价邀功。
10. **1085**（8-12 收盘，很重要）：缩量一千七百亿弱修复与昨预期一致（PIT -1690 亿精确吻合）；**红盘三至四天不上攻则红周必跌**（新数值化规则候选）；**锁仓两天（周四五）+不摸 4000 点则按系统减仓**（全语料最完整的事前锁定退出触发器，时间窗×点位双条件）；下午回落刹那有资金承接=有人吸筹（叙事支持满仓）；**把最后两成仓打进去、全仓突击**（仓位至极值）。
11. **1086**（8-12 盘后 45 秒）：信爷三日累计一万手多单、依然相信纯做多；"我今天打满了"互证；"每次想大干一场必被主力大干一场"自嘲。
12. **1087/1088**（8-12 CPI 前后）：CPI"最多符合预期"（事前 3 小时）→发布后 15 分钟确认，与官方读数一致；货政报告解读为放水前奏、降准降息一至两个月（开放）；明天大金融肯定高开（题材级不可精确结算）；"我今天满仓了明天主力来打我"自嘲——**8-13 实际放量大跌（1143/4317、中位 -1.4783%），满仓激进遭遇即时强反反馈**。

### 已核验的 PIT 数（8-10 至 8-13，本 session 复算）

| 日期 | 涨/跌 | 中位 | 较前日增量 |
|---|---:|---:|---:|
| 2026-08-10 | 4068/1391 | +1.2584% | -1444 亿 |
| 2026-08-11 | 1615/3777 | -0.6831% | -2027 亿 |
| 2026-08-12 | 4128/1280 | +0.8361% | -1690 亿 |
| 2026-08-13 | 1143/4317 | -1.4783% | +4011 亿 |

PIT `daily` 覆盖至 20260813。**1085 的锁仓-减仓触发窗口为 8-13/8-14 两日，8-14 超出覆盖，结算只能部分完成（已在 case findings 声明）。**

## 2. batch-044 剩余工作清单（接手第一步）

1. **hypothesis revision 10 条**（当前最新版之后依次延伸，先 `jq` 查最新版号再写；文案已在下方给出方向）：
   - `H-EXPECTATION-FEEDBACK-001-R29`：修复预期命中（8-12）、门禁三日命中/反例/反向并存、CPI 两步命中、触发窗口超覆盖声明纪律；
   - `H-SYSTEM-PRESET-001-R35`：卖点数值化示例（30% 减仓 30%/一半，基准未明）、系统 vs 赌博二分、红周必跌新规则候选；
   - `H-NEWS-STATE-WEIGHT-001-R31`：CPI 两步结构与官方读数一致、货政报告前奏解读开放、火箭失利事件 vindication 只到题材情绪层；
   - `H-CAPITAL-LEADS-NEWS-001-R28`：信爷三日一万手多单"纯做多"与满仓绑定、收费站机制化、吸筹叙事支持满仓（叙事-动作绑定风险最高形态）；
   - `H-THEME-CONTINUATION-001-R34`：大科技尾声月级调整、存储补涨周期结束、MLCC 细分双头减法、机器人事件窗口异动；
   - `H-TRADING-HORIZON-LOCK-001-R35`：锁仓两天+不摸 4000 则减仓=最完整事前锁定触发器；触发冻结与执行结算分离（执行在语料外）；
   - `H-AUDIENCE-SELF-LAYER-001-R22`：责任切割三连+邀功+"研究野哥一个人就行"+账户证词无回单；
   - `H-PHASE-EXPOSURE-CAP-001-R8`：仓位至极值满仓、利润垫自变量显式化、上限名存实亡与退出触发器纪律并存、8-13 大跌即时反反馈；
   - `H-MARKET-STATE-INPUTS-001-R19`：状态序列 8-10~8-13 四日（含 8-13 放量大跌）、连续精确缩量口播、量能-方向映射不稳定；
   - `H-TIERED-EXPECTATION-EXIT-001-R4`：反弹排序退出（先弹先结束、补涨最后结束）、不要想卖最高点。
   各条的可引用锚点（interpretation id）在对应 observation 内，格式 `observation:<aweme>#<interpret_id>`；case/event/bundle 引用格式同 batch-042/043。
2. **封板验证**：按 validate_batch043.py 同规格写 validate_batch044.py（13/13 schema+span、7/7 bundle cutoff、revision 链、case/event 引用、pytest 20 passed、`git diff --check`），追加 worklog `status=completed`、`resume_from` 指向综合阶段（建议 `M2-C-synthesis-1001-1088`）。
3. **1001—1088 综合 + 全语料冲突审查**：casebook（`docs/research/yeren-system/casebook.md`，上游 session 已有一段百条综合追加在工作区，本 session 未动）该轮由本步完成；然后最终 worklog 写 `resume_from=M2-C-complete-awaiting-G2-owner-review`，只宣布"可提交 G2 owner 审查"。

## 3. 当前工作区与工具

tracked：本文件及前两份 handoff 已 commit（见 git log）；`data/yeren_research/` 被 Git 忽略且 append-only。owner 已授权本分支 commit & push（2026-08-14 指令）。

临时工具在 `/tmp/claude-1000/-home-ps-papers-QuantMind/<session-scratchpad>/`：`obs_builder.py`（span 自动拼接）、`specs_m/n/o.py`（batch-044 observation 构建脚本）、`make_bundles_044.py`、`validate_batch043.py`（封板验证模板，改为 044 规格即可）。若已丢失按同思路重建：**span 一律由 `sentences[a:b+1]` 无分隔拼接自动生成，绝不手打 raw_text**；bundle 用 `scripts.yeren_research.market.build_market_bundles`；写盘用 `write_new_json`（`open("x")` 拒绝覆盖）。注意 builder 的 evidence/statement id 前缀 = aweme_id 末 6 位（本 session 两次手算错误被校验抓住，接手时直接 `aid[-6:]` 生成）。

## 4. 恢复命令

```bash
cd /home/ps/papers/QuantMind
git status -sb && git branch --show-current
tail -1 data/yeren_research/worklog.jsonl | jq -c '{work_unit,status,resume_from}'
# 应为 M2-C-batch-043 / completed / M2-C-batch-044

# batch-044 已写工件快速复验
/home/ps/anaconda3/envs/zhanglan/bin/python - <<'PY'
import json, sys
sys.path.insert(0, ".")
from pathlib import Path
from scripts.yeren_research.schema import VideoObservation
from scripts.yeren_research.inventory import read_jsonl
meta = list(read_jsonl(Path("data/yeren_corpus/metadata.jsonl")))
for row in meta[1075:1088]:
    aid = row["aweme_id"]
    m = VideoObservation.model_validate_json(Path(f"data/yeren_research/observations/{aid}.json").read_text())
    tp = Path(f"data/yeren_corpus/transcripts/{aid}.json")
    rows = json.loads(tp.read_text())["sentences"] if tp.exists() else []
    for ev in m.evidence:
        s = ev.transcript_span
        if s:
            assert "".join(r["text"] for r in rows[s.sentence_index:s.end_sentence_index+1]) == s.raw_text
print("batch-044 13/13 observations ready; resume with hypothesis revisions then sealing")
PY
```

G2 前继续禁止生产战法、确定性状态机、收益择优、回测优化和任何真实券商程序化下单。
