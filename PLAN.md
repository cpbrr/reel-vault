# reel-vault — Architecture & Build Plan

Turn Instagram saves into a searchable Obsidian second brain.
Every 12h: pull new saves → transcribe → Claude summarizes, extracts tips, categorizes → crawl mentioned repos → write notes into the vault → Obsidian Sync carries them to every device.

**Goal:** type `job` (or `climbing`, `claude skill`, `recipe`) in Obsidian search and get the relevant reels, with the tips already pulled out.

---

## 1. Decisions locked

| Area | Decision |
|---|---|
| Fetch | instaloader, `:saved` via Python API, main IG account session |
| Host | Proxmox, new LXC **CT 109 `reel-vault`** (Debian 13, 2 cores / 2 GB / 16 GB, Australia/Adelaide; downloads deleted after each note, processed data kept in state.db), same pattern as CT 108 |
| Schedule | systemd timer, **12:00 and 18:00** Adelaide, `Persistent=true` |
| Transcription | Groq Whisper API (`whisper-large-v3-turbo`) |
| Analysis | Claude Code headless (`claude -p`) on Rey's subscription, `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token` |
| Depth | caption + hashtags + transcript (option 4b). Images read only for photo/carousel posts (no audio to transcribe) |
| Categories | emergent: Claude picks an existing category or creates a new one, plus free tags |
| Memes | imported as lightweight notes under `Memes/`, tagged `#meme` |
| Language | summary/tips in English, transcript in original language |
| Enrichment | agent-reach crawls repos mentioned in tech reels (found via transcript/caption clues, since links are usually comment-gated) |
| Vault | `P:\remotmoc\2 - Personal\Saved Reels\` — thumbnail + note with transcript, **no mp4** |
| Sync | Obsidian Headless (`ob sync --continuous`) in CT 109 |
| Alerts | ntfy |
| IG saves | never modified |
| Backlog | hundreds; whole backlog processed on first run |
| Portability | one core package + per-platform profiles (`homelab` now, `mac` later) |

---

## 2. Architecture

```
                    CT 109 reel-vault (Proxmox)
┌──────────────────────────────────────────────────────────────────┐
│ reel-vault.timer (12:00, 18:00)                                  │
│   └─ reel-vault run                                              │
│        1. fetch     instaloader → new saved posts → state.db     │
│        2. media     temp download → ffmpeg audio + thumbnail     │
│        3. transcribe  Groq Whisper                               │
│        4. analyze   claude -p → JSON (category, tags, tips…)     │
│        5. enrich    claude -p + agent-reach → repo summaries     │
│        6. write     markdown note + thumbnail → vault dir        │
│        7. index     regenerate per-category index notes          │
│        8. notify    ntfy summary / errors                        │
│                                                                  │
│ obsidian-sync.service: ob sync --continuous  (vault dir ⇄ cloud) │
└──────────────────────────────────────────────────────────────────┘
                 │ Obsidian Sync (E2E encrypted)
       ┌─────────┴──────────┐
  Windows laptop         Phone
  (Obsidian app — read, search, annotate)
```

The pipeline only writes plain files into a folder. Whatever syncs that folder (Obsidian Headless here, the Obsidian app on a Mac) does not matter to it.

### Per-post flow

1. **Fetch.** `Profile.get_saved_posts()` iterates newest first. Stop once we hit 20 consecutive already-known shortcodes (fast incremental). The first run walks everything. Sleep 5–15s with jitter between posts to stay gentle on IG.
2. **Media.**
   - Video: download to `/tmp`, extract mono 16 kHz mp3 with ffmpeg, delete the mp4.
   - Photo or carousel: download images to `/tmp` so Claude can read them.
   - Thumbnail: always saved to the vault, because IG CDN URLs expire.
3. **Transcribe.** Groq, with the language auto-detected. On rate limits (429) we back off and retry. If the run's budget is exhausted, the post stays `pending` for the next run.
4. **Analyze.** One `claude -p` call per post. Input is the caption, hashtags, author, transcript, the current category registry, and image paths for photo posts. Output is structured JSON (schema below).
5. **Enrich.** Only when the analysis returns `repo_hints`. A second `claude -p` call is allowed to run `agent-reach` (GitHub search and read), so it can find the repo from clues like the creator, the repo name, or "comment SKILL for the link". It returns the repo URL, a short README summary, and how to install or use it.
6. **Write.** Notes are **write-once**: the pipeline never edits a note after creating it, so your own annotations are safe. Only the index notes get regenerated.
7. **Notify.** ntfy sends one message per run ("12 new reels: 4 Climbing, 3 Claude Skills, 2 Memes…") plus failure alerts: IG session expired, Claude token invalid, ob sync down, more than N failures.

### State: `state.db` (SQLite)

`posts(shortcode PK, url, owner, kind, saved_seen_at, status, attempts, last_error, category, note_path, updated_at)`

Status goes `pending → media → transcribed → analyzed → written`, or `failed` after 3 attempts. A crash resumes from the last completed stage. `reel-vault retry` resets failed posts.

### Category registry: `categories.json`

`[{ "name": "Job Hunting", "description": "...", "count": 14 }, ...]`

Every analyze call receives the registry. The prompt rules are:
- Strongly prefer an existing category.
- Create a new one only when nothing fits, and give a one-line description.
- Use Title Case and a broad scope ("Climbing", not "Bouldering Footwork").
- Optionally list up to 2 `related_categories` (existing ones only). The note stays in its main category folder and is also listed under "Also related" on those index pages.

**Drift control:** `reel-vault categories review` asks Claude to propose merges and renames. It's manual for now; it may become a weekly ntfy suggestion later. Applying a merge moves the files and rewrites the `category` field.

---

## 3. Analysis output schema

```json
{
  "category": "Job Hunting",
  "new_category_description": null,
  "related_categories": ["Communication"],
  "is_meme": false,
  "title": "Answer 'tell me about yourself' in 3 parts",
  "summary": "2–4 sentence English summary.",
  "key_points": ["Present → past → future structure", "Keep it under 90 seconds", "..."],
  "tags": ["interview", "career", "communication"],
  "keywords": ["job hunting", "interview prep", "resume", "career advice"],
  "language": "en",
  "repo_hints": []
}
```

`keywords` holds search synonyms. They make a search for `job` find a reel that only ever says "interview" or "career".

Memes get `is_meme: true`, a one-line summary, and no key points.

---

## 4. Vault layout & note template

```
2 - Personal/Saved Reels/
├── _attachments/            # <shortcode>.jpg thumbnails
├── _index/                  # generated, one per category + Home
│   ├── Home.md
│   ├── Job Hunting.md
│   └── Climbing.md
├── Job Hunting/
│   └── 2026-10-28 Answer tell me about yourself in 3 parts.md
├── Claude Skills/
├── Climbing/
├── Recipes/
├── Games/
├── Setups/
└── Memes/
```

(The categories above are examples. The real ones come out of the backlog run.)

```markdown
---
type: saved-reel
category: Job Hunting
related_categories: [Communication]
tags: [saved-reel, interview, career, communication]
keywords: [job hunting, interview prep, career advice]
author: "@creatorhandle"
url: https://www.instagram.com/reel/ABC123/
shortcode: ABC123
posted: 2026-09-14
saved_seen: 2026-10-28
language: en
---

![[ABC123.jpg|300]]

## Summary
...

## Key points
- Present → past → future structure
- ...

## Resources            <!-- only when enrichment found something -->
- [owner/repo](https://github.com/owner/repo): what it is, how to install

## Caption
> original caption

## Transcript
...full transcript...

## My notes
<!-- yours; never touched by the pipeline -->
```

The filename is `<posted date> <title>.md`. The shortcode in the frontmatter is the identity, so a rename on your side never breaks anything.

---

## 5. Repo layout (portable core)

```
reel-vault/
├── pyproject.toml                  # uv; entry point `reel-vault`
├── src/reel_vault/
│   ├── cli.py                      # run | status | retry | categories review | doctor
│   ├── config.py                   # loads profile + .env
│   ├── state.py                    # SQLite
│   ├── pipeline.py                 # stage orchestration, budgets, locking
│   ├── fetch/instagram.py          # instaloader wrapper
│   ├── media.py                    # ffmpeg audio, thumbnails, image temp files
│   ├── transcribe/
│   │   ├── base.py
│   │   ├── groq.py                 # homelab
│   │   └── mlx.py                  # mac (later)
│   ├── analyze/
│   │   ├── claude.py               # claude -p wrapper, JSON schema, retries
│   │   └── prompts/{analyze.md,enrich.md,review.md}
│   ├── vault/
│   │   ├── writer.py
│   │   ├── index.py
│   │   └── templates/note.md.j2
│   └── notify.py                   # ntfy
├── profiles/
│   ├── homelab.toml
│   └── mac.toml                    # later
├── deploy/
│   ├── homelab/
│   │   ├── setup-ct.sh             # run inside CT 109
│   │   ├── reel-vault.service      # oneshot, TimeoutStartSec=infinity
│   │   ├── reel-vault.timer        # 12:00, 18:00 Australia/Adelaide
│   │   └── obsidian-sync.service   # ob sync --continuous, Restart=always
│   └── mac/                        # launchd plist + installer (later)
├── plugin/                         # Claude Code plugin: skill + /reel-vault (later)
└── tests/
```

Swapped per platform: the transcriber, the scheduler files, the install flow, and the vault path. Everything else is shared.

---

## 6. CT 109 contents (`/opt/reel-vault`)

| Item | Notes |
|---|---|
| Python 3.13 venv via uv | reel-vault + instaloader + agent-reach |
| ffmpeg | apt |
| Node 22+ | for `obsidian-headless` and Claude Code |
| `claude` | native installer |
| `ob` | `npm i -g obsidian-headless` |
| `vault/` | Obsidian Headless synced copy of **remotmoc** (full vault, check size) |
| `secrets/.env` | `GROQ_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, `NTFY_TOPIC` — chmod 600 |
| `secrets/ig-session` | instaloader session — chmod 600 (**anyone with it can use your IG account**) |
| `data/state.db`, `data/categories.json` | pipeline state |
| `reel-vault` wrapper | symlinked to `/usr/local/bin`, like `streak` |

Access is the same as CT 108: `pct push 109 …` / `pct exec 109 -- bash -lc '…'`.

---

## 7. Steps we do together (secrets, never pasted in chat)

1. **IG session.** Log into IG in Firefox on the laptop. Run instaloader's Firefox cookie-import script. `pct push` the session file. Test with `reel-vault doctor`.
2. **Obsidian Headless.** `pct exec 109 -- bash -lc 'ob login'` (interactive), then `ob sync-setup --vault "remotmoc"` and the first sync.
3. **Claude token.** Run `claude setup-token` and put the result into `secrets/.env` yourself.
4. **Groq key.** Create a free key at console.groq.com and add it to `.env`.
5. **ntfy.** Pick an unguessable topic name and subscribe to it on your phone.

---

## 8. Build milestones

| # | Milestone | Done when |
|---|---|---|
| M0 | Repo skeleton, config, state, CLI stubs | `reel-vault status` runs |
| M1 | Fetch | dry-run lists your saved posts with shortcode, kind, caption (run on the laptop first; instaloader works on Windows) |
| M2 | Transcribe + analyze + write on **5 sample reels** into a test folder | **Checkpoint: we review the notes together and tune the prompts and template before anything touches the real vault** |
| M3 | Repo enrichment via agent-reach | a Claude-skills reel gets a correct `Resources` section |
| M4 | Deploy CT 109: setup script, secrets, Obsidian Headless, systemd units, ntfy | a manual `reel-vault run` writes a note that shows up on the laptop |
| M5 | Backlog run | all saves processed; we review the emergent categories and run `categories review` once |
| M6 | Timer live | 12:00/18:00 runs send ntfy summaries |
| Later | Mac profile (mlx-whisper, launchd) + Claude Code plugin, publish to GitHub | |

---

## 9. Risks & mitigations

| Risk | Mitigation |
|---|---|
| IG flags automated access on the main account | 2 runs/day, jitter between posts, backlog paced, residential home IP, never write to IG |
| instaloader breaks after IG changes | `doctor` check + ntfy alert; pin the version and upgrade deliberately |
| IG session expires | detected at fetch → ntfy "re-import session" |
| Claude usage limits during the backlog | posts stay `pending` and resume next run; Sonnet for analysis |
| Groq free-tier caps (requests and audio seconds) | backoff, carry over to the next run |
| Category sprawl | strong prefer-existing prompt + `categories review` |
| Obsidian Headless is beta | `obsidian-sync.service` restarts on failure; notes are write-once, so conflicts are unlikely |
| Full vault copy on the server | CT disk unencrypted on LVM, same trust level as CT 108; access only through `pct` |
| Repo not findable (comment-gated, no clues) | `Resources` omitted; a `repo_hint_unresolved` tag lets you find those notes later |

---

## 10. To verify at build time

- Current `claude -p` flags for structured output (`--output-format json`, JSON schema support) and image input (`--allowedTools Read`).
- That instaloader's `get_saved_posts()` still works and which session-import script is current.
- Groq's current free-tier limits and file-size cap.
- agent-reach's GitHub channel commands (`agent-reach github …`) and its install method (from GitHub, not PyPI).
- Whether `ob sync-setup` can limit sync to a subfolder (would avoid the full vault copy).
