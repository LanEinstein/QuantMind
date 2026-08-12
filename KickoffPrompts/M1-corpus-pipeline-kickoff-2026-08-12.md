# M1 接手 Prompt:野人语料流水线(2026-08-12)

> 在干净上下文中开始工作前,先完整读三份文件:`CLAUDE.md`(87 行,项目守则)、
> `AGENTS.md`(codex 守则)、`docs/research/midterm-rearch-action-plan-2026-08-12.md`(行动纲领)。
> 本 prompt 只补充它们没有的执行细节,冲突时以行动纲领为准。

## 一、你接手时的项目状态

QuantMind 于 2026-08-12 进入完全重构:只做中长线,以抖音主播「全能的野人」的交易体系为蓝本。
旧红线/冻结原则/amendment 流程**全部作废**(归档在 `docs/archive/`,只读参考),唯一底线 =
永禁真实券商下单。反过度防御(HERO)约定全程强制,见 CLAUDE.md。

已完成:计划书定稿(owner 已批)、仓库清理(缓存/废日志/旧治理文档归档,commit `45e1d08`)、
CLAUDE.md 重写 + AGENTS.md 新建(commit `0b1e65a`)、旧后端 systemd 服务已停止。
所有 commit 均在本地,**push 需 owner 明示授权**。

里程碑:**M1 语料流水线(本次任务)**→ M2 战法提炼 → M3 验证 → M4 执行器+飞书+前端 → M5 新章程。
每个里程碑结束设 owner gate,owner 点头才进下一步。

## 二、M1 任务定义

把「全能的野人」全部 1086 条视频变成带发布时间戳的文字库,流式处理,恒定磁盘占用。

**目标主页(2026-08-12 核实)**
- 昵称:全能的野人;抖音号:`203775400`;粉丝 177.8 万;作品 1086 条
- sec_uid:`MS4wLjABAAAAjoG0q686OVKqPnPYAhZVaVl5Y6Ul8gbWprwF52ualFY`
- 主页:`https://www.douyin.com/user/<sec_uid>`
- 内容形态:每日收盘后「X月X日复盘」口播为主 → 文字主体在语音里,必须 ASR

**流水线(owner 拍板的设计,不要改)**
从最早的视频开始,逐条执行,任何时刻本地只有一条视频:
1. 取元数据:视频 ID、标题、描述、话题标签、发布时间戳(精确到秒)、时长、互动数
2. 下载视频到临时目录
3. 本地 ASR 转写(FunASR Paraformer 中文模型;输出带句级时间偏移)
4. 落盘:转写全文 + 元数据 + 转写模型版本 →`data/yeren_corpus/`,append-only
5. (选做)删除前抽 2-4 张关键帧存档——他的视频常有大字贴片(如「空仓 试错 加仓 锁仓 推仓」)
6. 删除本地视频文件,写处理台账,进下一条

台账驱动断点续跑:重启后跳过已完成的视频 ID,幂等重入,失败条目记录原因后继续(单条失败不阻塞全局)。

**存储建议(可按实际微调,保持简单)**
```
data/yeren_corpus/
  ledger.jsonl        # 每行 {aweme_id, status: done|failed, error?, processed_at}
  metadata.jsonl      # 每行一条视频的完整元数据(含 create_time)
  transcripts/<aweme_id>.json   # {text, sentences: [{start_ms, end_ms, text}], asr_model}
  keyframes/<aweme_id>/*.jpg    # 选做
```

## 三、技术选型与坑

- **下载**:优先开源现成工具 —— `JoeanAmier/TikTokDownloader` 或
  `Evil0ctal/Douyin_TikTok_Download_API`(GitHub),配 owner 浏览器已登录的 cookie
  (owner 的 Chrome 可通过 claude-in-chrome 拿到会话;或让 owner 导出 cookie)。
  控制请求频率(条间 sleep 数秒 + 抖动),只采这一个公开主页。
  开源工具失效 → 兜底:用 owner 的 Chrome 半自动翻页采集,慢但稳;卡住就问 owner,别硬刚反爬。
- **ASR**:FunASR(`modelscope/FunASR`),Paraformer-large 中文模型,财经口语好于 Whisper 系;
  备选 faster-whisper 交叉校验。先 `nvidia-smi` 探 GPU,有 GPU 用 GPU,没有 CPU 也能跑(慢一些)。
  模型权重下载走 modelscope,国内快。
- **环境**:Python 一律 `/home/ps/anaconda3/envs/zhanglan/bin/`;新依赖装这个 env;
  出站 IPv4-only(httpx `local_address="0.0.0.0"`);全程离线本地转写,语料不出机器。
- **代码位置**:`scripts/yeren_corpus/` 新建(采集与转写是研究脚本,不进 backend);
  英文注释与 commit,面向 owner 的输出用中文。

## 四、反过度防御提醒(HERO,针对本任务)

- 台账就是一个 jsonl,不要引入数据库/队列/重试框架。
- 不要给转写文本算没有用途的 checksum;去重靠 aweme_id 就够。
- 不要为"抖音可能改版"预建适配层;坏了再修。
- 单条视频失败:记录、跳过、继续;不要写复杂的重试状态机(最多简单重试 1 次)。
- 完成的标准是"1086 条语料在盘上、台账对得上",不是测试覆盖率。核心解析函数配少量真样本测试即可。

## 五、M1 验收(owner gate)

1. 流水线端到端跑通:先在最早的 5-10 条视频上验证全链路(元数据/转写质量/删除/续跑),
   把一条转写样例(带时间偏移)发给 owner 过目转写质量,确认后再放开全量跑。
2. 全量跑挂机期间定期汇报进度(已处理条数/失败数/预计剩余时间)。
3. 结束时报告:总条数、成功/失败清单、语料库磁盘占用、最早与最新视频的发布时间范围。
4. commit 本地(pipeline 代码一个 commit;语料数据不进 git,`data/` 已 gitignore——确认
   `.gitignore` 覆盖 `data/yeren_corpus/`,没有就加一行)。

## 六、开工第一步

1. 读完三份文件后,`nvidia-smi` 探 GPU、确认 zhanglan env 可用。
2. 装并试跑下载工具,用主页最早的 1 条视频打通"元数据+下载"。
3. 装 FunASR,转写那条视频,把样例给 owner 看。
4. owner 确认质量 → 全量挂机。
