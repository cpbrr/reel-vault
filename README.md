# reel-vault

Turn Instagram saves into a searchable Obsidian second brain: pull new saves every 12h, transcribe, let Claude summarize, extract tips and categorize, then write notes into an Obsidian vault.

See [PLAN.md](PLAN.md) for the architecture and milestones.

> **Account risk.** This automates access to your own Instagram account with your session cookies, which is against Instagram's terms. Keep run frequency low, never share your session file, and use at your own risk.

## Status

M0–M3 are done and run locally: fetch saves → Groq transcription → Claude analysis + emergent categories → repo lookup via [agent-reach](https://github.com/Panniantong/Agent-Reach) → notes and index pages in the vault. Next: deployment on the homelab (M4).

## Run

```bash
uv run reel-vault doctor                 # tools, secrets, Claude + IG logins
uv run reel-vault run --limit 5          # fetch new saves, process up to 5
uv run reel-vault run --no-fetch --only SHORTCODE ...
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
