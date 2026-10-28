# reel-vault

Turn Instagram saves into a searchable Obsidian second brain: pull saves, transcribe, let Claude summarize, extract tips and categorize, then write notes into an Obsidian vault. Runs on a laptop with `uv`.

See [PLAN.md](PLAN.md) for the architecture, measured numbers and next steps.

> **Account risk.** This automates access to your own Instagram account with your session cookies, which is against Instagram's terms. Keep run frequency low, never share your session file, and use at your own risk.

## Status

Working end to end on Windows: fetch saves → download + video frames → Groq transcription → Claude analysis (parallel workers) + emergent categories → repo lookup via [agent-reach](https://github.com/Panniantong/Agent-Reach) → notes and index pages in the vault. Backlog run in progress; next: category review and save dates. A homelab deploy was dropped; an installable Claude Code skill is the long-term idea.

## Run

```bash
uv run reel-vault doctor                 # tools, secrets, Claude + IG logins
uv run reel-vault pull                   # fetch saves, download + transcribe + frames (no Claude)
uv run reel-vault analyze --workers 4 --follow   # Claude stages in parallel, following a running pull
uv run reel-vault run --limit 5          # everything for up to 5 new saves, one at a time
uv run reel-vault run --no-fetch --only SHORTCODE ...
uv run reel-vault rerender               # rewrite all notes from saved data (no API calls)
uv run reel-vault skip --after 1200      # leave saves below #1200 out (--undo to restore)
uv run reel-vault reanalyze [SHORTCODE ...]  # re-run Claude on written notes; keeps filename + "My notes"
uv run reel-vault retry                  # failed posts back to pending
```

Secrets go in `<home>/secrets/.env`: `GROQ_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`), `NTFY_TOPIC`. Mentioned tools are only looked up when `agent-reach` is on PATH.

## Dev quickstart

```bash
uv sync
uv run pytest
uv run reel-vault doctor --offline
```

Profiles live in `src/reel_vault/profiles/`. `local` (default) keeps everything under `./.dev`, including a throwaway test vault. Select another with `-p homelab` or `REEL_VAULT_PROFILE`.

## Instagram session

Log into instagram.com in a browser, then import its cookies:

```bash
uv run reel-vault login                     # from Firefox
uv run reel-vault login --browser edge      # Chromium browsers (uv sync --extra browser)
uv run reel-vault login --interactive USER  # password prompt instead
```

The session is saved to `<home>/secrets/ig-session`. It works like a password.

## Fetch

```bash
uv run reel-vault fetch --dry-run --limit 20   # list the 20 newest saves, record nothing
uv run reel-vault fetch                        # record all new saves as pending
uv run reel-vault status
```

The first fetch walks the whole saved list (the backlog). After that walk has completed once, fetches stop after 20 already-known posts in a row.
