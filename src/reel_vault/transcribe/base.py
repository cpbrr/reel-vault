from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str | None = None


class TranscribeError(RuntimeError):
    pass


class RateLimited(TranscribeError):
    """Provider quota hit. The run should stop and pick up again next time."""


class Transcriber(Protocol):
    def transcribe(self, audio: Path) -> Transcript: ...


def make_transcriber(backend: str, model: str | None = None) -> Transcriber:
    if backend == "groq":
        from reel_vault.transcribe.groq import GroqTranscriber

        return GroqTranscriber(model=model) if model else GroqTranscriber()
    raise SystemExit(f"Unknown transcribe backend {backend!r}")
