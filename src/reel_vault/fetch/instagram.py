"""Walk the account's saved posts.

`SavedScan` holds the stop logic and knows nothing about instaloader, so it can
be tested with plain lists. `iter_saved` is the thin instaloader adapter.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from datetime import timezone

import instaloader

from reel_vault.models import SavedPost

KIND_BY_TYPENAME = {
    "GraphVideo": "video",
    "GraphImage": "image",
    "GraphSidecar": "carousel",
}


def to_saved_post(post: instaloader.Post) -> SavedPost:
    kind = KIND_BY_TYPENAME.get(post.typename) or ("video" if post.is_video else "image")
    return SavedPost(
        shortcode=post.shortcode,
        kind=kind,
        owner=_owner_from_node(post),
        posted_at=post.date_utc.replace(tzinfo=timezone.utc),
        caption=post.caption or "",
        hashtags=list(post.caption_hashtags),
    )


def _owner_from_node(post: instaloader.Post) -> str:
    # Post.owner_username fetches the full post metadata (one extra IG request per
    # post) when the saved-feed node lacks the username, which it usually does.
    # Read only what the node already has; the media stage fills in the rest.
    node = getattr(post, "_node", None)
    if node is None:  # test doubles
        return post.owner_username
    return (node.get("owner") or {}).get("username", "")


def iter_saved(loader: instaloader.Instaloader) -> Iterator[SavedPost]:
    """Saved posts, newest save first."""
    profile = instaloader.Profile.own_profile(loader.context)
    for post in profile.get_saved_posts():
        yield to_saved_post(post)


class SavedScan:
    """Iterate saved posts as (post, is_new), stopping early when caught up.

    stop_after_known: stop after this many already-known posts in a row (0 = never).
    limit: stop after this many posts in total (0 = no limit).
    After iteration, `reached_end` says whether the whole saved list was walked.
    """

    def __init__(
        self,
        posts: Iterable[SavedPost],
        is_known: Callable[[str], bool],
        *,
        stop_after_known: int = 0,
        limit: int = 0,
    ):
        self._posts = posts
        self._is_known = is_known
        self._stop_after_known = stop_after_known
        self._limit = limit
        self.reached_end = False
        self.stop_reason: str | None = None

    def __iter__(self) -> Iterator[tuple[SavedPost, bool]]:
        seen = 0
        known_streak = 0
        for post in self._posts:
            is_new = not self._is_known(post.shortcode)
            known_streak = 0 if is_new else known_streak + 1
            seen += 1
            yield post, is_new
            # Checked after yielding so we never request a page we won't use.
            if self._stop_after_known and known_streak >= self._stop_after_known:
                self.stop_reason = "caught up"
                return
            if self._limit and seen >= self._limit:
                self.stop_reason = "limit"
                return
        self.reached_end = True
