"""Render notes and index pages into the vault folder.

Notes are write-once: an existing file is never overwritten, so anything the
user adds under "My notes" (or anywhere else) is safe. Index pages are fully
generated and rewritten on every run.
"""

from __future__ import annotations

import json
import re
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ATTACHMENTS = "_attachments"
INDEX_DIR = "_index"
HOME_INDEX = "Saved Reels Home"
SEPARATORS = re.compile(r"[/\\|]")
FORBIDDEN = re.compile(r'[<>:"?*#^\[\]\x00-\x1f]')


def safe_name(text: str, max_len: int = 80) -> str:
    """Filename-safe and wikilink-safe."""
    name = FORBIDDEN.sub("", SEPARATORS.sub(" ", text))
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:max_len].rstrip(" .") or "Untitled"


def index_name(category: str) -> str:
    return f"Reels - {safe_name(category)}"


def yaml_value(value) -> str:
    # JSON scalars and arrays are valid YAML, and JSON escaping handles quotes/colons.
    return json.dumps(value, ensure_ascii=False)


UNRESOLVED_TAG = "repo-hint-unresolved"
MY_NOTES = "## My notes"


def render_note(
    post, analysis: dict, attachment: str | None, saved_seen: str, resources: list[dict] | None = None,
    my_notes: str = "",
) -> str:
    """`resources` is the enrich stage's output; None means enrichment didn't run."""
    found = [r for r in resources or [] if r["found"] and r.get("url")]
    unresolved = [r for r in resources or [] if not (r["found"] and r.get("url"))]

    tags = ["saved-reel", *analysis["tags"]]
    if analysis.get("is_meme") and "meme" not in tags:
        tags.append("meme")
    if unresolved:
        tags.append(UNRESOLVED_TAG)
    frontmatter = {
        "type": "saved-reel",
        "category": analysis["category"],
        "related_categories": analysis.get("related_categories", []),
        "tags": tags,
        "keywords": analysis["keywords"],
        "author": f"@{post['owner']}",
        "url": _post_url(post),
        "shortcode": post["shortcode"],
        "kind": post["kind"],
        "posted": (post["posted_at"] or "")[:10],
        "saved_seen": saved_seen,
        "language": analysis.get("language") or post["language"],
    }
    lines = ["---", *(f"{key}: {yaml_value(value)}" for key, value in frontmatter.items()), "---", ""]

    if attachment:
        lines += [f"![[{attachment}|300]]", ""]
    lines += [f"# {analysis['title']}", "", "## Summary", analysis["summary"], ""]
    if analysis["key_points"]:
        lines += ["## Key points", *(f"- {p}" for p in analysis["key_points"]), ""]
    if found:
        lines.append("## Resources")
        for r in found:
            lines.append(f"- [{r['name']}]({r['url']}): {r['what_it_is']}")
            if r.get("install"):
                lines.append(f"  - Install: `{r['install']}`")
        lines.append("")
    if unresolved:
        lines += ["## Mentioned tools (link not found)", *(f"- {r['name']}: {r['what_it_is']}" for r in unresolved), ""]
    elif resources is None and analysis.get("repo_hints"):
        lines += ["## Mentioned tools", *(f"- {h['query']} ({h['evidence']})" for h in analysis["repo_hints"]), ""]
    if post["caption"]:
        lines += ["## Caption", *(f"> {line}" if line else ">" for line in post["caption"].splitlines()), ""]
    # Song lyrics / background music would only pollute search results.
    if post["transcript"] and analysis.get("transcript_is_speech", True):
        lines += ["## Transcript", post["transcript"], ""]
    lines += [MY_NOTES, "", *([my_notes, ""] if my_notes else [""])]
    return "\n".join(lines)


def _post_url(post) -> str:
    path = "reel" if post["kind"] == "video" else "p"
    return f"https://www.instagram.com/{path}/{post['shortcode']}/"


def note_stem(post, analysis: dict) -> str:
    """"<title> DD-MM-YYYY" (posted date). Filenames can't contain "/"."""
    posted = (post["posted_at"] or "")[:10]
    date = f"{posted[8:10]}-{posted[5:7]}-{posted[:4]}" if len(posted) == 10 else ""
    return safe_name(f"{safe_name(analysis['title'], 68)} {date}")


class VaultWriter:
    def __init__(self, vault_dir: Path):
        self.vault_dir = vault_dir
        # Wikilinks resolve by filename, so names must be unique across the whole folder.
        self._taken = {p.stem.casefold() for p in vault_dir.rglob("*.md")} if vault_dir.is_dir() else set()

    def write(self, post, analysis: dict, thumbnail: Path | None, resources: list[dict] | None = None) -> Path:
        """Write the note; returns its path relative to the vault dir."""
        stem = self._unique_stem(post, analysis)
        path = self._folder(analysis) / f"{stem}.md"
        with open(path, "x", encoding="utf-8") as f:  # "x": never overwrite
            f.write(self._render(post, analysis, thumbnail, resources))
        self._taken.add(stem.casefold())
        return path.relative_to(self.vault_dir)

    def rewrite(self, old_path: str, post, analysis: dict, thumbnail: Path | None,
                resources: list[dict] | None = None) -> Path:
        """Replace a note (after re-analysis or a template/naming change), keeping
        whatever is under "My notes". Renamed/moved if its title or category changed."""
        old = self.vault_dir / old_path
        my_notes = ""
        if old.is_file():
            marker = f"\n{MY_NOTES}\n"
            text = old.read_text(encoding="utf-8")
            if marker in text:
                my_notes = text.split(marker, 1)[1].strip()
        self._taken.discard(old.stem.casefold())
        path = self._folder(analysis) / f"{self._unique_stem(post, analysis)}.md"
        path.write_text(self._render(post, analysis, thumbnail, resources, my_notes), encoding="utf-8")
        if path != old and old.is_file():
            old.unlink()
            if not any(old.parent.iterdir()):
                old.parent.rmdir()
        self._taken.add(path.stem.casefold())
        return path.relative_to(self.vault_dir)

    def _unique_stem(self, post, analysis: dict) -> str:
        base = note_stem(post, analysis)
        stem, n = base, 1
        while stem.casefold() in self._taken:
            stem = f"{base} ({post['shortcode']}{f' {n}' if n > 1 else ''})"
            n += 1
        return stem

    def _folder(self, analysis: dict) -> Path:
        folder = self.vault_dir / safe_name(analysis["category"])
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _render(self, post, analysis, thumbnail, resources, my_notes: str = "") -> str:
        attachment = f"{post['shortcode']}.jpg"
        dest = self.vault_dir / ATTACHMENTS / attachment
        if thumbnail and thumbnail.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(thumbnail, dest)
        elif not dest.is_file():  # downloads may be gone; reuse the copy already in the vault
            attachment = None
        saved_seen = (post["first_seen_at"] or datetime.now(timezone.utc).isoformat())[:10]
        return render_note(post, analysis, attachment, saved_seen, resources, my_notes)

    def write_indexes(self, written_posts, descriptions: dict[str, str]) -> None:
        by_category: dict[str, list] = defaultdict(list)
        related: dict[str, list] = defaultdict(list)  # posts filed elsewhere that also fit here
        for post in written_posts:
            analysis = json.loads(post["analysis"])
            by_category[post["category"]].append((post, analysis))
            for name in analysis.get("related_categories", []):
                related[name].append((post, analysis))
        # A related category whose own posts all moved elsewhere gets no page.
        related = defaultdict(list, {k: v for k, v in related.items() if k in by_category})

        index_dir = self.vault_dir / INDEX_DIR
        index_dir.mkdir(parents=True, exist_ok=True)
        for stale in index_dir.glob("Reels - *.md"):
            stale.unlink()

        for category in by_category:
            posts, also = by_category.get(category, []), related.get(category, [])
            lines = [
                "---", "type: saved-reels-index", f"category: {yaml_value(category)}", "---", "",
                f"# {category}", "", descriptions.get(category, ""), "",
                f"[[{HOME_INDEX}|All categories]] · {len(posts)} reels", "",
                *(_index_line(post, analysis) for post, analysis in posts),
            ]
            if also:
                lines += ["", "## Also related", "", *(_index_line(post, analysis) for post, analysis in also)]
            (index_dir / f"{index_name(category)}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

        home = ["---", "type: saved-reels-index", "---", "", "# Saved Reels", ""]
        for category, posts in sorted(by_category.items(), key=lambda kv: -len(kv[1])):
            extra = f" +{len(related[category])} related" if related.get(category) else ""
            home.append(
                f"- [[{index_name(category)}|{category}]] ({len(posts)}{extra}): {descriptions.get(category, '')}"
            )
        (index_dir / f"{HOME_INDEX}.md").write_text("\n".join(home) + "\n", encoding="utf-8")


def _index_line(post, analysis: dict) -> str:
    return f"- [[{Path(post['note_path']).stem}|{analysis['title']}]] · @{post['owner']}"
