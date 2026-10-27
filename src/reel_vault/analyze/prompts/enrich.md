An Instagram post I saved points to some software, but the link is usually gated ("comment X and I'll DM you"). Find the real thing for each hint below.

You have the `agent-reach` CLI (every command prints one JSON object; read `ok`, `items`, `error`):

- `agent-reach web search "<query>" --limit 5`: semantic web search. Usually the best first step.
- `agent-reach github search "<query>" --limit 5`: repository search. Often returns forks and copies, so prefer the original owner.
- `agent-reach github read <owner/repo>`: repo details and README, to verify a candidate and get install steps.
- `agent-reach web read <url>`: read any page, e.g. a skills.sh or product page.

Rules:
- Use at most 8 commands in total. Stop as soon as each hint is resolved.
- Only report a match you have verified (the README/page matches the post's description and creator). Prefer the original repo over forks, mirrors and "awesome" lists.
- `install`: a single command copied from the README/page (e.g. `npx skills add vercel-labs/skills`). If installing means following a guide, write `Follow <url>` with that guide's URL. No explanations, English only, under 120 characters. Null if there is no install step.
- `what_it_is`: one plain English sentence, under 25 words.
- `name`: the repo as `owner/repo` for GitHub, otherwise the product name.
- If you cannot find it with reasonable confidence, set `found` false and leave `url` null. A wrong link is worse than none.

## Post

- Author: @$owner
- Title: $title
- Summary: $summary

### Caption

$caption

### Transcript

$transcript

## Hints

$hints
