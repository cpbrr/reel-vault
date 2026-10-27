"""Per-post stages: media + transcribe -> analyze -> enrich -> write.

Each stage persists its result in state.db before the next starts, so a crash
or a stopped run resumes where it left off. Downloaded files live in
data/media/<shortcode>/ and are deleted once the note is written (or the post
fails for good) unless storage.keep_media is on.
"""

from __future__ import annotations

import json
import random
import shutil
import time
from collections.abc import Callable
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
from reel_vault.media import MediaError, fetch_media
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
        self._sleep = sleep
        self._log = log

    def process(self, limit: int = 0, only: list[str] | None = None) -> RunReport:
        report = RunReport()
        if only:
            queue = [row for row in self.state.work_queue() if row["shortcode"] in only][: limit or None]
        else:
            queue = self.state.work_queue(limit)
        self._log(f"{len(queue)} posts to process")

        for i, row in enumerate(queue):
            shortcode = row["shortcode"]
            try:
                hit_instagram = self._process_one(shortcode, report)
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

    def _process_one(self, shortcode: str, report: RunReport) -> bool:
        """Advance one post as far as possible. Returns True if Instagram was contacted."""
        workdir = self.cfg.media_dir / shortcode
        hit_instagram = False
        row = self.state.get_post(shortcode)

        if row["status"] in (Status.PENDING, Status.MEDIA):
            self._log(f"  {shortcode} downloading")
            hit_instagram = True
            try:
                media = self._fetch_media(self.loader, shortcode, workdir, keep_video=self.cfg.keep_media)
            except (LoginRequiredException, ConnectionException) as e:
                if _is_auth_problem(e):
                    raise StopRun(f"Instagram session problem: {e}") from e
                raise
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

        if row["status"] == Status.TRANSCRIBED:
            self._log(f"  {shortcode} analyzing")
            try:
                analysis = self.analyzer.analyze(
                    row, row["transcript"], _images_in(workdir, row["kind"]), workdir, self.registry
                )
            except ClaudeError as e:
                if e.stop_run:
                    raise StopRun(str(e)) from e
                raise
            category = self.registry.assign(analysis["category"], analysis.get("new_category_description"))
            self.registry.save()
            self.state.save_analysis(shortcode, analysis, category.name)
            row = self.state.get_post(shortcode)

        if row["status"] == Status.ANALYZED:
            analysis = json.loads(row["analysis"])
            if analysis.get("repo_hints") and row["enrichment"] is None and self.enricher:
                self._enrich(row, analysis, workdir)
                row = self.state.get_post(shortcode)
            resources = json.loads(row["enrichment"]) if row["enrichment"] else None
            note = self.writer.write(row, analysis, workdir / "thumb.jpg", resources)
            self.state.mark_written(shortcode, note.as_posix())
            if not self.cfg.keep_media:
                shutil.rmtree(workdir, ignore_errors=True)
            report.written.append((analysis["category"], analysis["title"]))
            self._log(f"  {shortcode} -> {note.as_posix()}")

        return hit_instagram

    def _enrich(self, row, analysis: dict, workdir: Path) -> None:
        shortcode = row["shortcode"]
        self._log(f"  {shortcode} looking up {len(analysis['repo_hints'])} mentioned tool(s)")
        try:
            resources = self.enricher.enrich(row, analysis, workdir)
        except ClaudeError as e:
            if e.stop_run:
                raise StopRun(str(e)) from e
            # Not worth losing the note over: write it with the hints unresolved.
            self._log(f"  {shortcode} lookup failed: {e}")
            resources = [
                {"hint": h["query"], "found": False, "name": h["query"], "url": None,
                 "what_it_is": h["evidence"], "install": None, "confidence": "low"}
                for h in analysis["repo_hints"]
            ]
        self.state.save_enrichment(shortcode, resources)


def _images_in(workdir: Path, kind: str) -> list[Path]:
    images = sorted(workdir.glob("img*.jpg"))
    if not images and kind == "image" and (workdir / "thumb.jpg").is_file():
        images = [workdir / "thumb.jpg"]
    return images


def _is_auth_problem(e: Exception) -> bool:
    if isinstance(e, LoginRequiredException):
        return True
    text = str(e).lower()
    return "401" in text or "login" in text or "checkpoint" in text
