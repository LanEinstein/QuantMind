from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from scripts.yeren_corpus.asr import FunASRTranscriber, normalize_result
from scripts.yeren_corpus.douyin import DouyinClient, normalize_aweme
from scripts.yeren_corpus.pipeline import (
    append_new_metadata,
    completed_ids,
    metadata_ids_in_chronological_order,
)


def _raw_aweme() -> dict[str, object]:
    return {
        "aweme_id": "123456789",
        "desc": "3月1日复盘\n正文",
        "text_extra": [
            {"hashtag_name": "股票"},
            {"hashtag_name": "复盘"},
            {"hashtag_name": "股票"},
        ],
        "create_time": 1_700_000_000,
        "duration": 42_000,
        "statistics": {
            "digg_count": 10,
            "comment_count": 2,
            "collect_count": 3,
            "share_count": 4,
            "play_count": 50,
        },
        "author": {"nickname": "全能的野人", "unique_id": "203775400"},
        "video": {"play_addr": {"url_list": ["https://video.example/123"]}},
    }


def test_normalize_aweme_keeps_research_fields() -> None:
    item = normalize_aweme(_raw_aweme())

    assert item.metadata.aweme_id == "123456789"
    assert item.metadata.title == "3月1日复盘"
    assert item.metadata.hashtags == ("股票", "复盘")
    assert item.metadata.create_time == 1_700_000_000
    assert item.metadata.duration_ms == 42_000
    assert item.download_url == "https://video.example/123"


def test_normalize_aweme_allows_empty_description_and_missing_video() -> None:
    raw = _raw_aweme()
    raw["desc"] = ""
    raw["video"] = {}

    item = normalize_aweme(raw)

    assert item.metadata.title == "123456789"
    assert item.download_url == ""


def test_normalize_result_preserves_sentence_offsets() -> None:
    transcript = normalize_result(
        {
            "text": "先试错，再加仓。",
            "sentence_info": [
                {"start": 120, "end": 980, "text": "先试错，"},
                {"start": 1_020, "end": 1_880, "text": "再加仓。"},
            ],
        },
        "paraformer-test",
    )

    assert transcript.text == "先试错，再加仓。"
    assert [sentence.to_dict() for sentence in transcript.sentences] == [
        {"start_ms": 120, "end_ms": 980, "text": "先试错，"},
        {"start_ms": 1_020, "end_ms": 1_880, "text": "再加仓。"},
    ]


def test_transcriber_records_empty_speech_without_text(tmp_path: Path) -> None:
    class EmptySpeechModel:
        def generate(self, **_: object) -> list[object]:
            return []

    transcriber = object.__new__(FunASRTranscriber)
    transcriber.model = EmptySpeechModel()
    transcriber.model_version = "paraformer-test"

    transcript = transcriber.transcribe(tmp_path / "silent.wav")

    assert transcript.text == ""
    assert transcript.sentences == ()
    assert transcript.asr_model == "paraformer-test"


def test_metadata_and_ledger_are_append_only_by_aweme_id(tmp_path: Path) -> None:
    item = normalize_aweme(_raw_aweme())
    metadata_path = tmp_path / "metadata.jsonl"
    append_new_metadata(metadata_path, [item, item])
    append_new_metadata(metadata_path, [item])

    assert len(metadata_path.read_text(encoding="utf-8").splitlines()) == 1

    ledger_path = tmp_path / "ledger.jsonl"
    ledger_path.write_text(
        json.dumps({"aweme_id": "failed", "status": "failed"})
        + "\n"
        + json.dumps({"aweme_id": "done", "status": "done"})
        + "\n",
        encoding="utf-8",
    )
    assert completed_ids(ledger_path) == {"done"}


def test_pending_metadata_includes_items_missing_from_current_catalog(
    tmp_path: Path,
) -> None:
    metadata_path = tmp_path / "metadata.jsonl"
    metadata_path.write_text(
        "\n".join(
            (
                json.dumps({"aweme_id": "newer", "create_time": 2}),
                json.dumps({"aweme_id": "older", "create_time": 1}),
            )
        )
        + "\n",
        encoding="utf-8",
    )

    assert metadata_ids_in_chronological_order(metadata_path) == ["older", "newer"]


def test_download_restarts_once_after_truncated_response(
    tmp_path: Path, monkeypatch: Any
) -> None:
    class FakeResponse:
        headers = {"content-type": "video/mp4"}

        def __init__(self, fails: bool) -> None:
            self.fails = fails

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self, *, chunk_size: int) -> Any:
            del chunk_size
            yield b"partial" if self.fails else b"complete"
            if self.fails:
                raise httpx.RemoteProtocolError("truncated response")

    class FakeHttpClient:
        calls = 0

        def stream(self, method: str, url: str) -> FakeResponse:
            del method, url
            self.calls += 1
            return FakeResponse(fails=self.calls == 1)

    client = object.__new__(DouyinClient)
    client.client = FakeHttpClient()
    monkeypatch.setattr("scripts.yeren_corpus.douyin.time.sleep", lambda _: None)
    destination = tmp_path / "video.mp4"

    client.download(normalize_aweme(_raw_aweme()), destination)

    assert destination.read_bytes() == b"complete"
    assert client.client.calls == 2
