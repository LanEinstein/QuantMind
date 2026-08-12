# 野人语料流水线

本目录实现 M1 的单视频流式流程：主页元数据分页采集 → 按发布时间从早到晚 → 临时下载一条
视频 → FFmpeg 抽音频 → 本地 FunASR Paraformer 转写 → 写语料和台账 → 删除临时视频。

## 一次性安装

Python 依赖必须安装到 `zhanglan` 环境：

```bash
/home/ps/anaconda3/envs/zhanglan/bin/pip install -e '.[corpus]'
mkdir -p ~/.local/share/quantmind
git clone https://github.com/Evil0ctal/Douyin_TikTok_Download_API.git \
  ~/.local/share/quantmind/Douyin_TikTok_Download_API
```

下载工具只提供当前有效的 `a_bogus` 参数签名；请求和文件下载由本目录的 IPv4-only
`httpx` 客户端完成。流水线从当前 Chrome 登录会话读取 `douyin.com` cookie，cookie 只在
进程内存中存在，不写入项目文件或日志。运行前请确认 Chrome 已登录抖音。

## Owner gate 前试跑

两张 GPU 都忙时可先用 CPU 跑最早一条：

```bash
/home/ps/anaconda3/envs/zhanglan/bin/python -m scripts.yeren_corpus \
  --limit 1 --device cpu
```

确认一条样例后再试最早 5-10 条；owner 确认转写质量前不要启动全量：

```bash
/home/ps/anaconda3/envs/zhanglan/bin/python -m scripts.yeren_corpus --limit 5
```

去掉 `--limit` 才会处理全部待处理作品。`ledger.jsonl` 中 `done` 的作品会跳过，失败作品
记录错误后继续，下一次运行会重新尝试；API 明确返回作品已删除、隐藏或不可见时记录
`unavailable` 终态，不伪造转写也不无限重试。待处理集合以 append-only 元数据为准，因此
作品从当前主页消失后仍会尝试通过历史 ID 补转。运行时每页请求间随机等待 5-8 秒；偶发
403 时等待 10 秒、刷新 Chrome 会话并只重试一次。每条作品在下载前重新取一次详情，
避免全量运行数天后使用过期的 CDN 地址；视频传输中断时从头覆盖临时文件并只重试一次，
相邻作品间再等待 3-6 秒。

输出位于 `data/yeren_corpus/`：

- `metadata.jsonl`：作品发布时间、描述、话题、互动数等元数据；
- `transcripts/<aweme_id>.json`：全文、句级毫秒偏移、ASR 模型版本；
- `ledger.jsonl`：`done` / `failed` 处理台账。
