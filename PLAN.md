# reel-vault — Architecture & Plan

Turn Instagram saves into a searchable Obsidian second brain: pull saves → transcribe → Claude summarizes, extracts tips and categorizes → look up mentioned repos → write notes into the vault. Obsidian Sync carries them to every device.

**Goal:** type `job` (or `climbing`, `claude skill`, `recipe`) in Obsidian search and get the relevant reels, with the tips already pulled out.

**Direction (changed 2026-10-28):** runs on the Windows laptop, not a homelab server. The long-term vision is a Claude Code skill/plugin anyone can install from GitHub (see §9). That is for later; the next steps are finishing the backlog and tidying categories.

---

## 1. Decisions

| Area | Decision |
|---|---|
| Host | Windows laptop, run from the repo with `uv run reel-vault …` (homelab CT 109 dropped) |
| Fetch | instaloader, saved posts via the Python API, logged-in session file; IG saves are never modified |
| Pacing | 3–8 s random pause between Instagram requests on the laptop (`local.toml`) |
| Media | thumbnail + mono 16 kHz mp3 + 3–6 video frames (one per ~8 s, long edge 1024 px) + up to 6 carousel images; mp4 never kept |
| Transcription | Groq Whisper `whisper-large-v3-turbo` (free tier) by default, or on-device faster-whisper (`backend = "local"`, `uv sync --extra local`); transcript kept in its original language |
| Analysis | `claude -p` on the Max subscription, Sonnet, structured output; sees caption, hashtags, transcript, cover + frames/images, category list |
| Enrichment | second `claude -p` call allowed only `agent-reach` (GitHub/web search) to resolve comment-gated repos |
| Categories | emergent; one main category + up to 2 related (existing only, never Memes) |
| Memes | notes under `Memes/`, one-line summary, no key points, but full tags/keywords so a specific meme is findable |
| Language | summary, key points, keywords in English |
| Vault | `P:\remotmoc\2 - Personal\Saved Reels\` (set by `REEL_VAULT_VAULT_DIR` in `.dev/secrets/.env`) |
| Notes | named `<title> DD-MM-YYYY.md` (posted date); `/` is not allowed in filenames |
| Storage | downloads deleted once a note is written; everything processed stays in `state.db`, so nothing is re-done |
| Backlog | 1830 saves; keep #1–1200 (newest first), skip #1201–1830 (old 2020 meme block, `reel-vault skip --after 1200`) |

---

## 2. Architecture

```
        pull process (Instagram + Groq)              analyze process (Claude only)
┌───────────────────────────────────────────┐   ┌──────────────────────────────────────┐
│ fetch   walk saved list newest-first      │   │ analyze --workers 4 [--follow]       │
│         → state.db (pending)              │   │  worker threads: claude -p calls     │
│ media   thumb, video → ffmpeg audio+frames│   │   1. analyze (Sonnet + Read images)  │
│ transcribe  Groq Whisper                  │   │   2. repo lookup (Bash: agent-reach) │
│         → transcribed (+ frames.done)     │   │  main thread only: state.db,         │
└───────────────────┬───────────────────────┘   │   categories.json, vault writes      │
                    │      shared state.db      │  → note + thumbnail, index pages     │
                    └──────────────────────────►└──────────────────────────────────────┘
                                                              │ plain files
                                                 P:\remotmoc\…\Saved Reels  ── Obsidian Sync ──► phone
```

The pipeline only writes plain files into a folder; whatever syncs that folder doesn't matter to it.

### Post states

`pending → media → transcribed → analyzed → written`; `failed` after 3 attempts (`retry` resets), `skipped` for saves deliberately left out (`skip --undo` restores). Every stage saves before the next starts, so a crash or stop resumes exactly where it was.

### Failure handling

- Instagram session problems, Claude usage/auth limits and long Groq limits stop the run without counting failures. `pull` waits out Groq hourly limits by itself.
- Other errors count against that post only. A failed repo lookup still writes the note, with the tools marked unresolved.

### Claude call settings

`claude -p --output-format json --json-schema … --model sonnet --system-prompt <short> --tools <Read | Bash> --setting-sources project --strict-mcp-config --no-session-persistence`, prompt on stdin, 600 s timeout, desktop-app env vars stripped, `CLAUDE_CODE_OAUTH_TOKEN` for auth. Token usage is logged per post and summed per run.

---

## 3. Analysis output

```json
{
  "category": "AI Tools",
  "new_category_description": null,
  "related_categories": ["Career"],
  "is_meme": false,
  "title": "CareerOps: Claude tool that scores jobs against your CV",
  "summary": "2–4 sentence English summary.",
  "key_points": ["…"],
  "tags": ["claude-code", "job-hunting"],
  "keywords": ["job hunting tool", "resume scoring"],
  "language": "en",
  "transcript_is_speech": true,
  "repo_hints": [{"query": "career-ops", "evidence": "comment APPLY for the repo"}]
}
```

Python then normalizes: memes forced to `Memes`, category snapped to an existing name, related categories filtered, tags turned into valid Obsidian tags, keywords de-duplicated. `transcript_is_speech: false` (lyrics, filler) drops the transcript from the note.

---

## 4. Vault layout & note

```
Saved Reels/
├── _attachments/<shortcode>.jpg
├── _index/Saved Reels Home.md, Reels - <Category>.md   (regenerated every run; "Also related" section)
├── AI Tools/CareerOps Claude tool that scores jobs against your CV 18-10-2026.md
├── Climbing/ …  Memes/ …  Recipes/ …
```

Front matter: `type, category, related_categories, tags, keywords, author, url, shortcode, kind, posted, saved_seen, language`. Body: cover, title, Summary, Key points, Resources (with install line) or "Mentioned tools (link not found)", Caption, Transcript (speech only), My notes.

New notes never overwrite a file. `reanalyze` and `rerender` rewrite notes but keep everything under **My notes**. The shortcode in the front matter is the identity.

---

## 5. Repo layout

```
reel-vault/
├── pyproject.toml, uv.lock          uv; console script `reel-vault`
├── src/reel_vault/
│   ├── cli.py                       status | doctor | login | fetch | pull | run | analyze |
│   │                                reanalyze | rerender | skip | retry
│   ├── config.py                    profile TOML → secrets/.env → env overrides
│   ├── profiles/{local,homelab}.toml
│   ├── state.py                     SQLite + migrations
│   ├── pipeline.py                  sequential stages + parallel analyze
│   ├── fetch/{session,instagram}.py
│   ├── media.py                     downloads, ffmpeg audio, frames
│   ├── transcribe/{base,groq}.py    Transcriber interface (local Whisper can slot in)
│   ├── analyze/{claude,analyzer,enricher}.py + prompts/{analyze,enrich}.md
│   ├── categories.py                emergent registry
│   └── vault/writer.py              notes, indexes, naming, My-notes preservation
└── tests/                           pytest, fakes for every outside service
```

Local data (git-ignored) under `.dev/`: `secrets/` (IG session, `.env`), `data/state.db`, `data/categories.json`, `data/media/` (temporary).

---

## 6. Commands

```bash
uv run reel-vault doctor                      # tools, secrets present, Claude + IG logins
uv run reel-vault pull [--no-fetch]           # fetch saves, download, transcribe, frames
uv run reel-vault analyze --workers 4 [--follow] [--limit N]   # Claude stages only
uv run reel-vault run [--no-fetch]            # everything, one post at a time
uv run reel-vault reanalyze [SHORTCODE ...]   # re-run Claude from saved data
uv run reel-vault rerender                    # rewrite notes from state.db (free)
uv run reel-vault skip --after N | --undo
uv run reel-vault retry                       # failed → pending
uv run reel-vault status
```

---

## 7. Measured numbers (2026-10-28)

| Stage | Speed |
|---|---|
| Fetch saved list | ~200 saves/min (1680 in 8 min) |
| Pull | ~4.5 posts/min at 3–8 s pauses (pause ~5 s, IG request ~3 s, download + ffmpeg ~4 s, Groq <1 s) |
| Analyze | ~16 s per Claude call; 4 workers ≈ 900 posts/h, so the pull is the bottleneck |
| Usage | ~0.14% of the Max 5-hour window and ~0.012% of the weekly limit per post; ≈ $0.07 API-equivalent per post (500 posts: 14.7M tokens in, 0.9M out, $35.53) |

All 1200 kept saves ≈ 1.7 five-hour windows and ~15% of a week.

---

## 8. Status & next steps

**Done:** fetch, media + frames, transcription, analysis, related categories, repo lookup, parallel analyze, note naming, rerender/reanalyze, skip, usage logging. ~700 of 1200 notes written so far.

| # | Next | Done when |
|---|---|---|
| 1 | Finish the backlog (#1–1200) | `status` shows only written/skipped/failed |
| 2 | Commit the work since the first commit | git history matches what runs |
| 3 | `categories review`: Claude proposes merges/renames/splits (one-post categories; AI Tools vs Dev Tools vs Software Engineering; maybe split AI Tools); apply moves files + rewrites `category` via rerender | reviewed with Rey, applied |
| 4 | Save dates: import Instagram's "Download your information" JSON, add a real `saved:` date to each note | backlog notes have true save dates |
| 5 | Ongoing new saves: decide how they run (manual `run`, or Windows Task Scheduler at 12:00/18:00 while the laptop is awake); `stop_after_known=20` keeps each run short | decided and set up |
| 6 | Optional: ntfy summary/alert per run | |
| 7 | Reanalyze the first 22 notes with frames (they predate frame extraction) | |

---

## 9. Future vision (not now): installable Claude Code skill

Publish on GitHub as a Claude Code plugin anyone can install:

- **`/reel-vault-setup`**: installs dependencies (uv, ffmpeg, agent-reach), walks through Instagram login, asks for the vault path and transcription choice (Groq key, or local Whisper on a Mac), writes the config and `.env`, runs `doctor`.
- **`/reel-vault`**: runs the pipeline (pull + analyze), shows progress and usage, answers "find my reels about X" from the vault.
- Platform profiles: Windows/Linux (Groq), Mac (mlx-whisper, launchd). The transcriber interface and profile system already allow this.

---

## 10. Risks

| Risk | Mitigation |
|---|---|
| Instagram flags automated access on the main account | paced requests, read-only, home IP; a checkpoint stops the run (re-login with `login --interactive`) |
| instaloader breaks after IG changes | `doctor`; pin the version, upgrade deliberately |
| Claude usage limits | runs stop cleanly and resume; ~0.14% of a 5-hour window per post |
| Groq free-tier caps | `pull` waits them out |
| Category sprawl (worse with parallel workers) | prefer-existing prompt + `categories review` |
| `claude` CLI flag changes | one wrapper in `analyze/claude.py`; `doctor` checks login |
| Repo not findable | listed as "link not found", tagged `repo-hint-unresolved` |
