"""Instagram session handling: import once from a browser, reuse from file.

The session file holds live IG cookies. Anyone with it can act as the account,
so it is written with 0600 permissions and must never be committed or logged.
"""

from __future__ import annotations

import os
import platform
import sqlite3
from glob import glob
from pathlib import Path

import instaloader

FIREFOX_COOKIE_GLOBS = {
    "Windows": ["~/AppData/Roaming/Mozilla/Firefox/Profiles/*/cookies.sqlite"],
    "Darwin": ["~/Library/Application Support/Firefox/Profiles/*/cookies.sqlite"],
    "Linux": ["~/.config/mozilla/firefox/*/cookies.sqlite", "~/.mozilla/firefox/*/cookies.sqlite"],
}


class SessionError(RuntimeError):
    pass


def new_loader(quiet: bool = True) -> instaloader.Instaloader:
    # Metadata only: media is downloaded by our own pipeline stages.
    return instaloader.Instaloader(
        quiet=quiet,
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
        max_connection_attempts=3,
    )


def open_session(session_file: Path) -> instaloader.Instaloader:
    """Load the saved session and confirm it is still logged in."""
    if not session_file.is_file():
        raise SessionError(f"No Instagram session at {session_file}. Run `reel-vault login` first.")
    loader = new_loader()
    # Username is resolved from the cookies by test_login() below.
    loader.load_session_from_file("", str(session_file))
    username = _test_login(loader)
    if not username:
        raise SessionError("Instagram session expired. Re-import it with `reel-vault login`.")
    loader.context.username = username
    return loader


def login_firefox(session_file: Path, cookiefile: Path | None = None) -> str:
    cookiefile = cookiefile or find_firefox_cookiefile()
    conn = sqlite3.connect(f"file:{cookiefile}?immutable=1", uri=True)
    try:
        try:
            rows = conn.execute(
                "SELECT name, value FROM moz_cookies WHERE baseDomain = 'instagram.com'"
            ).fetchall()
        except sqlite3.OperationalError:
            rows = conn.execute(
                "SELECT name, value FROM moz_cookies WHERE host LIKE '%instagram.com'"
            ).fetchall()
    finally:
        conn.close()
    loader = new_loader()
    loader.context._session.cookies.update(dict(rows))
    return _save(loader, session_file)


def login_browser(session_file: Path, browser: str) -> str:
    try:
        import browser_cookie3
    except ModuleNotFoundError:
        raise SessionError("Install the browser extra: uv sync --extra browser") from None
    load = getattr(browser_cookie3, browser.lower(), None)
    if load is None:
        raise SessionError(f"browser_cookie3 does not support {browser!r}")
    try:
        cookies = load(domain_name="instagram.com")
    except Exception as e:
        # Usually the cookie DB is locked by the running browser (the shadow-copy
        # fallback needs admin), or Chromium's app-bound encryption blocks decryption.
        raise SessionError(
            f"Could not read {browser} cookies ({type(e).__name__}: {e}). "
            f"Fully quit {browser} (check the tray / Task Manager) and retry, "
            "or use --interactive."
        ) from e
    loader = new_loader()
    loader.context._session.cookies.update(cookies)
    return _save(loader, session_file)


def login_interactive(session_file: Path, username: str) -> str:
    """Prompts for the password (and 2FA code) in the terminal; nothing is stored but cookies."""
    # instaloader refuses to prompt for a password in quiet mode.
    loader = new_loader(quiet=False)
    try:
        loader.interactive_login(username)
    except instaloader.exceptions.InstaloaderException as e:
        raise SessionError(f"Login failed: {e}") from e
    return _save(loader, session_file)


def find_firefox_cookiefile() -> Path:
    patterns = FIREFOX_COOKIE_GLOBS.get(platform.system(), FIREFOX_COOKIE_GLOBS["Linux"])
    found = [Path(p) for pattern in patterns for p in glob(os.path.expanduser(pattern))]
    if not found:
        raise SessionError("No Firefox cookies.sqlite found. Pass --cookiefile.")
    # Several profiles: the one used most recently is the one logged in.
    return max(found, key=lambda p: p.stat().st_mtime)


def _test_login(loader: instaloader.Instaloader) -> str | None:
    try:
        return loader.test_login()
    except instaloader.exceptions.ConnectionException as e:
        raise SessionError(f"Could not reach Instagram to verify the session: {e}") from e


def _save(loader: instaloader.Instaloader, session_file: Path) -> str:
    username = _test_login(loader)
    if not username:
        raise SessionError("Not logged in. Log into instagram.com in that browser first.")
    loader.context.username = username
    session_file.parent.mkdir(parents=True, exist_ok=True)
    loader.save_session_to_file(str(session_file))
    if os.name == "posix":
        os.chmod(session_file, 0o600)
    return username
