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
        assert workdir.is_dir()  # claude -p runs inside it
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def fake_fetch_media(loader, shortcode, workdir, keep_video=False):
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "thumb.jpg").write_bytes(b"jpg")
    (workdir / "frames.done").touch()  # real fetch_media extracts frames itself
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


def test_stop_after_transcribed_skips_claude(env):
    cfg, state = env
    analyzer = FakeAnalyzer([])
    report = make_pipeline(cfg, state, FakeTranscriber(), analyzer).process(stop_after=Status.TRANSCRIBED)
    assert analyzer.calls == 0 and not report.written
    assert state.counts() == {Status.TRANSCRIBED: 2}
    assert (cfg.media_dir / "AAA" / "audio.mp3").is_file()  # kept until analyzed


def test_reanalyze_rewrites_in_place_keeping_my_notes(env):
    cfg, state = env
    transcriber = FakeTranscriber()
    pipeline = make_pipeline(cfg, state, transcriber, FakeAnalyzer([analysis(), analysis(title="B")]))
    pipeline.process()
    old = cfg.paths.vault_dir / state.get_post("AAA")["note_path"]
    old.write_text(old.read_text(encoding="utf-8") + "my own thoughts\n", encoding="utf-8")

    assert state.reset_for_reanalysis(["AAA"]) == ["AAA"]
    pipeline.analyzer = FakeAnalyzer([analysis("Career", "New title", related_categories=[])])
    report = pipeline.process(only=["AAA"])

    assert report.written == [("Career", "New title")]
    assert transcriber.calls == 2  # no new download or transcription
    new = cfg.paths.vault_dir / state.get_post("AAA")["note_path"]
    assert new.as_posix().endswith("Career/New title 01-10-2026.md")
    assert not old.exists()
    text = new.read_text(encoding="utf-8")
    assert "# New title" in text and text.rstrip().endswith("my own thoughts")
    assert f"![[AAA.jpg|300]]" in text  # thumbnail reused from the vault
    registry = Registry(cfg.categories_file)
    assert registry.find("Career").count == 1
    assert registry.find("Job Hunting").count == 1  # BBB still there


def test_rerender_renames_without_calling_anything(env):
    cfg, state = env
    pipeline = make_pipeline(cfg, state, FakeTranscriber(), FakeAnalyzer([analysis(), analysis(title="B")]))
    pipeline.process()
    note = cfg.paths.vault_dir / state.get_post("AAA")["note_path"]
    old = note.with_name("2026-10-01 Old style name.md")
    note.rename(old)
    old.write_text(old.read_text(encoding="utf-8") + "keep me\n", encoding="utf-8")
    state.mark_written("AAA", old.relative_to(cfg.paths.vault_dir).as_posix())

    pipeline = make_pipeline(cfg, state, None, None)  # fresh writer; nothing may be called
    assert pipeline.rerender() == 2
    new = cfg.paths.vault_dir / state.get_post("AAA")["note_path"]
    assert new.name == "Answer tell me about yourself 01-10-2026.md"
    assert not old.exists()
    assert new.read_text(encoding="utf-8").rstrip().endswith("keep me")
    index = (cfg.paths.vault_dir / "_index" / "Reels - Job Hunting.md").read_text(encoding="utf-8")
    assert "[[Answer tell me about yourself 01-10-2026|" in index


def test_pull_backfills_frames_for_already_transcribed_videos(env):
    cfg, state = env
    from reel_vault.media import FRAMES_DONE

    calls = []

    def fake_frames(loader, shortcode, workdir):
        calls.append(shortcode)
        (workdir / FRAMES_DONE).touch()
        frame = workdir / "frame00_3s.jpg"
        frame.write_bytes(b"jpg")
        return [frame]

    state.save_transcript("AAA", "old pull", "en")  # pulled before frames existed
    pipeline = make_pipeline(cfg, state, FakeTranscriber(), None)
    pipeline.loader = object()
    pipeline._fetch_frames = fake_frames
    pipeline.process(stop_after=Status.TRANSCRIBED)
    assert calls == ["AAA"]  # BBB was downloaded fresh, frames included
    assert state.get_post("AAA")["transcript"] == "old pull"  # not re-transcribed
    assert (cfg.media_dir / "AAA" / "frame00_3s.jpg").is_file()

    calls.clear()
    pipeline.process(stop_after=Status.TRANSCRIBED)
    assert calls == []  # nothing left to do


def test_images_in_order(tmp_path):
    from reel_vault.pipeline import _images_in

    for name in ("thumb.jpg", "img00.jpg", "img01.jpg", "frame01_9s.jpg", "frame00_3s.jpg"):
        (tmp_path / name).write_bytes(b"x")
    names = lambda kind: [p.name for p in _images_in(tmp_path, kind)]
    assert names("carousel") == ["img00.jpg", "img01.jpg", "frame00_3s.jpg", "frame01_9s.jpg"]
    assert names("video") == ["thumb.jpg", "img00.jpg", "img01.jpg", "frame00_3s.jpg", "frame01_9s.jpg"]


def _ready(cfg, state, shortcode, frames=True):
    state.save_transcript(shortcode, "hello there", "en")
    workdir = cfg.media_dir / shortcode
    workdir.mkdir(parents=True, exist_ok=True)
    if frames:
        (workdir / "frames.done").touch()


def test_analyze_parallel_writes_all(env):
    cfg, state = env
    _ready(cfg, state, "AAA")
    _ready(cfg, state, "BBB")
    pipeline = make_pipeline(cfg, state, None, FakeAnalyzer([analysis(title="A"), analysis(title="B")]))
    report = pipeline.analyze_parallel(workers=2)
    assert sorted(t for _, t in report.written) == ["A", "B"]
    assert state.counts() == {Status.WRITTEN: 2}
    assert Registry(cfg.categories_file).find("Job Hunting").count == 2
    assert (cfg.paths.vault_dir / "_index" / "Reels - Job Hunting.md").is_file()


def test_analyze_parallel_skips_posts_without_frames_and_follows(env):
    cfg, state = env
    _ready(cfg, state, "AAA")
    _ready(cfg, state, "BBB", frames=False)  # the pull hasn't fetched its frames yet
    pipeline = make_pipeline(cfg, state, None, FakeAnalyzer([analysis(title="A"), analysis(title="B")]))

    assert len(pipeline.analyze_parallel(workers=2).written) == 1  # no follow: BBB left alone
    assert state.get_post("BBB")["status"] == Status.TRANSCRIBED

    waits = []
    def pull_finishes(seconds):
        waits.append(seconds)
        (cfg.media_dir / "BBB" / "frames.done").touch()
    pipeline._sleep = pull_finishes
    assert len(pipeline.analyze_parallel(workers=2, follow=True).written) == 1
    assert waits == [60.0]
    assert state.counts() == {Status.WRITTEN: 2}


def test_analyze_parallel_stop_run_keeps_posts(env):
    cfg, state = env
    _ready(cfg, state, "AAA")
    _ready(cfg, state, "BBB")
    err = ClaudeError("usage limit reached", stop_run=True)
    pipeline = make_pipeline(cfg, state, None, FakeAnalyzer([err, err]))
    report = pipeline.analyze_parallel(workers=1)
    assert "usage limit" in report.stopped and not report.failed
    assert state.get_post("AAA")["attempts"] == 0
    assert state.counts() == {Status.TRANSCRIBED: 2}


def test_analyze_parallel_limit_and_enrichment(env):
    cfg, state = env
    _ready(cfg, state, "AAA")
    _ready(cfg, state, "BBB")
    found = [{"hint": "x", "found": True, "name": "o/x", "url": "https://github.com/o/x",
              "what_it_is": "X.", "install": None, "confidence": "high"}]
    pipeline = make_pipeline(cfg, state, None, FakeAnalyzer([analysis(**HINTS)]))
    pipeline.enricher = FakeEnricher(found)
    report = pipeline.analyze_parallel(limit=1, workers=4)
    assert len(report.written) == 1
    note = (cfg.paths.vault_dir / state.get_post("AAA")["note_path"]).read_text(encoding="utf-8")
    assert "https://github.com/o/x" in note


def test_categories_merge_relabels_moves_and_reindexes(env, tmp_path):
    cfg, state = env
    pipeline = make_pipeline(cfg, state, FakeTranscriber(), FakeAnalyzer([
        analysis("Job Hunting", "A"),
        analysis("Climbing", "B", related_categories=["Job Hunting"]),
    ]))
    pipeline.process()
    old_note = cfg.paths.vault_dir / state.get_post("AAA")["note_path"]
    old_note.write_text(old_note.read_text(encoding="utf-8") + "mine\n", encoding="utf-8")

    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"Career & Skills": {"description": "Jobs and skills", "from": ["Job Hunting"]}}),
                    encoding="utf-8")
    from reel_vault.cli import main
    assert main(["categories", "merge", str(plan)]) == 0

    a, b = state.get_post("AAA"), state.get_post("BBB")
    assert a["category"] == "Career & Skills" and a["note_path"].startswith("Career & Skills/")
    assert json.loads(b["analysis"])["related_categories"] == ["Career & Skills"]
    assert not old_note.exists()
    assert (cfg.paths.vault_dir / a["note_path"]).read_text(encoding="utf-8").rstrip().endswith("mine")
    assert not (cfg.paths.vault_dir / "Job Hunting").exists()
    registry = Registry(cfg.categories_file)
    assert registry.find("Job Hunting") is None
    assert registry.find("Career & Skills").count == 1 and registry.find("Climbing").count == 1
    home = (cfg.paths.vault_dir / "_index" / "Saved Reels Home.md").read_text(encoding="utf-8")
    assert "Career & Skills]] (1 +1 related): Jobs and skills" in home
