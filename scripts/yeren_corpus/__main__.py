"""Command-line entry point for the M1 owner-gated corpus run."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from scripts.yeren_corpus.asr import FunASRTranscriber
from scripts.yeren_corpus.douyin import DouyinClient
from scripts.yeren_corpus.pipeline import CorpusPaths, CorpusPipeline

SEC_UID = "MS4wLjABAAAAjoG0q686OVKqPnPYAhZVaVl5Y6Ul8gbWprwF52ualFY"
DEFAULT_SIGNER_ROOT = (
    Path.home() / ".local/share/quantmind/Douyin_TikTok_Download_API"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="全能的野人语料流水线")
    parser.add_argument("--root", type=Path, default=Path("data/yeren_corpus"))
    parser.add_argument("--signer-root", type=Path, default=DEFAULT_SIGNER_ROOT)
    parser.add_argument("--device", default="auto", help="auto, cpu, cuda, or cuda:N")
    parser.add_argument(
        "--limit",
        type=int,
        help="只处理时间最早的 N 条待处理作品；owner 验收前使用 5-10",
    )
    return parser.parse_args()


def resolve_device(requested: str) -> str:
    if requested != "auto":
        return requested
    import torch

    if not torch.cuda.is_available():
        return "cpu"
    free_memory = [
        torch.cuda.mem_get_info(index)[0]
        for index in range(torch.cuda.device_count())
    ]
    return f"cuda:{max(range(len(free_memory)), key=free_memory.__getitem__)}"


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    device = resolve_device(args.device)
    logging.info("ASR 设备: %s", device)
    transcriber = FunASRTranscriber(device)
    with DouyinClient(SEC_UID, args.signer_root) as client:
        success, failed = CorpusPipeline(
            client=client,
            transcriber=transcriber,
            paths=CorpusPaths(args.root),
        ).run(limit=args.limit)
    logging.info("本次完成: 成功 %s，失败 %s", success, failed)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
