"""Local FunASR transcription with sentence-level millisecond offsets."""

from __future__ import annotations

import importlib.metadata
import socket
import subprocess
from pathlib import Path
from typing import Any, cast

from scripts.yeren_corpus.models import Sentence, Transcript


def _force_ipv4_dns() -> None:
    """Apply the repository's IPv4-only rule to ModelScope model downloads."""
    original = socket.getaddrinfo
    if getattr(original, "__quantmind_ipv4_only__", False):
        return

    def ipv4_only(
        host: str,
        port: int | str,
        family: int = 0,
        type_: int = 0,
        proto: int = 0,
        flags: int = 0,
    ) -> list[tuple[Any, ...]]:
        return original(host, port, socket.AF_INET, type_, proto, flags)

    setattr(ipv4_only, "__quantmind_ipv4_only__", True)
    socket.getaddrinfo = cast(Any, ipv4_only)


def extract_audio(video_path: Path, audio_path: Path) -> None:
    """Normalize audio before ASR so video codecs do not leak into model behavior."""
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            str(audio_path),
        ],
        check=True,
    )


def normalize_result(result: dict[str, Any], model_version: str) -> Transcript:
    sentences = tuple(
        Sentence(
            start_ms=int(sentence["start"]),
            end_ms=int(sentence["end"]),
            text=str(sentence.get("text") or sentence.get("sentence") or "").strip(),
        )
        for sentence in result.get("sentence_info") or ()
        if sentence.get("text") or sentence.get("sentence")
    )
    text = str(result.get("text") or "").strip()
    if text and not sentences:
        raise RuntimeError("FunASR 返回了文本但没有句级时间偏移")
    return Transcript(text=text, sentences=sentences, asr_model=model_version)


class FunASRTranscriber:
    """Reuse one loaded Paraformer pipeline for every sequential video."""

    def __init__(self, device: str) -> None:
        _force_ipv4_dns()
        from funasr import AutoModel  # type: ignore[import-untyped]

        version = importlib.metadata.version("funasr")
        self.model_version = (
            "iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-"
            "common-vocab8404-pytorch@master; "
            "vad=iic/speech_fsmn_vad_zh-cn-16k-common-pytorch@master; "
            "punc=iic/punc_ct-transformer_cn-en-common-vocab471067-large@master; "
            f"funasr={version}"
        )
        self.model = AutoModel(
            model="paraformer-zh",
            vad_model="fsmn-vad",
            punc_model="ct-punc",
            device=device,
            disable_update=True,
            disable_pbar=True,
        )

    def transcribe(self, audio_path: Path) -> Transcript:
        result = self.model.generate(
            input=str(audio_path),
            batch_size_s=300,
            sentence_timestamp=True,
        )
        if not result:
            raise RuntimeError("FunASR 没有返回转写结果")
        return normalize_result(result[0], self.model_version)
