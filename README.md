# reel-vault

Turn your Instagram saves into a searchable Obsidian second brain.

reel-vault pulls the posts and reels you have saved, transcribes them, has Claude write a short note for each one (summary, key takeaways, tags, search keywords, a category), looks up any tools or GitHub repos the reel mentions, and writes everything as plain Markdown into your Obsidian vault. Type `job`, `climbing` or `claude skill` into Obsidian search and the relevant reels come up with the tips already pulled out.

> **Account risk.** This reads your own Instagram account with your own session cookie, which Instagram's terms don't allow. It never posts, likes or changes your saves, and it paces its requests, but use it at your own risk.

## How it works

```
Instagram saved list
   │  fetch     instaloader walks your saves, newest first → state.db
   ▼
pull       download thumbnail + video, ffmpeg → mono 16 kHz audio + 3–6 still frames
           transcribe the audio (Groq Whisper, or Whisper running locally)
   ▼
analyze    claude -p (Sonnet) reads caption, hashtags, transcript, cover and frames
           → title, summary, key points, tags, keywords, category (+ up to 2 related)
   ▼
enrich     if a tool/repo is mentioned: a second claude -p call that may only run
           agent-reach (GitHub + web search) finds the link and install command
   ▼
write      Markdown note + thumbnail into the vault, category index pages rebuilt
```

- **Categories emerge from your saves.** Claude sees the current category list for every post, reuses one when it fits and creates a new one only when nothing does. `categories merge` tidies them up later.
- **Everything is resumable.** Each post moves `pending → transcribed → analyzed → written`, and every step is saved in a local SQLite file (`state.db`) before the next one starts. Stop it at any point and it carries on where it left off; nothing is downloaded or analyzed twice.
- **Downloads are temporary.** Video is never kept; audio and frames are deleted once the note is written. What's kept in `state.db` (caption, transcript, analysis) is enough to rebuild every note for free with `rerender`.
- **Your notes stay yours.** Anything you write under `## My notes` survives re-analysis and re-rendering.
- **Fast where it can be.** Downloading is deliberately paced (a few seconds between Instagram requests). Analysis runs several Claude calls at once (`analyze --workers 4`) and can follow a running pull.

### Tech stack

| Part | Tool |
|---|---|
| Language | Python 3.11+, managed with [uv](https://docs.astral.sh/uv/) |
| Instagram | [instaloader](https://instaloader.github.io/) with a saved login session |
| Media | `requests`, `ffmpeg` / `ffprobe` |
| Transcription | [Groq](https://console.groq.com) Whisper `whisper-large-v3-turbo` (free tier), or [faster-whisper](https://github.com/SYSTRAN/faster-whisper) on your own machine |
| Analysis | [Claude Code](https://docs.anthropic.com/en/docs/claude-code) headless (`claude -p`) on your own Claude subscription, structured JSON output |
| Repo lookup | [agent-reach](https://github.com/Panniantong/Agent-Reach) |
| State | SQLite + a JSON category list |
| Output | Markdown with YAML front matter, read by Obsidian |

### Code layout

```
src/reel_vault/
├── cli.py              all commands
├── config.py           profile TOML → secrets/.env → environment overrides
├── profiles/           local.toml (default), homelab.toml
├── state.py            SQLite: one row per saved post
├── pipeline.py         runs posts through the steps (sequential and parallel)
├── fetch/              Instagram session + saved-list walk
├── media.py            downloads, audio, frames
├── transcribe/         groq.py (cloud) and local.py (on-device), same interface
├── analyze/            claude -p wrapper, analysis + repo-lookup prompts
├── categories.py       the emergent category list
└── vault/writer.py     notes, index pages, file names
```

## Security and privacy

- **Keys and cookies stay on your machine.** The Groq key, the Claude token and the Instagram session cookie live only in `.dev/secrets/` (git-ignored). They are never committed, never sent anywhere except to the service they belong to, and never printed: `doctor` only reports whether each one is set. On Linux/macOS the session file is created with `chmod 600`.
- **No server, no telemetry.** reel-vault runs entirely on your computer and writes plain files into a folder you choose. There is no reel-vault backend.
- **Transcription can be fully local.** With `backend = "local"` the audio is transcribed on your own CPU/GPU by Whisper (faster-whisper) and never leaves your machine. The Groq backend is faster and free, but sends the audio to Groq.
- **What does leave your machine, and to whom:**
  - Instagram: requests for your own saved posts, using your session.
  - Groq: the extracted audio, only if you use the Groq backend.
  - Anthropic: caption, transcript and frames for analysis, through your own Claude Code login (the same as using Claude yourself).
  - GitHub / web search: tool or repo names, only for posts that mention one.
- **Claude runs locked down.** Each call gets a short custom system prompt and only the tool it needs (`Read` for the images, or `agent-reach` commands for repo lookup). Your Claude Code settings, hooks, plugins and MCP servers are not loaded, and no session history is kept.

## Setup

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/), `ffmpeg` on PATH, [Claude Code](https://docs.anthropic.com/en/docs/claude-code) logged in to your subscription, and optionally [agent-reach](https://github.com/Panniantong/Agent-Reach) for repo lookups.

```bash
git clone <this repo> && cd reel-vault
uv sync                      # Groq transcription
uv sync --extra local        # or: on-device transcription with faster-whisper
```

Create `.dev/secrets/.env` (it's git-ignored):

```ini
GROQ_API_KEY=...                       # only for the Groq backend (free key at console.groq.com)
CLAUDE_CODE_OAUTH_TOKEN=...            # from `claude setup-token`
REEL_VAULT_VAULT_DIR=C:/path/to/YourVault/Saved Reels
```

To transcribe locally instead of with Groq, set this in `src/reel_vault/profiles/local.toml` (or a copy passed with `-p my.toml`):

```toml
[transcribe]
backend = "local"
model = "small"      # tiny | base | small | medium | large-v3-turbo; downloaded once, then offline
```

Log in to Instagram (you type the password and 2FA yourself; only the session cookie is saved):

```bash
uv run reel-vault login --interactive YOUR_USERNAME
uv run reel-vault doctor           # checks tools, secrets, Claude login and the Instagram session
```

## Running it

```bash
uv run reel-vault pull                           # fetch saves, download, transcribe, frames
uv run reel-vault analyze --workers 4 --follow   # Claude notes, in parallel, following the pull
uv run reel-vault status                         # counts per stage
```

Run `pull` and `analyze --follow` in two terminals to process a large backlog. For a few new saves, `uv run reel-vault run` does everything one post at a time.

| Command | What it does |
|---|---|
| `pull [--no-fetch] [--limit N]` | fetch the saved list, then download + transcribe (no Claude) |
| `analyze [--workers N] [--follow] [--limit N]` | Claude analysis + repo lookup + notes (no Instagram) |
| `run [--no-fetch] [--limit N]` | everything, one post at a time |
| `reanalyze [SHORTCODE ...]` | re-run Claude from the saved transcript and rewrite the notes |
| `rerender` | rewrite every note from `state.db` (no API calls), e.g. after a template change |
| `categories merge plan.json` | merge/rename categories, move notes, rebuild index pages |
| `skip --after N` / `skip --undo` | leave saves below position N out (e.g. an old block of memes) |
| `retry` | send failed posts back to the queue |
| `fetch --dry-run --limit 20` | list your newest saves without recording anything |
| `doctor` | check the setup |

### What a note looks like

```markdown
---
category: "AI Tools"
related_categories: ["Career & Skills"]
tags: ["saved-reel", "claude-code", "job-hunting"]
keywords: ["job hunting tool", "resume scoring", "cv tailoring"]
author: "@creator"
url: "https://www.instagram.com/reel/…/"
posted: "2026-10-18"
---
![[<shortcode>.jpg|300]]
# CareerOps: Claude tool that scores jobs against your CV
## Summary · ## Key points · ## Resources (link + install command)
## Caption · ## Transcript · ## My notes
```

Files are named `<title> DD-MM-YYYY.md` and grouped in one folder per category, with generated index pages in `_index/`.

## Cost and speed

Measured on about 1200 reels with a Claude Max plan:

- **Pull:** ~4.5 posts a minute (the pause between Instagram requests is the bottleneck). Groq takes under a second per reel.
- **Analysis:** ~16 s per Claude call; 4 workers can do ~900 posts an hour.
- **Usage:** about 0.14% of the 5-hour window and 0.012% of the weekly limit per post. 1200 reels used roughly 12% of a week's allowance (≈ $0.07 per post if it were billed at API prices).

## Development

```bash
uv run pytest                    # every outside service is faked
uv run reel-vault doctor --offline
```

See [PLAN.md](PLAN.md) for the design decisions, measured numbers and what's next (including the idea of turning this into an installable Claude Code skill: `/reel-vault-setup` and `/reel-vault`).
