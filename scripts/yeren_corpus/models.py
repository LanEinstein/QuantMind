"""Small immutable records shared by the corpus pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class VideoMetadata:
    """Keep the research fields needed to align a video with later market data."""

    aweme_id: str
    title: str
    description: str
    hashtags: tuple[str, ...]
    create_time: int
    published_at: str
    duration_ms: int
    digg_count: int
    comment_count: int
    collect_count: int
    share_count: int
    play_count: int
    author_nickname: str
    author_douyin_id: str
    source_url: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["hashtags"] = list(self.hashtags)
        return data

@dataclass(frozen=True)
class VideoItem:
    """Attach the short-lived CDN URL without persisting it as metadata."""

    metadata: VideoMetadata
    download_url: str


@dataclass(frozen=True)
class Sentence:
    start_ms: int
    end_ms: int
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Transcript:
    text: str
    sentences: tuple[Sentence, ...]
    asr_model: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "sentences": [sentence.to_dict() for sentence in self.sentences],
            "asr_model": self.asr_model,
        }
