"""Groq Whisper (OpenAI-compatible /audio/transcriptions endpoint)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import requests

from reel_vault.transcribe.base import RateLimited, TranscribeError, Transcript

ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"
# Short waits are absorbed here; anything longer ends the run (RateLimited).
MAX_INLINE_WAIT = 60

# Whisper names languages in full ("english"); notes use ISO codes.
LANGUAGE_CODES = {
    "english": "en", "vietnamese": "vi", "chinese": "zh", "japanese": "ja", "korean": "ko",
    "spanish": "es", "french": "fr", "german": "de", "portuguese": "pt", "russian": "ru",
    "italian": "it", "thai": "th", "indonesian": "id", "hindi": "hi", "arabic": "ar",
}


class GroqTranscriber:
    def __init__(self, model: str = "whisper-large-v3-turbo", api_key: str | None = None):
        self.model = model
        self.api_key = api_key or os.environ.get("GROQ_API_KEY")
        if not self.api_key:
            raise TranscribeError("GROQ_API_KEY is not set")

    def transcribe(self, audio: Path) -> Transcript:
        for attempt in range(3):
            with open(audio, "rb") as f:
                try:
                    resp = requests.post(
                        ENDPOINT,
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        files={"file": (audio.name, f)},
                        data={"model": self.model, "response_format": "verbose_json", "temperature": "0"},
                        timeout=120,
                    )
                except requests.RequestException as e:
                    raise TranscribeError(f"Groq request failed: {e}") from e

            if resp.status_code == 429:
                wait = _retry_after(resp)
                if wait > MAX_INLINE_WAIT or attempt == 2:
                    raise RateLimited(f"Groq rate limit, retry after {wait:.0f}s")
                time.sleep(wait)
                continue
            if resp.status_code != 200:
                raise TranscribeError(f"Groq HTTP {resp.status_code}: {resp.text[:300]}")

            body = resp.json()
            language = (body.get("language") or "").lower()
            return Transcript(
                text=body.get("text", "").strip(),
                language=LANGUAGE_CODES.get(language, language or None),
            )
        raise TranscribeError("unreachable")


def _retry_after(resp: requests.Response) -> float:
    try:
        return float(resp.headers.get("retry-after", "10"))
    except ValueError:
        return 10.0
