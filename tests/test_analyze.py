import json
from datetime import datetime, timezone

import pytest

from reel_vault.analyze.analyzer import build_prompt, normalize, tag_slug
from reel_vault.analyze.claude import ClaudeError, child_env, parse_output


def test_child_env_drops_parent_session_vars():
    env = child_env({
        "PATH": "p", "CLAUDE_CODE_SIMPLE": "1", "ANTHROPIC_BASE_URL": "http://proxy",
        "CLAUDECODE": "1", "CLAUDE_CODE_OAUTH_TOKEN": "t", "ANTHROPIC_API_KEY": "k",
    })
    assert env == {"PATH": "p", "CLAUDE_CODE_OAUTH_TOKEN": "t", "ANTHROPIC_API_KEY": "k"}
from reel_vault.categories import Registry
from reel_vault.models import SavedPost
from reel_vault.state import State


def test_parse_structured_output():
    out = json.dumps({"is_error": False, "structured_output": {"category": "X"}, "result": ""})
    assert parse_output(out) == {"category": "X"}


def test_parse_falls_back_to_fenced_result():
    out = json.dumps({"is_error": False, "result": '```json\n{"category": "Y"}\n```'})
    assert parse_output(out) == {"category": "Y"}


def test_auth_error_stops_run():
    out = json.dumps({"is_error": True, "result": "Failed to authenticate: OAuth session expired"})
    with pytest.raises(ClaudeError) as exc:
        parse_output(out)
    assert exc.value.stop_run


def test_garbage_output_is_post_level_error():
    with pytest.raises(ClaudeError) as exc:
        parse_output("not json", "boom", 1)
    assert not exc.value.stop_run


def test_registry_assign_and_find(tmp_path):
    reg = Registry(tmp_path / "c.json")
    reg.assign("Climbing", "Climbing videos")
    reg.assign("climbing", None)
    reg.save()
    reloaded = Registry(tmp_path / "c.json")
    assert reloaded.find("CLIMBING").count == 2
    assert "Climbing: Climbing videos (2 notes)" in reloaded.prompt_listing()


def test_normalize_snaps_to_existing_category_and_cleans_tags(tmp_path):
    reg = Registry(tmp_path / "c.json")
    reg.assign("Claude Skills", "Claude Code skills and plugins")
    result = normalize(
        {"category": "claude skills", "new_category_description": "dup", "is_meme": False,
         "tags": ["#Claude Code", "claude-code", "MCP!"], "keywords": [" a ", "a", ""],
         "key_points": ["x"]},
        reg,
    )
    assert result["category"] == "Claude Skills"
    assert result["new_category_description"] is None
    assert result["tags"] == ["claude-code", "mcp"]
    assert result["keywords"] == ["a"]


def test_normalize_memes():
    result = normalize(
        {"category": "Funny", "is_meme": True, "key_points": ["x"], "tags": [], "keywords": []},
        _empty_registry(),
    )
    assert result["category"] == "Memes"
    assert result["key_points"] == []


def _empty_registry():
    reg = Registry.__new__(Registry)
    reg.categories = []
    return reg


def test_enrich_clean_drops_low_confidence_links():
    from reel_vault.analyze.enricher import clean

    out = clean([
        {"found": True, "url": "https://a", "confidence": "low"},
        {"found": True, "url": None, "confidence": "high"},
        {"found": True, "url": "https://b", "confidence": "medium"},
    ])
    assert [(r["found"], r["url"]) for r in out] == [(False, None), (False, None), (True, "https://b")]


def test_tag_slug():
    assert tag_slug(" Kilter Board ") == "kilter-board"


def test_build_prompt_includes_post_and_images(tmp_path):
    with State(tmp_path / "s.db") as state:
        state.add_post(SavedPost("ABC", "carousel", "chef", datetime(2026, 5, 2, tzinfo=timezone.utc),
                                 "Pho recipe $5", ["pho"]))
        prompt = build_prompt(state.get_post("ABC"), None, [tmp_path / "img00.jpg"], _empty_registry())
    assert "@chef" in prompt
    assert "Pho recipe $5" in prompt
    assert "(no speech)" in prompt
    assert "img00.jpg" in prompt
    assert "https://www.instagram.com/p/ABC/" in prompt


def test_normalize_related_categories_existing_only(tmp_path):
    reg = Registry(tmp_path / "c.json")
    for name in ("AI Tools", "Career", "Climbing", "Recipes"):
        reg.assign(name, name)
    result = normalize(
        {"category": "AI Tools", "related_categories": ["career", "AI Tools", "Made Up", "Career",
                                                        "Climbing", "Recipes"],
         "is_meme": False, "tags": [], "keywords": [], "key_points": []},
        reg,
    )
    assert result["related_categories"] == ["Career", "Climbing"]


def test_normalize_memes_have_no_related(tmp_path):
    reg = Registry(tmp_path / "c.json")
    reg.assign("Games", "Games")
    result = normalize(
        {"category": "Memes", "related_categories": ["Games"], "is_meme": True,
         "tags": [], "keywords": [], "key_points": []},
        reg,
    )
    assert result["related_categories"] == []
