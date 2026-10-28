"""Download what the later stages need for one post into its media directory.

Videos are reduced to a small mono mp3 for transcription plus a few still
frames (so Claude can read on-screen text); the mp4 itself is kept only when the
profile says so (storage.keep_media).
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import instaloader
import requests

MAX_IMAGES = 6
# Frames: one per ~8 s of video, at least 3 and at most 6, long edge 1024 px.
MIN_FRAMES, MAX_FRAMES, SECONDS_PER_FRAME, FRAME_EDGE = 3, 6, 8, 1024
FRAMES_DONE = "frames.done"  # marker: frame extraction was attempted for this post
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"


class MediaError(RuntimeError):
    pass


@dataclass
class Media:
    thumbnail: Path
    owner: str = ""
    audio: Path | None = None
    images: list[Path] = field(default_factory=list)
    frames: list[Path] = field(default_factory=list)


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
                media.audio, media.frames = _process_video(
                    node.video_url, workdir, keep_video, f"video{i:02d}.mp4"
                )
            elif not node.is_video and len(media.images) < MAX_IMAGES:
                media.images.append(download(node.display_url, workdir / f"img{i:02d}.jpg"))
    elif post.is_video and post.video_url:
        media.audio, media.frames = _process_video(post.video_url, workdir, keep_video)
    else:
        media.images.append(thumbnail)
    (workdir / FRAMES_DONE).touch()
    return media


def fetch_frames(loader: instaloader.Instaloader, shortcode: str, workdir: Path) -> list[Path]:
    """Frames for a post downloaded before frame extraction existed (video re-downloaded, not transcribed)."""
    workdir.mkdir(parents=True, exist_ok=True)
    post = instaloader.Post.from_shortcode(loader.context, shortcode)
    video_url = post.video_url if post.is_video else None
    if post.typename == "GraphSidecar":
        video_url = next((n.video_url for n in post.get_sidecar_nodes() if n.is_video and n.video_url), None)
    frames: list[Path] = []
    if video_url:
        video = download(video_url, workdir / "video.mp4")
        try:
            frames = extract_frames(video, workdir)
        finally:
            video.unlink(missing_ok=True)
    (workdir / FRAMES_DONE).touch()
    return frames


def needs_frames(workdir: Path, kind: str) -> bool:
    return kind != "image" and not (workdir / FRAMES_DONE).exists()


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


def _process_video(
    video_url: str, workdir: Path, keep_video: bool, name: str = "video.mp4"
) -> tuple[Path | None, list[Path]]:
    video = download(video_url, workdir / name)
    try:
        return extract_audio(video, workdir / "audio.mp3"), extract_frames(video, workdir)
    finally:
        if not keep_video:
            video.unlink(missing_ok=True)


def extract_frames(video: Path, workdir: Path) -> list[Path]:
    """Evenly spaced stills named frameNN_<second>s.jpg. Best effort: frames are
    extra context, so a failure here returns what it has instead of failing the post."""
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        return []
    probe = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True,
    )
    try:
        duration = float(probe.stdout.strip())
    except ValueError:
        return []
    count = max(MIN_FRAMES, min(MAX_FRAMES, round(duration / SECONDS_PER_FRAME)))
    scale = (f"scale='if(gt(iw,ih),{FRAME_EDGE},-2)':'if(gt(iw,ih),-2,{FRAME_EDGE})'")
    frames = []
    for i in range(count):
        at = duration * (i + 0.5) / count
        dest = workdir / f"frame{i:02d}_{at:.0f}s.jpg"
        result = subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", str(video),
             "-frames:v", "1", "-vf", scale, "-q:v", "4", str(dest)],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and dest.is_file():
            frames.append(dest)
    return frames


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
