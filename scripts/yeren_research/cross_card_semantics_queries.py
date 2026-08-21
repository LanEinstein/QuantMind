"""The four questions of work unit F, as frozen retrieval queries.

Q1/Q2/Q4 are cross-card; Q3 is 520-only. Both forms are kept deliberately: the
co-occurrence queries are what the execution plan proposed, and the census
queries are what the answers actually rest on (the decisive terms turn out to be
rare enough corpus-wide that reading every occurrence beats co-occurrence).
Keeping the zero-hit co-occurrence queries makes the retrieval path auditable
instead of leaving only the queries that produced results.
"""

from __future__ import annotations

from scripts.yeren_research.cross_card_semantics import Query

QUERIES: tuple[Query, ...] = (
    Query(
        name="Q1-moving-average-kind",
        left=("均线", "日线", "均价"),
        right=("加权", "指数", "简单", "算术", "平滑", "参数", "设置", "指标", "复权"),
        note="卡1+卡8: 均线是 SMA 还是 EMA、用什么价",
    ),
    Query(
        name="Q1b-latin-ma",
        left=("EMA", "ema", "MA", "均线设置"),
        note="卡1+卡8: 拉丁字母写法命中即高价值",
    ),
    Query(
        name="Q2-intraday-or-close",
        left=("盘中", "收盘", "尾盘", "分时", "竞价", "开盘", "早盘", "午后"),
        right=(
            "买",
            "卖",
            "进",
            "出",
            "走",
            "离场",
            "入场",
            "上车",
            "下车",
            "减仓",
            "加仓",
            "清仓",
            "判断",
            "确认",
            "成立",
        ),
        note="全部18张卡: 判定时点是盘中还是收盘",
    ),
    Query(
        name="Q2b-ma-cross-timing",
        left=("上穿", "拐头", "下穿"),
        right=("盘中", "收盘", "尾盘", "分时", "竞价", "开盘"),
        window=2,
        note="卡8: 「即将上穿」的盘中判定",
    ),
    Query(
        name="Q3-arbitrage-magnitude",
        left=("个点", "空间", "利润", "收益"),
        right=("套利", "一口", "就跑", "就走", "短线"),
        window=2,
        note="卡8 专属: 八到十个点是事后描述、事前预期还是修辞",
    ),
    Query(
        name="Q3b-point-counts",
        left=("八个点", "十个点", "八到十", "三到五", "三五个点", "五个点", "八到九"),
        note="卡8 专属: 数字口语形式",
    ),
    Query(
        name="Q4-trend-invalidation",
        left=("趋势", "逻辑", "模式", "结构"),
        right=(
            "失效",
            "走坏",
            "变坏",
            "破位",
            "跌破",
            "结束",
            "不行了",
            "坏了",
            "完了",
        ),
        window=2,
        note="卡1 退出端: 「趋势失效」有没有可观察定义",
    ),
    Query(
        name="Q4b-break-below-lines",
        left=("下跌中继", "跌破", "破位", "走出下跌"),
        note="卡1 退出端: 形态型硬退出的可观察说法",
    ),
    # The co-occurrence queries above return 0 hits for Q1 and Q2b. The decisive
    # terms turn out to be rare enough corpus-wide (均线 11, 上穿 7, 失效 8) that
    # an exhaustive census of every occurrence is both feasible and stronger
    # evidence than co-occurrence, so both forms are kept in the artifact.
    Query(
        name="Q1c-ma-vocabulary-census",
        left=("均线", "均价"),
        note="卡1+卡8 穷举: 作者提到均线的全部场合",
    ),
    Query(
        name="Q1d-ma-type-census",
        left=(
            "加权",
            "指数均线",
            "EMA",
            "MA",
            "参数",
            "复权",
            "平滑",
            "算术",
            "简单移动",
        ),
        note="卡1+卡8 穷举: 任何指定均线类型的词，命中为 0 即无表述",
    ),
    Query(
        name="Q2c-ma-cross-census",
        left=("上穿", "下穿"),
        note="卡8 穷举: 上穿/下穿的全部场合，检查有无盘中判定说明",
    ),
    Query(
        name="Q4c-invalidation-census",
        left=("失效", "破位", "跌破", "走坏", "收复不了", "杀破"),
        note="卡1 退出端穷举: 失效类措辞的全部场合",
    ),
)
