"""On-device Whisper via faster-whisper: audio never leaves the machine.

Install with `uv sync --extra local`. The model is downloaded once from the
Hugging Face Hub on first use and cached; after that it runs offline.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from reel_vault.transcribe.base import TranscribeError, Transcript


class LocalWhisperTranscriber:
    def __init__(self, model: str = "small"):
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise TranscribeError("local transcription needs faster-whisper: uv sync --extra local") from e
        # int8 keeps CPU-only laptops usable; a GPU is picked up automatically.
        self._model = WhisperModel(model, device="auto", compute_type="int8")

    def transcribe(self, audio: Path) -> Transcript:
        try:
            segments, info = self._model.transcribe(_decode(audio), vad_filter=True)
            text = " ".join(segment.text.strip() for segment in segments)  # runs here
        except Exception as e:  # decoding errors surface from the generator
            raise TranscribeError(f"local whisper failed: {e}") from e
        return Transcript(text=text.strip(), language=info.language or None)


def _decode(audio: Path):
    """16 kHz mono float32 samples via ffmpeg (already required), so faster-whisper's
    PyAV decoder, which breaks across PyAV releases, is never used."""
    import numpy as np

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise TranscribeError("ffmpeg not found on PATH")
    result = subprocess.run(
        [ffmpeg, "-loglevel", "error", "-i", str(audio), "-f", "f32le", "-ac", "1", "-ar", "16000", "-"],
        capture_output=True,
    )
    if result.returncode != 0:
        raise TranscribeError(f"ffmpeg decode failed: {result.stderr.decode(errors='replace')[-300:]}")
    return np.frombuffer(result.stdout, dtype=np.float32)
