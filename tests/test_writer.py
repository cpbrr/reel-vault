import json
from datetime import datetime, timezone

import pytest

from reel_vault.models import SavedPost
from reel_vault.state import State
from reel_vault.vault.writer import VaultWriter, render_note, safe_name


@pytest.fixture
def post(tmp_path):
    with State(tmp_path / "s.db") as state:
        state.add_post(SavedPost("ABC", "video", "creator", datetime(2026, 9, 14, tzinfo=timezone.utc),
                                 'Line one: "quoted"\n\nLine three', ["career"]))
        state.save_transcript("ABC", "spoken words", "en")
        yield state.get_post("ABC")


ANALYSIS = {
    "category": "Job Hunting",
    "is_meme": False,
    "title": 'Answer "tell me about yourself": 3 parts?',
    "summary": "Summary.",
    "key_points": ["One", "Two"],
    "tags": ["interview"],
    "keywords": ["job hunting: prep"],
    "language": "en",
    "repo_hints": [],
}


def test_safe_name_strips_link_and_path_chars():
    assert safe_name('a/b\\c: "d" [e] #f | g?') == "a b c d e f g"
    assert safe_name("...") == "Untitled"


def test_render_note_frontmatter_and_sections(post):
    text = render_note(post, ANALYSIS, "ABC.jpg", "2026-10-28")
    front = text.split("---")[1]
    assert 'tags: ["saved-reel", "interview"]' in front
    assert 'keywords: ["job hunting: prep"]' in front
    assert 'url: "https://www.instagram.com/reel/ABC/"' in front
    assert "![[ABC.jpg|300]]" in text
    assert "- One" in text
    assert '> Line one: "quoted"\n>\n> Line three' in text
    assert "## Transcript\nspoken words" in text
    assert text.rstrip().endswith("## My notes")


def test_resources_and_unresolved(post):
    resources = [
        {"hint": "a", "found": True, "name": "vercel-labs/skills", "url": "https://github.com/vercel-labs/skills",
         "what_it_is": "Skills CLI.", "install": "npx skills add vercel-labs/skills", "confidence": "high"},
        {"hint": "b", "found": False, "name": "Mystery tool", "url": None,
         "what_it_is": "Gated link.", "install": None, "confidence": "low"},
    ]
    text = render_note(post, ANALYSIS | {"repo_hints": [{"query": "a", "evidence": "e"}]}, None, "d", resources)
    assert "- [vercel-labs/skills](https://github.com/vercel-labs/skills): Skills CLI." in text
    assert "  - Install: `npx skills add vercel-labs/skills`" in text
    assert "## Mentioned tools (link not found)\n- Mystery tool: Gated link." in text
    assert '"repo-hint-unresolved"' in text.split("---")[1]


def test_all_resolved_has_no_unresolved_tag(post):
    resources = [{"hint": "a", "found": True, "name": "x", "url": "https://x", "what_it_is": "X.",
                  "install": None, "confidence": "high"}]
    text = render_note(post, ANALYSIS, None, "d", resources)
    assert "repo-hint-unresolved" not in text
    assert "Install:" not in text


def test_music_transcript_is_dropped(post):
    text = render_note(post, ANALYSIS | {"transcript_is_speech": False}, None, "2026-10-28")
    assert "## Transcript" not in text


def test_write_is_unique_and_never_overwrites(tmp_path, post):
    vault = tmp_path / "vault"
    thumb = tmp_path / "thumb.jpg"
    thumb.write_bytes(b"jpg")
    writer = VaultWriter(vault)
    first = writer.write(post, ANALYSIS, thumb)
    assert first.as_posix() == "Job Hunting/2026-09-14 Answer tell me about yourself 3 parts.md"
    second = writer.write(post, ANALYSIS, thumb)
    assert second.name == "2026-09-14 Answer tell me about yourself 3 parts (ABC).md"
    # A fresh writer sees existing files too.
    third = VaultWriter(vault).write(post, ANALYSIS | {"title": "Answer tell me about yourself 3 parts"}, None)
    assert third.name == "2026-09-14 Answer tell me about yourself 3 parts (ABC 2).md"


def test_indexes(tmp_path, post):
    vault = tmp_path / "vault"
    writer = VaultWriter(vault)
    note = writer.write(post, ANALYSIS, None)
    row = dict(post) | {"category": "Job Hunting", "note_path": note.as_posix(), "analysis": json.dumps(ANALYSIS)}
    writer.write_indexes([row], {"Job Hunting": "Interview tips"})
    index = (vault / "_index" / "Reels - Job Hunting.md").read_text(encoding="utf-8")
    assert f"[[{note.stem}|{ANALYSIS['title']}]]" in index
    home = (vault / "_index" / "Saved Reels Home.md").read_text(encoding="utf-8")
    assert "[[Reels - Job Hunting|Job Hunting]] (1): Interview tips" in home


def test_related_category_index(tmp_path, post):
    vault = tmp_path / "vault"
    writer = VaultWriter(vault)
    analysis = ANALYSIS | {"category": "AI Tools", "related_categories": ["Career"]}
    note = writer.write(post, analysis, None)
    text = (vault / note).read_text(encoding="utf-8")
    assert 'related_categories: ["Career"]' in text
    rows = [
        dict(post) | {"category": "AI Tools", "note_path": note.as_posix(), "analysis": json.dumps(analysis)},
        dict(post) | {"shortcode": "XYZ", "category": "Career", "note_path": "Career/Other.md",
                      "analysis": json.dumps(ANALYSIS | {"title": "Other"})},
    ]
    writer.write_indexes(rows, {"AI Tools": "AI", "Career": "Jobs"})
    career = (vault / "_index" / "Reels - Career.md").read_text(encoding="utf-8")
    assert "· 1 reels" in career
    assert career.index("[[Other|Other]]") < career.index("## Also related") < career.index(note.stem)
    home = (vault / "_index" / "Saved Reels Home.md").read_text(encoding="utf-8")
    assert "[[Reels - Career|Career]] (1 +1 related): Jobs" in home
    assert "[[Reels - AI Tools|AI Tools]] (1): AI" in home
