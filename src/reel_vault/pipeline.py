"""Per-post stages: media + transcribe -> analyze -> enrich -> write.

Each stage persists its result in state.db before the next starts, so a crash
or a stopped run resumes where it left off. Downloaded files live in
data/media/<shortcode>/ and are deleted once the note is written (or the post
fails for good) unless storage.keep_media is on.
"""

from __future__ import annotations

import copy
import json
import random
import shutil
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from instaloader.exceptions import (
    ConnectionException,
    InstaloaderException,
    LoginRequiredException,
)

from reel_vault.analyze.claude import ClaudeError
from reel_vault.categories import Registry
from reel_vault.config import Config
from reel_vault.media import MediaError, fetch_frames, fetch_media, needs_frames
from reel_vault.state import State, Status
from reel_vault.transcribe.base import RateLimited, Transcriber, TranscribeError
from reel_vault.vault.writer import VaultWriter


class StopRun(Exception):
    """Something that will fail every remaining post the same way."""


@dataclass
class RunReport:
    written: list[tuple[str, str]] = field(default_factory=list)  # (category, title)
    failed: list[tuple[str, str]] = field(default_factory=list)  # (shortcode, error)
    stopped: str | None = None
    usage: dict[str, float] = field(default_factory=dict)  # summed Claude usage

    def add_usage(self, usage: dict) -> None:
        for key, value in usage.items():
            self.usage[key] = self.usage.get(key, 0) + value


class Pipeline:
    def __init__(
        self,
        cfg: Config,
        state: State,
        loader,
        transcriber: Transcriber,
        analyzer,
        *,
        enricher=None,
        registry: Registry | None = None,
        writer: VaultWriter | None = None,
        fetch_media: Callable = fetch_media,
        fetch_frames: Callable = fetch_frames,
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] = print,
    ):
        self.cfg = cfg
        self.state = state
        self.loader = loader
        self.transcriber = transcriber
        self.analyzer = analyzer
        self.enricher = enricher
        self.registry = registry or Registry(cfg.categories_file)
        self.writer = writer or VaultWriter(cfg.paths.vault_dir)
        self._fetch_media = fetch_media
        self._fetch_frames = fetch_frames
        self._sleep = sleep
        self._log = log

    def process(
        self, limit: int = 0, only: list[str] | None = None, stop_after: Status | None = None
    ) -> RunReport:
        """`stop_after=Status.TRANSCRIBED` downloads and transcribes only (no Claude)."""
        report = RunReport()
        if only:
            queue = [row for row in self.state.work_queue() if row["shortcode"] in only][: limit or None]
        else:
            queue = self.state.work_queue(limit)
        if stop_after == Status.TRANSCRIBED:
            queue = [
                row for row in queue
                if row["status"] in (Status.PENDING, Status.MEDIA)
                or (row["status"] == Status.TRANSCRIBED
                    and needs_frames(self.cfg.media_dir / row["shortcode"], row["kind"]))
            ]
        self._log(f"{len(queue)} posts to process")

        for i, row in enumerate(queue):
            shortcode = row["shortcode"]
            try:
                hit_instagram = self._process_one(shortcode, report, stop_after)
            except StopRun as e:
                report.stopped = str(e)
                self._log(f"stopping run: {e}")
                break
            except (MediaError, TranscribeError, ClaudeError, InstaloaderException, OSError, KeyError) as e:
                error = f"{type(e).__name__}: {e}"
                permanent = self.state.record_failure(shortcode, error)
                if permanent and not self.cfg.keep_media:
                    shutil.rmtree(self.cfg.media_dir / shortcode, ignore_errors=True)
                report.failed.append((shortcode, error))
                self._log(f"  {shortcode} failed{' permanently' if permanent else ''}: {error}")
                hit_instagram = True
            if hit_instagram and i < len(queue) - 1:
                self._sleep(random.uniform(self.cfg.fetch.sleep_min, self.cfg.fetch.sleep_max))

        if report.written:
            self.writer.write_indexes(
                self.state.written(), {c.name: c.description for c in self.registry.categories}
            )
        return report

    def _process_one(self, shortcode: str, report: RunReport, stop_after: Status | None = None) -> bool:
        """Advance one post as far as possible. Returns True if Instagram was contacted."""
        workdir = self.cfg.media_dir / shortcode
        hit_instagram = False
        row = self.state.get_post(shortcode)

        if row["status"] in (Status.PENDING, Status.MEDIA):
            self._log(f"  {shortcode} downloading")
            hit_instagram = True
            with _instagram_errors():
                media = self._fetch_media(self.loader, shortcode, workdir, keep_video=self.cfg.keep_media)
            if media.owner and not row["owner"]:
                self.state.set_owner(shortcode, media.owner)
            transcript_text, language = "", None
            if media.audio:
                self._log(f"  {shortcode} transcribing")
                try:
                    transcript = self.transcriber.transcribe(media.audio)
                except RateLimited as e:
                    raise StopRun(str(e)) from e
                transcript_text, language = transcript.text, transcript.language
            self.state.save_transcript(shortcode, transcript_text, language)
            row = self.state.get_post(shortcode)
            self._log(f"  {shortcode} transcribed ({len(media.frames)} frames)")

        # Claude runs inside workdir; after a re-analysis the downloads may be long gone.
        workdir.mkdir(parents=True, exist_ok=True)

        # Posts pulled before frame extraction existed. Skipped without a session (reanalyze).
        if row["status"] == Status.TRANSCRIBED and self.loader is not None and needs_frames(workdir, row["kind"]):
            self._log(f"  {shortcode} fetching frames")
            hit_instagram = True
            try:
                with _instagram_errors():
                    frames = self._fetch_frames(self.loader, shortcode, workdir)
                self._log(f"  {shortcode} {len(frames)} frames")
            except (MediaError, InstaloaderException) as e:
                self._log(f"  {shortcode} frames skipped: {e}")  # optional context; analyze anyway

        if stop_after == Status.TRANSCRIBED:
            return hit_instagram

        if row["status"] == Status.TRANSCRIBED:
            self._log(f"  {shortcode} analyzing")
            try:
                images = _images_in(workdir, row["kind"])
                analysis = self.analyzer.analyze(row, row["transcript"], images, workdir, self.registry)
                usage = getattr(self.analyzer, "last_usage", {})
                report.add_usage(usage)
                self._log(f"  {shortcode} analyzed: {len(images)} images, {_tokens(usage)}")
            except ClaudeError as e:
                if e.stop_run:
                    raise StopRun(str(e)) from e
                raise
            self._save_analysis(row, analysis)
            row = self.state.get_post(shortcode)

        if row["status"] == Status.ANALYZED:
            analysis = json.loads(row["analysis"])
            if analysis.get("repo_hints") and row["enrichment"] is None and self.enricher:
                self._enrich(row, analysis, workdir, report)
                row = self.state.get_post(shortcode)
            self._write(row, report)

        return hit_instagram

    def _save_analysis(self, row, analysis: dict) -> None:
        category = self.registry.assign(analysis["category"], analysis.get("new_category_description"))
        if row["category"]:  # re-analysis: move the count off the old category
            self.registry.unassign(row["category"])
        self.registry.save()
        self.state.save_analysis(row["shortcode"], analysis, category.name)

    def _write(self, row, report: RunReport) -> None:
        shortcode, workdir = row["shortcode"], self.cfg.media_dir / row["shortcode"]
        analysis = json.loads(row["analysis"])
        resources = json.loads(row["enrichment"]) if row["enrichment"] else None
        if row["note_path"]:
            note = self.writer.rewrite(row["note_path"], row, analysis, workdir / "thumb.jpg", resources)
        else:
            note = self.writer.write(row, analysis, workdir / "thumb.jpg", resources)
        self.state.mark_written(shortcode, note.as_posix())
        if not self.cfg.keep_media:
            shutil.rmtree(workdir, ignore_errors=True)
        report.written.append((analysis["category"], analysis["title"]))
        self._log(f"  {shortcode} -> {note.as_posix()}")

    # ---- parallel Claude stages -------------------------------------------------

    def analyze_parallel(self, limit: int = 0, workers: int = 4, follow: bool = False,
                         poll: float = 60.0) -> RunReport:
        """Analyze (+ repo lookup) `workers` posts at a time; no Instagram or Groq.

        Takes transcribed posts whose frames are in place. With `follow`, waits for a
        running pull to make more posts ready until `limit` is reached or nothing is
        left upstream. Worker threads only call Claude; state, categories and the
        vault are touched from this thread alone.
        """
        report, started, running = RunReport(), set(), {}
        stop: str | None = None
        with ThreadPoolExecutor(max_workers=workers) as pool:
            while True:
                room = stop is None and (not limit or len(started) < limit)
                if room and len(running) < workers:
                    for row in self._ready(started, workers - len(running)):
                        if limit and len(started) >= limit:
                            break
                        started.add(row["shortcode"])
                        self._log(f"  {row['shortcode']} analyzing ({len(started)})")
                        running[pool.submit(self._claude_job, row, self.registry.snapshot())] = row
                if not running:
                    if not room or not follow or not self._upstream_left():
                        break
                    self._log(f"waiting {poll:.0f}s for the pull to make more posts ready")
                    self._sleep(poll)
                    continue
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in done:
                    row = running.pop(future)
                    try:
                        self._finish(row, future.result(), report)
                    except StopRun as e:
                        if stop is None:
                            stop = str(e)
                            self._log(f"stopping run (letting {len(running)} in-flight posts finish): {e}")
                    except (ClaudeError, OSError, KeyError) as e:
                        error = f"{type(e).__name__}: {e}"
                        permanent = self.state.record_failure(row["shortcode"], error)
                        report.failed.append((row["shortcode"], error))
                        self._log(f"  {row['shortcode']} failed{' permanently' if permanent else ''}: {error}")
        report.stopped = stop
        if report.written:
            self.writer.write_indexes(
                self.state.written(), {c.name: c.description for c in self.registry.categories}
            )
        return report

    def _ready(self, exclude: set[str], n: int) -> list:
        rows = []
        for row in self.state.work_queue():
            if len(rows) >= n:
                break
            if row["shortcode"] in exclude or row["status"] not in (Status.TRANSCRIBED, Status.ANALYZED):
                continue
            if row["status"] == Status.TRANSCRIBED and needs_frames(self.cfg.media_dir / row["shortcode"], row["kind"]):
                continue
            rows.append(row)
        return rows

    def _upstream_left(self) -> bool:
        return any(
            row["status"] in (Status.PENDING, Status.MEDIA)
            or (row["status"] == Status.TRANSCRIBED and needs_frames(self.cfg.media_dir / row["shortcode"], row["kind"]))
            for row in self.state.work_queue()
        )

    def _claude_job(self, row, registry: Registry) -> dict:
        """Runs in a worker thread: Claude calls only, results returned to the main thread."""
        workdir = self.cfg.media_dir / row["shortcode"]
        workdir.mkdir(parents=True, exist_ok=True)
        result: dict = {"analysis": None, "resources": None, "usage": [], "stop": None, "images": 0}
        if row["status"] == Status.TRANSCRIBED:
            analyzer = copy.copy(self.analyzer)  # own last_usage per thread
            images = _images_in(workdir, row["kind"])
            try:
                analysis = analyzer.analyze(row, row["transcript"], images, workdir, registry)
            except ClaudeError as e:
                if e.stop_run:
                    raise StopRun(str(e)) from e
                raise
            result.update(analysis=analysis, images=len(images))
            result["usage"].append(getattr(analyzer, "last_usage", {}))
        else:
            analysis = json.loads(row["analysis"])
        if analysis.get("repo_hints") and row["enrichment"] is None and self.enricher:
            enricher = copy.copy(self.enricher)
            try:
                result["resources"] = enricher.enrich(row, analysis, workdir)
                result["usage"].append(getattr(enricher, "last_usage", {}))
            except ClaudeError as e:
                if e.stop_run:
                    result["stop"] = str(e)  # keep the analysis; the lookup resumes next run
                else:
                    result["resources"] = _unresolved(analysis)
        return result

    def _finish(self, row, result: dict, report: RunReport) -> None:
        shortcode = row["shortcode"]
        for usage in result["usage"]:
            report.add_usage(usage)
        if result["analysis"] is not None:
            self._save_analysis(row, result["analysis"])
            self._log(f"  {shortcode} analyzed: {result['images']} images, "
                      f"{_tokens(result['usage'][0] if result['usage'] else {})}")
        if result["resources"] is not None:
            self.state.save_enrichment(shortcode, result["resources"])
        if result["stop"]:
            raise StopRun(result["stop"])
        self._write(self.state.get_post(shortcode), report)

    def rerender(self) -> int:
        """Rewrite every written note from state (no Instagram, Groq or Claude)."""
        rows = self.state.written()
        for row in rows:
            resources = json.loads(row["enrichment"]) if row["enrichment"] else None
            note = self.writer.rewrite(row["note_path"], row, json.loads(row["analysis"]), None, resources)
            if note.as_posix() != row["note_path"]:
                self.state.mark_written(row["shortcode"], note.as_posix())
                self._log(f"  {row['note_path']} -> {note.as_posix()}")
        if rows:
            self.writer.write_indexes(
                self.state.written(), {c.name: c.description for c in self.registry.categories}
            )
        return len(rows)

    def _enrich(self, row, analysis: dict, workdir: Path, report: RunReport) -> None:
        shortcode = row["shortcode"]
        self._log(f"  {shortcode} looking up {len(analysis['repo_hints'])} mentioned tool(s)")
        try:
            resources = self.enricher.enrich(row, analysis, workdir)
            report.add_usage(getattr(self.enricher, "last_usage", {}))
        except ClaudeError as e:
            if e.stop_run:
                raise StopRun(str(e)) from e
            # Not worth losing the note over: write it with the hints unresolved.
            self._log(f"  {shortcode} lookup failed: {e}")
            resources = _unresolved(analysis)
        self.state.save_enrichment(shortcode, resources)


def _unresolved(analysis: dict) -> list[dict]:
    return [
        {"hint": h["query"], "found": False, "name": h["query"], "url": None,
         "what_it_is": h["evidence"], "install": None, "confidence": "low"}
        for h in analysis["repo_hints"]
    ]


def _images_in(workdir: Path, kind: str) -> list[Path]:
    thumb = workdir / "thumb.jpg"
    images = sorted(workdir.glob("img*.jpg"))
    frames = sorted(workdir.glob("frame*.jpg"))
    # The cover often carries the title text; carousels whose images are gone
    # (older runs deleted them) fall back to it too.
    if thumb.is_file() and (kind != "carousel" or not images):
        images.insert(0, thumb)
    return images + frames


def _tokens(usage: dict) -> str:
    if not usage:
        return "no usage reported"
    total_in = sum(usage.get(k, 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    return f"{total_in / 1000:.1f}k in / {usage.get('output_tokens', 0) / 1000:.1f}k out, {usage.get('seconds', 0):.0f}s"


@contextmanager
def _instagram_errors():
    try:
        yield
    except (LoginRequiredException, ConnectionException) as e:
        if _is_auth_problem(e):
            raise StopRun(f"Instagram session problem: {e}") from e
        raise


def _is_auth_problem(e: Exception) -> bool:
    if isinstance(e, LoginRequiredException):
        return True
    text = str(e).lower()
    return "401" in text or "login" in text or "checkpoint" in text
