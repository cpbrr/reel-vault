"""Resolve repo/tool hints from the analysis into verified links via agent-reach.

Claude runs with only the Bash tool, restricted to `agent-reach` commands.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from string import Template

from reel_vault.analyze.claude import run_claude

SYSTEM_PROMPT = (
    "You find the original source of software mentioned in social media posts. "
    "You verify before you report and never guess links. Respond with the structured output."
)

ENRICH_SCHEMA = {
    "type": "object",
    "properties": {
        "resources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "hint": {"type": "string"},
                    "found": {"type": "boolean"},
                    "name": {"type": "string"},
                    "url": {"type": ["string", "null"]},
                    "what_it_is": {"type": "string"},
                    "install": {"type": ["string", "null"]},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": ["hint", "found", "name", "url", "what_it_is", "install", "confidence"],
            },
        }
    },
    "required": ["resources"],
}


def build_prompt(post, analysis: dict) -> str:
    template = Template(
        (resources.files("reel_vault.analyze.prompts") / "enrich.md").read_text(encoding="utf-8")
    )
    hints = "\n".join(f"- {h['query']} (evidence: {h['evidence']})" for h in analysis["repo_hints"])
    transcript = post["transcript"] if analysis.get("transcript_is_speech", True) else ""
    return template.substitute(
        owner=post["owner"],
        title=analysis["title"],
        summary=analysis["summary"],
        caption=post["caption"] or "(no caption)",
        transcript=transcript or "(no speech)",
        hints=hints,
    )


def clean(resources_: list[dict]) -> list[dict]:
    """Drop links Claude itself was unsure about; keep them as unresolved."""
    for r in resources_:
        if r["confidence"] == "low" or not r.get("url"):
            r["found"] = False
            r["url"] = None
    return resources_


class AgentReachEnricher:
    def __init__(self, model: str = "sonnet", timeout: int = 600):
        self.model = model
        self.last_usage: dict = {}  # token counts of the latest call
        self.timeout = timeout

    def enrich(self, post, analysis: dict, workdir: Path) -> list[dict]:
        if not analysis.get("repo_hints"):
            return []
        workdir.mkdir(parents=True, exist_ok=True)
        result = run_claude(
            build_prompt(post, analysis),
            schema=ENRICH_SCHEMA,
            system_prompt=SYSTEM_PROMPT,
            model=self.model,
            tools=("Bash",),
            allowed_tools=("Bash(agent-reach:*)",),
            cwd=workdir,
            usage=(usage := {}),
            timeout=self.timeout,
        )
        self.last_usage = usage
        return clean(result.get("resources", []))
