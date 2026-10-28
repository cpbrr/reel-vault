"""Claude Code headless (`claude -p`) wrapper.

Runs on the user's Claude subscription (CLAUDE_CODE_OAUTH_TOKEN from
`claude setup-token`, or the CLI's own login). User settings, hooks, plugins and
MCP servers are skipped so every call is small and predictable.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

# Credentials the child may use; every other CLAUDE*/ANTHROPIC* variable is dropped.
KEEP_ENV = {"CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "CLAUDE_CONFIG_DIR"}

# Errors that will hit every post the same way: stop the run instead of
# burning a failed attempt on each post.
STOP_RUN_MARKERS = ("usage limit", "rate limit", "authenticate", "oauth", "overloaded", "credit")


class ClaudeError(RuntimeError):
    def __init__(self, message: str, stop_run: bool = False):
        super().__init__(message)
        self.stop_run = stop_run


def run_claude(
    prompt: str,
    *,
    schema: dict,
    system_prompt: str,
    model: str = "sonnet",
    tools: tuple[str, ...] = (),
    allowed_tools: tuple[str, ...] = (),
    cwd: Path | None = None,
    timeout: int = 600,
    usage: dict | None = None,
) -> dict:
    """`tools` are the tools Claude can see; `allowed_tools` are permission rules
    that let them run unattended (e.g. "Bash(agent-reach:*)"). Anything else is denied.
    `usage`, if given, is filled with the call's token counts and API-equivalent cost."""
    exe = shutil.which("claude")
    if exe is None:
        raise ClaudeError("claude CLI not found on PATH", stop_run=True)

    cmd = [
        exe, "-p",
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--model", model,
        "--system-prompt", system_prompt,
        "--tools", ",".join(tools),
        "--setting-sources", "project",
        "--strict-mcp-config",
        "--no-session-persistence",
    ]
    if allowed_tools:
        cmd += ["--allowedTools", *allowed_tools]
    try:
        # Prompt goes over stdin: transcripts can exceed Windows' argv limit.
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
            timeout=timeout, cwd=cwd, env=child_env(),
        )
    except subprocess.TimeoutExpired as e:
        raise ClaudeError(f"claude timed out after {timeout}s") from e

    return parse_output(proc.stdout, proc.stderr, proc.returncode, usage)


def check_auth() -> str:
    """One tiny call to prove the CLI can authenticate. Raises ClaudeError if not."""
    result = run_claude(
        "Reply with ok.",
        schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
        system_prompt="Reply with the structured output only.",
        model="haiku",
        timeout=120,
    )
    return "authenticated" if result.get("ok") else f"unexpected reply: {result}"


def child_env(environ: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for the child CLI.

    When reel-vault itself runs inside a Claude Code session (desktop app, its
    terminal panel), that session's variables (CLAUDE_CODE_SIMPLE, a proxy
    ANTHROPIC_BASE_URL, session ids...) would otherwise leak into the child and
    break its auth.
    """
    environ = os.environ if environ is None else environ
    return {
        key: value
        for key, value in environ.items()
        if key in KEEP_ENV or not key.upper().startswith(("CLAUDE", "ANTHROPIC"))
    }


USAGE_FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


def parse_output(stdout: str, stderr: str = "", returncode: int = 0, usage: dict | None = None) -> dict:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        detail = (stderr or stdout).strip()[-500:]
        raise ClaudeError(f"claude exited {returncode}: {detail}", _is_stop_run(detail)) from None

    if usage is not None:
        raw = data.get("usage") or {}
        usage.update({key: raw.get(key) or 0 for key in USAGE_FIELDS})
        usage["cost_usd"] = data.get("total_cost_usd") or 0.0
        usage["seconds"] = (data.get("duration_ms") or 0) / 1000

    if data.get("is_error"):
        message = str(data.get("result") or data.get("subtype") or "unknown error")
        raise ClaudeError(f"claude error: {message}", _is_stop_run(message))

    structured = data.get("structured_output")
    if isinstance(structured, dict):
        return structured
    # Fallback: the final message itself is the JSON object.
    result = str(data.get("result", "")).strip()
    result = result.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(result)
    except json.JSONDecodeError:
        raise ClaudeError(f"claude returned no structured output: {result[:300]}") from None


def _is_stop_run(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in STOP_RUN_MARKERS)
