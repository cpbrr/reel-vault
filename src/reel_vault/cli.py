"""Turn Instagram saves into a searchable Obsidian second brain."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from instaloader.exceptions import InstaloaderException

from reel_vault import __version__
from reel_vault.config import Config, load_config
from reel_vault.fetch import session
from reel_vault.state import State

SECRET_ENV_VARS = ("GROQ_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "NTFY_TOPIC")


def cmd_status(cfg: Config, args: argparse.Namespace) -> int:
    with State(cfg.paths.state_db) as state:
        counts = state.counts()
        print(f"profile:   {cfg.profile}")
        print(f"state:     {cfg.paths.state_db}")
        print(f"backfill:  {'complete' if state.backfill_complete else 'not complete'}")
        print(f"last fetch: {state.get_meta('last_fetch_at', 'never')}")
        print(f"posts:     {sum(counts.values())}")
        for status, n in sorted(counts.items()):
            print(f"  {status:<12} {n}")
    return 0


def cmd_doctor(cfg: Config, args: argparse.Namespace) -> int:
    ok = True

    def check(label: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok &= passed
        print(f"[{'ok' if passed else 'XX'}] {label}{f'  ({detail})' if detail else ''}")

    print(f"profile {cfg.profile}, home {cfg.paths.home}")
    for tool in cfg.required_tools:
        check(f"tool {tool}", shutil.which(tool) is not None)
    for var in SECRET_ENV_VARS:
        # Presence only: never print secret values.
        check(f"env {var}", bool(os.environ.get(var)), "set" if os.environ.get(var) else "missing")
    check("vault dir", cfg.paths.vault_dir.is_dir(), str(cfg.paths.vault_dir))

    if not args.offline and shutil.which("claude"):
        from reel_vault.analyze.claude import ClaudeError, check_auth

        try:
            check("claude login", True, check_auth())
        except ClaudeError as e:
            check("claude login", False, str(e))

    if not cfg.paths.session_file.is_file():
        check("instagram session", False, "missing, run `reel-vault login`")
    elif args.offline:
        check("instagram session", True, "file present, not verified (--offline)")
    else:
        try:
            loader = session.open_session(cfg.paths.session_file)
            check("instagram session", True, f"logged in as {loader.context.username}")
        except session.SessionError as e:
            check("instagram session", False, str(e))
    return 0 if ok else 1


def cmd_login(cfg: Config, args: argparse.Namespace) -> int:
    target = cfg.paths.session_file
    if args.interactive is not None:
        username = args.interactive or input("Instagram username: ").strip().lstrip("@")
        username = session.login_interactive(target, username)
    elif args.browser:
        username = session.login_browser(target, args.browser)
    else:
        username = session.login_firefox(target, Path(args.cookiefile) if args.cookiefile else None)
    print(f"Saved Instagram session for @{username} to {target}")
    print("Treat this file like a password: anyone with it can use the account.")
    return 0


def fetch_saved(cfg: Config, state: State, loader, *, limit: int | None, dry_run: bool) -> int:
    """Walk saves, record new ones as pending. Returns the number of new posts."""
    from reel_vault.fetch.instagram import SavedScan, iter_saved

    # Until one full walk has finished, never stop early (see State.backfill_complete).
    stop_after = cfg.fetch.stop_after_known if state.backfill_complete else 0
    limit = limit if limit is not None else cfg.fetch.max_posts_per_run
    scan = SavedScan(iter_saved(loader), state.is_known, stop_after_known=stop_after, limit=limit)

    seen = new = 0
    for post, is_new in scan:
        seen += 1
        new += is_new
        if is_new and not dry_run:
            state.add_post(post)
        caption = " ".join(post.caption.split())[:60]
        print(
            f"{'NEW ' if is_new else '    '} {post.kind:<8} {post.posted_at:%Y-%m-%d} "
            f"@{post.owner:<20} {post.shortcode:<12} {caption}"
        )

    if not dry_run:
        if scan.reached_end:
            state.mark_backfill_complete()
        state.set_meta("last_fetch_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))

    stopped = "end of saves" if scan.reached_end else scan.stop_reason
    verb = "would add" if dry_run else "added"
    print(f"{seen} scanned, {new} new ({verb}), stopped: {stopped}", file=sys.stderr)
    return new


def cmd_fetch(cfg: Config, args: argparse.Namespace) -> int:
    loader = session.open_session(cfg.paths.session_file)
    print(f"Logged in as @{loader.context.username}", file=sys.stderr)
    with State(cfg.paths.state_db) as state:
        fetch_saved(cfg, state, loader, limit=args.limit, dry_run=args.dry_run)
    return 0


def cmd_run(cfg: Config, args: argparse.Namespace) -> int:
    from reel_vault.analyze.analyzer import ClaudeAnalyzer
    from reel_vault.analyze.enricher import AgentReachEnricher
    from reel_vault.pipeline import Pipeline
    from reel_vault.transcribe.base import make_transcriber

    loader = session.open_session(cfg.paths.session_file)
    print(f"Logged in as @{loader.context.username}", file=sys.stderr)
    with State(cfg.paths.state_db) as state:
        if not args.no_fetch:
            fetch_saved(cfg, state, loader, limit=None, dry_run=False)
        pipeline = Pipeline(
            cfg,
            state,
            loader,
            make_transcriber(cfg.transcriber, cfg.transcribe_model),
            ClaudeAnalyzer(cfg.analyze_model),
            enricher=AgentReachEnricher(cfg.analyze_model) if shutil.which("agent-reach") else None,
        )
        if pipeline.enricher is None:
            print("agent-reach not on PATH: mentioned tools won't be looked up", file=sys.stderr)
        report = pipeline.process(limit=args.limit or 0, only=args.only)

    print(f"\n{len(report.written)} written, {len(report.failed)} failed")
    for category, title in report.written:
        print(f"  [{category}] {title}")
    for shortcode, error in report.failed:
        print(f"  FAILED {shortcode}: {error}")
    if report.stopped:
        print(f"Stopped early: {report.stopped}")
    return 1 if report.stopped or report.failed else 0


def cmd_retry(cfg: Config, args: argparse.Namespace) -> int:
    with State(cfg.paths.state_db) as state:
        print(f"{state.retry_failed()} failed posts reset to pending")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reel-vault", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("-p", "--profile", help="profile name or path to .toml (default: $REEL_VAULT_PROFILE or local)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="show pipeline state").set_defaults(func=cmd_status)

    doctor = sub.add_parser("doctor", help="check tools, secrets, vault and IG session")
    doctor.add_argument("--offline", action="store_true", help="don't contact Instagram")
    doctor.set_defaults(func=cmd_doctor)

    login = sub.add_parser("login", help="import the Instagram session (default: from Firefox)")
    how = login.add_mutually_exclusive_group()
    how.add_argument("--cookiefile", help="path to Firefox cookies.sqlite")
    how.add_argument("--browser", help="chrome, edge, brave, ... (needs the 'browser' extra)")
    how.add_argument(
        "--interactive", nargs="?", const="", metavar="USERNAME",
        help="log in with username/password prompt instead",
    )
    login.set_defaults(func=cmd_login)

    fetch = sub.add_parser("fetch", help="record new saved posts as pending")
    fetch.add_argument("--dry-run", action="store_true", help="list only, don't record")
    fetch.add_argument("--limit", type=int, help="stop after N posts (overrides profile)")
    fetch.set_defaults(func=cmd_fetch)

    run = sub.add_parser("run", help="fetch new saves, then transcribe, analyze and write notes")
    run.add_argument("--limit", type=int, help="process at most N posts this run")
    run.add_argument("--no-fetch", action="store_true", help="only process posts already recorded")
    run.add_argument("--only", nargs="+", metavar="SHORTCODE", help="process just these recorded posts")
    run.set_defaults(func=cmd_run)

    sub.add_parser("retry", help="reset failed posts to pending").set_defaults(func=cmd_retry)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Captions are full of emoji; don't crash on a non-UTF-8 console or pipe.
    # Line buffering keeps stdout/stderr in order when piped (e.g. journald).
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

    args = build_parser().parse_args(argv)
    cfg = load_config(args.profile)
    try:
        return args.func(cfg, args)
    except session.SessionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except InstaloaderException as e:
        # Mid-walk failures (rate limit, session revoked). Posts seen so far are already saved.
        print(f"error: Instagram: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
