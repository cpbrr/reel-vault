"""Download what the later stages need for one post into its media directory.

Videos are reduced to a small mono mp3 for transcription; the mp4 itself is
kept only when the profile says so (storage.keep_media).
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import instaloader
import requests

MAX_IMAGES = 6
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"


class MediaError(RuntimeError):
    pass


@dataclass
class Media:
    thumbnail: Path
    owner: str = ""
    audio: Path | None = None
    images: list[Path] = field(default_factory=list)


def fetch_media(
    loader: instaloader.Instaloader, shortcode: str, workdir: Path, keep_video: bool = False
) -> Media:
    workdir.mkdir(parents=True, exist_ok=True)
    # from_shortcode loads the full metadata, so owner_username costs nothing extra here.
    post = instaloader.Post.from_shortcode(loader.context, shortcode)

    thumbnail = download(post.url, workdir / "thumb.jpg")
    media = Media(thumbnail=thumbnail, owner=post.owner_username)

    if post.typename == "GraphSidecar":
        for i, node in enumerate(post.get_sidecar_nodes()):
            if node.is_video and media.audio is None and node.video_url:
                media.audio = _video_to_audio(node.video_url, workdir, keep_video, f"video{i:02d}.mp4")
            elif not node.is_video and len(media.images) < MAX_IMAGES:
                media.images.append(download(node.display_url, workdir / f"img{i:02d}.jpg"))
    elif post.is_video and post.video_url:
        media.audio = _video_to_audio(post.video_url, workdir, keep_video)
    else:
        media.images.append(thumbnail)
    return media


def download(url: str, dest: Path) -> Path:
    try:
        with requests.get(url, stream=True, timeout=60, headers={"User-Agent": USER_AGENT}) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 16):
                    f.write(chunk)
    except requests.RequestException as e:
        raise MediaError(f"download failed: {e}") from e
    return dest


def _video_to_audio(
    video_url: str, workdir: Path, keep_video: bool, name: str = "video.mp4"
) -> Path | None:
    video = download(video_url, workdir / name)
    try:
        return extract_audio(video, workdir / "audio.mp3")
    finally:
        if not keep_video:
            video.unlink(missing_ok=True)


def extract_audio(video: Path, dest: Path) -> Path | None:
    """16 kHz mono mp3 (what Whisper uses anyway). None if the video has no audio track."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise MediaError("ffmpeg not found on PATH")
    result = subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-i", str(video),
         "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", str(dest)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        if "does not contain any stream" in result.stderr or "matches no streams" in result.stderr:
            return None
        raise MediaError(f"ffmpeg failed: {result.stderr.strip()[-500:]}")
    return dest
