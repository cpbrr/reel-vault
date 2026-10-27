from __future__ import annotations

import json
import re
from importlib import resources
from pathlib import Path
from string import Template

from reel_vault.analyze.claude import run_claude
from reel_vault.categories import MEMES, Registry

MAX_RELATED = 2

SYSTEM_PROMPT = (
    "You write concise, accurate notes about saved social media posts for a personal "
    "knowledge base. Only state what the post actually contains. Respond with the "
    "structured output only."
)

ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string"},
        "new_category_description": {"type": ["string", "null"]},
        "related_categories": {"type": "array", "items": {"type": "string"}},
        "is_meme": {"type": "boolean"},
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "tags": {"type": "array", "items": {"type": "string"}},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "language": {"type": "string"},
        "transcript_is_speech": {"type": "boolean"},
        "repo_hints": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"query": {"type": "string"}, "evidence": {"type": "string"}},
                "required": ["query", "evidence"],
            },
        },
    },
    "required": [
        "category", "new_category_description", "related_categories", "is_meme", "title", "summary",
        "key_points", "tags", "keywords", "language", "transcript_is_speech", "repo_hints",
    ],
}


def build_prompt(post, transcript: str | None, images: list[Path], registry: Registry) -> str:
    template = Template(
        (resources.files("reel_vault.analyze.prompts") / "analyze.md").read_text(encoding="utf-8")
    )
    image_section = ""
    if images:
        listing = "\n".join(f"- {p.name}" for p in images)
        image_section = (
            "\n### Images\n\nThis post's images are in the current directory. "
            f"Read each one with the Read tool before answering:\n{listing}\n"
        )
    return template.substitute(
        categories=registry.prompt_listing(),
        owner=post["owner"],
        kind=post["kind"],
        posted=(post["posted_at"] or "")[:10],
        url=_post_url(post),
        hashtags=", ".join(json.loads(post["hashtags"] or "[]")) or "(none)",
        caption=post["caption"] or "(no caption)",
        transcript=transcript or "(no speech)",
        images=image_section,
    )


def normalize(analysis: dict, registry: Registry) -> dict:
    """Make Claude's answer consistent with the registry and note conventions."""
    if analysis.get("is_meme"):
        analysis["category"] = MEMES
        analysis["key_points"] = []
    existing = registry.find(analysis["category"])
    if existing:
        analysis["category"] = existing.name
        analysis["new_category_description"] = None
    analysis["related_categories"] = _related(analysis, registry)
    analysis["tags"] = _unique(slug for t in analysis.get("tags", []) if (slug := tag_slug(t)))
    analysis["keywords"] = _unique(k.strip() for k in analysis.get("keywords", []) if k.strip())
    return analysis


def _related(analysis: dict, registry: Registry) -> list[str]:
    """Existing categories only (a related page must already exist), never the primary."""
    if analysis.get("is_meme"):
        return []
    primary = analysis["category"].casefold()
    names = (c.name for n in analysis.get("related_categories") or [] if (c := registry.find(n)))
    return _unique(n for n in names if n.casefold() != primary)[:MAX_RELATED]


def tag_slug(tag: str) -> str:
    # Obsidian tags: no spaces; letters, digits, -, _ and / only.
    tag = tag.strip().lstrip("#").lower().replace(" ", "-")
    return re.sub(r"[^\w\-/]", "", tag)


def _unique(items) -> list[str]:
    seen: dict[str, None] = {}
    for item in items:
        seen.setdefault(item, None)
    return list(seen)


def _post_url(post) -> str:
    path = "reel" if post["kind"] == "video" else "p"
    return f"https://www.instagram.com/{path}/{post['shortcode']}/"


class ClaudeAnalyzer:
    def __init__(self, model: str = "sonnet"):
        self.model = model

    def analyze(self, post, transcript: str | None, images: list[Path], workdir: Path,
                registry: Registry) -> dict:
        prompt = build_prompt(post, transcript, images, registry)
        result = run_claude(
            prompt,
            schema=ANALYSIS_SCHEMA,
            system_prompt=SYSTEM_PROMPT,
            model=self.model,
            tools=("Read",) if images else (),
            cwd=workdir,
        )
        return normalize(result, registry)
