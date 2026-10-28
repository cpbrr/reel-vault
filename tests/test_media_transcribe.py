import shutil
import subprocess

import pytest

from reel_vault.media import extract_audio, extract_frames
from reel_vault.transcribe import groq
from reel_vault.transcribe.base import RateLimited, TranscribeError

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _make_video(path, with_audio: bool):
    inputs = ["-f", "lavfi", "-i", "color=c=black:s=64x64:d=1"]
    if with_audio:
        inputs += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1"]
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-shortest", str(path)], check=True)


@needs_ffmpeg
def test_extract_audio(tmp_path):
    video = tmp_path / "v.mp4"
    _make_video(video, with_audio=True)
    out = extract_audio(video, tmp_path / "a.mp3")
    assert out is not None and out.stat().st_size > 0


@needs_ffmpeg
def test_extract_audio_silent_video_returns_none(tmp_path):
    video = tmp_path / "v.mp4"
    _make_video(video, with_audio=False)
    assert extract_audio(video, tmp_path / "a.mp3") is None


class FakeResponse:
    def __init__(self, status, body=None, headers=None):
        self.status_code = status
        self._body = body or {}
        self.headers = headers or {}
        self.text = str(body)

    def json(self):
        return self._body


def test_groq_success_maps_language(tmp_path, monkeypatch):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    monkeypatch.setattr(groq.requests, "post", lambda *a, **k: FakeResponse(200, {"text": " hi ", "language": "Vietnamese"}))
    result = groq.GroqTranscriber(api_key="k").transcribe(audio)
    assert result.text == "hi"
    assert result.language == "vi"


def test_groq_long_rate_limit_raises(tmp_path, monkeypatch):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    monkeypatch.setattr(groq.requests, "post", lambda *a, **k: FakeResponse(429, headers={"retry-after": "600"}))
    with pytest.raises(RateLimited):
        groq.GroqTranscriber(api_key="k").transcribe(audio)


def test_groq_short_rate_limit_retries(tmp_path, monkeypatch):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    responses = [FakeResponse(429, headers={"retry-after": "1"}), FakeResponse(200, {"text": "ok"})]
    monkeypatch.setattr(groq.requests, "post", lambda *a, **k: responses.pop(0))
    monkeypatch.setattr(groq.time, "sleep", lambda s: None)
    assert groq.GroqTranscriber(api_key="k").transcribe(audio).text == "ok"


def test_groq_requires_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(TranscribeError):
        groq.GroqTranscriber()


@needs_ffmpeg
@pytest.mark.parametrize("seconds,expected", [(4, 3), (40, 5), (120, 6)])
def test_extract_frames_count_and_size(tmp_path, seconds, expected):
    video = tmp_path / "v.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=s=1080x1920:d={seconds}:r=5",
         str(video)],
        check=True,
    )
    frames = extract_frames(video, tmp_path)
    assert len(frames) == expected
    assert frames[0].name.startswith("frame00_") and frames[0].name.endswith("s.jpg")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(frames[0])],
        capture_output=True, text=True, check=True,
    )
    assert probe.stdout.strip() == "576,1024"  # long edge scaled to 1024


def test_extract_frames_bad_file_is_empty(tmp_path):
    bad = tmp_path / "v.mp4"
    bad.write_bytes(b"not a video")
    assert extract_frames(bad, tmp_path) == []
