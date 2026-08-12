"""Ledger-driven, single-video-at-a-time corpus processing."""

from __future__ import annotations

import json
import logging
import random
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from scripts.yeren_corpus.asr import extract_audio
from scripts.yeren_corpus.douyin import DouyinClient, VideoUnavailableError, utc_now
from scripts.yeren_corpus.models import Transcript, VideoItem

LOGGER = logging.getLogger(__name__)


class Transcriber(Protocol):
    def transcribe(self, audio_path: Path) -> Transcript: ...


@dataclass(frozen=True)
class CorpusPaths:
    root: Path

    @property
    def ledger(self) -> Path:
        return self.root / "ledger.jsonl"

    @property
    def metadata(self) -> Path:
        return self.root / "metadata.jsonl"

    @property
    def transcripts(self) -> Path:
        return self.root / "transcripts"

    def create(self) -> None:
        self.transcripts.mkdir(parents=True, exist_ok=True)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def resolved_ids(path: Path) -> set[str]:
    return {
        str(entry["aweme_id"])
        for entry in _read_jsonl(path)
        if entry.get("status") in {"done", "unavailable"}
    }


def metadata_ids_in_chronological_order(path: Path) -> list[str]:
    records = sorted(
        (entry for entry in _read_jsonl(path) if entry.get("aweme_id")),
        key=lambda entry: int(entry.get("create_time") or 0),
    )
    return list(dict.fromkeys(str(entry["aweme_id"]) for entry in records))


def _append_jsonl(path: Path, data: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
        output.write("\n")


def append_new_metadata(path: Path, catalog: list[VideoItem]) -> None:
    existing = {
        str(entry["aweme_id"])
        for entry in _read_jsonl(path)
        if entry.get("aweme_id")
    }
    for item in catalog:
        if item.metadata.aweme_id not in existing:
            _append_jsonl(path, item.metadata.to_dict())
            existing.add(item.metadata.aweme_id)


class CorpusPipeline:
    def __init__(
        self,
        client: DouyinClient,
        transcriber: Transcriber,
        paths: CorpusPaths,
    ) -> None:
        self.client = client
        self.transcriber = transcriber
        self.paths = paths

    def _recover_finished_transcript(self, aweme_id: str) -> bool:
        transcript_path = self.paths.transcripts / f"{aweme_id}.json"
        if not transcript_path.exists():
            return False
        _append_jsonl(
            self.paths.ledger,
            {
                "aweme_id": aweme_id,
                "status": "done",
                "processed_at": utc_now(),
                "recovered_existing_transcript": True,
            },
        )
        return True

    def _process_one(self, aweme_id: str) -> None:
        item = self.client.fetch_detail(aweme_id)
        with tempfile.TemporaryDirectory(prefix=f"yeren-{aweme_id}-") as raw_temp:
            temp = Path(raw_temp)
            video_path = temp / f"{aweme_id}.mp4"
            audio_path = temp / f"{aweme_id}.wav"
            self.client.download(item, video_path)
            extract_audio(video_path, audio_path)
            transcript = self.transcriber.transcribe(audio_path)
            target = self.paths.transcripts / f"{aweme_id}.json"
            staged = self.paths.transcripts / f".{aweme_id}.json.tmp"
            staged.write_text(
                json.dumps(transcript.to_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            staged.replace(target)
        _append_jsonl(
            self.paths.ledger,
            {"aweme_id": aweme_id, "status": "done", "processed_at": utc_now()},
        )

    def run(self, *, limit: int | None = None) -> tuple[int, int]:
        self.paths.create()
        catalog = self.client.fetch_catalog()
        append_new_metadata(self.paths.metadata, catalog)
        done = resolved_ids(self.paths.ledger)
        pending = [
            aweme_id
            for aweme_id in metadata_ids_in_chronological_order(self.paths.metadata)
            if aweme_id not in done
        ]
        if limit is not None:
            pending = pending[:limit]
        success = 0
        failed = 0
        started = time.monotonic()
        for index, aweme_id in enumerate(pending, start=1):
            try:
                if not self._recover_finished_transcript(aweme_id):
                    self._process_one(aweme_id)
                success += 1
                elapsed = time.monotonic() - started
                eta_seconds = elapsed / index * (len(pending) - index)
                LOGGER.info(
                    "进度 %s/%s，成功 %s，失败 %s，预计剩余 %.1f 分钟",
                    index,
                    len(pending),
                    success,
                    failed,
                    eta_seconds / 60,
                )
            except VideoUnavailableError as error:
                LOGGER.warning("作品 %s 当前不可用，记录终态后继续", aweme_id)
                _append_jsonl(
                    self.paths.ledger,
                    {
                        "aweme_id": aweme_id,
                        "status": "unavailable",
                        "error": str(error),
                        "processed_at": utc_now(),
                    },
                )
            except Exception as error:
                failed += 1
                LOGGER.exception("作品 %s 处理失败，继续下一条", aweme_id)
                _append_jsonl(
                    self.paths.ledger,
                    {
                        "aweme_id": aweme_id,
                        "status": "failed",
                        "error": f"{type(error).__name__}: {error}",
                        "processed_at": utc_now(),
                    },
                )
            if index < len(pending):
                time.sleep(random.uniform(3.0, 6.0))
        return success, failed
