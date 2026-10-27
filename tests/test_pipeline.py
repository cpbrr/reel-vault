import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from reel_vault.analyze.claude import ClaudeError
from reel_vault.categories import Registry
from reel_vault.config import load_config
from reel_vault.media import Media
from reel_vault.models import SavedPost
from reel_vault.pipeline import Pipeline
from reel_vault.state import State, Status
from reel_vault.transcribe.base import RateLimited, Transcript


def analysis(category="Job Hunting", title="Answer tell me about yourself", **extra):
    base = {
        "category": category,
        "new_category_description": "Job search and interview tips",
        "is_meme": False,
        "title": title,
        "summary": "Three-part answer.",
        "key_points": ["Present, past, future"],
        "tags": ["interview"],
        "keywords": ["job hunting"],
        "language": "en",
        "repo_hints": [],
    }
    return base | extra


class FakeTranscriber:
    def __init__(self, error=None):
        self.error = error
        self.calls = 0

    def transcribe(self, audio):
        self.calls += 1
        if self.error:
            raise self.error
        return Transcript("hello there", "en")


class FakeAnalyzer:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def analyze(self, post, transcript, images, workdir, registry):
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def fake_fetch_media(loader, shortcode, workdir, keep_video=False):
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "thumb.jpg").write_bytes(b"jpg")
    audio = workdir / "audio.mp3"
    audio.write_bytes(b"mp3")
    return Media(thumbnail=workdir / "thumb.jpg", owner="filled-later", audio=audio)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("REEL_VAULT_HOME", str(tmp_path))
    cfg = load_config("local")
    state = State(cfg.paths.state_db)
    for i, sc in enumerate(["AAA", "BBB"]):
        state.add_post(SavedPost(sc, "video", f"owner{i}", datetime(2026, 10, 1 + i, tzinfo=timezone.utc), "cap"))
    yield cfg, state
    state.close()


def make_pipeline(cfg, state, transcriber, analyzer):
    return Pipeline(
        cfg, state, loader=None, transcriber=transcriber, analyzer=analyzer,
        fetch_media=fake_fetch_media, sleep=lambda s: None, log=lambda m: None,
    )


def test_happy_path_writes_notes_indexes_and_cleans_up(env):
    cfg, state = env
    analyzer = FakeAnalyzer([analysis(), analysis("Climbing", "Kilter V7 beta")])
    report = make_pipeline(cfg, state, FakeTranscriber(), analyzer).process()

    assert [c for c, _ in report.written] == ["Job Hunting", "Climbing"]
    assert state.counts() == {Status.WRITTEN: 2}
    note = cfg.paths.vault_dir / state.get_post("AAA")["note_path"]
    assert note.is_file()
    assert "hello there" in note.read_text(encoding="utf-8")
    assert (cfg.paths.vault_dir / "_attachments" / "AAA.jpg").is_file()
    assert (cfg.paths.vault_dir / "_index" / "Reels - Climbing.md").is_file()
    assert (cfg.paths.vault_dir / "_index" / "Saved Reels Home.md").is_file()
    assert not (cfg.media_dir / "AAA").exists()  # downloads deleted, results kept in state
    assert state.get_post("AAA")["transcript"] == "hello there"

    registry = Registry(cfg.categories_file)
    assert registry.find("job hunting").count == 1


def test_keep_media_keeps_downloads_and_owner_backfilled(env):
    cfg, state = env
    from dataclasses import replace

    cfg = replace(cfg, keep_media=True)
    state._update("AAA", owner="")
    make_pipeline(cfg, state, FakeTranscriber(), FakeAnalyzer([analysis()])).process(limit=1)
    assert (cfg.media_dir / "AAA" / "audio.mp3").is_file()
    assert state.get_post("AAA")["owner"] == "filled-later"


def test_rate_limit_stops_run_without_counting_failure(env):
    cfg, state = env
    report = make_pipeline(cfg, state, FakeTranscriber(RateLimited("slow down")), FakeAnalyzer([])).process()
    assert report.stopped == "slow down"
    assert state.get_post("AAA")["attempts"] == 0
    assert state.get_post("AAA")["status"] == Status.PENDING


def test_analyze_failure_resumes_without_redownloading(env):
    cfg, state = env
    transcriber = FakeTranscriber()
    pipeline = make_pipeline(cfg, state, transcriber, FakeAnalyzer([ClaudeError("bad json"), analysis(title="B")]))
    report = pipeline.process()
    assert report.failed[0][0] == "AAA"
    assert state.get_post("AAA")["status"] == Status.TRANSCRIBED
    assert state.get_post("AAA")["attempts"] == 1

    pipeline.analyzer = FakeAnalyzer([analysis(title="A")])
    report = pipeline.process()
    assert report.written == [("Job Hunting", "A")]
    assert transcriber.calls == 2  # one per post, none for the retry


def test_claude_stop_run_error_stops(env):
    cfg, state = env
    err = ClaudeError("OAuth session expired", stop_run=True)
    report = make_pipeline(cfg, state, FakeTranscriber(), FakeAnalyzer([err])).process()
    assert "OAuth" in report.stopped
    assert state.get_post("AAA")["attempts"] == 0


def test_three_failures_mark_failed_and_retry_resets(env):
    cfg, state = env
    pipeline = make_pipeline(cfg, state, FakeTranscriber(), None)
    for _ in range(3):
        pipeline.analyzer = FakeAnalyzer([ClaudeError("x"), ClaudeError("x")])
        pipeline.process()
    assert state.counts() == {Status.FAILED: 2}
    assert state.retry_failed() == 2
    assert state.counts() == {Status.PENDING: 2}


class FakeEnricher:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def enrich(self, post, analysis, workdir):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


HINTS = {"repo_hints": [{"query": "find-skills", "evidence": "comment SKILLS"}]}


def test_enrichment_runs_only_with_hints_and_lands_in_note(env):
    cfg, state = env
    found = [{"hint": "find-skills", "found": True, "name": "vercel-labs/skills",
              "url": "https://github.com/vercel-labs/skills", "what_it_is": "Skills.",
              "install": None, "confidence": "high"}]
    enricher = FakeEnricher(found)
    pipeline = make_pipeline(cfg, state, FakeTranscriber(), FakeAnalyzer([analysis(**HINTS), analysis(title="B")]))
    pipeline.enricher = enricher
    pipeline.process()
    assert enricher.calls == 1
    note = (cfg.paths.vault_dir / state.get_post("AAA")["note_path"]).read_text(encoding="utf-8")
    assert "https://github.com/vercel-labs/skills" in note


def test_enrichment_failure_still_writes_note_unresolved(env):
    cfg, state = env
    pipeline = make_pipeline(cfg, state, FakeTranscriber(), FakeAnalyzer([analysis(**HINTS)]))
    pipeline.enricher = FakeEnricher(ClaudeError("bad output"))
    report = pipeline.process(limit=1)
    assert len(report.written) == 1
    note = (cfg.paths.vault_dir / state.get_post("AAA")["note_path"]).read_text(encoding="utf-8")
    assert "repo-hint-unresolved" in note


def test_enrichment_usage_limit_stops_and_resumes_at_enrich(env):
    cfg, state = env
    pipeline = make_pipeline(cfg, state, FakeTranscriber(), FakeAnalyzer([analysis(**HINTS)]))
    pipeline.enricher = FakeEnricher(ClaudeError("usage limit reached", stop_run=True))
    report = pipeline.process(limit=1)
    assert report.stopped
    assert state.get_post("AAA")["status"] == Status.ANALYZED
    pipeline.enricher = FakeEnricher([])
    assert len(pipeline.process(limit=1).written) == 1


def test_only(env):
    cfg, state = env
    report = make_pipeline(cfg, state, FakeTranscriber(), FakeAnalyzer([analysis()])).process(only=["BBB"])
    assert len(report.written) == 1
    assert state.get_post("BBB")["status"] == Status.WRITTEN
    assert state.get_post("AAA")["status"] == Status.PENDING


def test_limit(env):
    cfg, state = env
    report = make_pipeline(cfg, state, FakeTranscriber(), FakeAnalyzer([analysis()])).process(limit=1)
    assert len(report.written) == 1
    assert state.counts() == {Status.WRITTEN: 1, Status.PENDING: 1}
