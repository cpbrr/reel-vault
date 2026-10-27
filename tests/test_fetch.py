from datetime import datetime, timezone
from types import SimpleNamespace

from reel_vault.fetch.instagram import SavedScan, to_saved_post
from reel_vault.models import SavedPost


def posts(*shortcodes: str) -> list[SavedPost]:
    when = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [SavedPost(shortcode=s, kind="video", owner="x", posted_at=when) for s in shortcodes]


class Counting:
    """Iterable that records how many items were pulled (i.e. pages requested)."""

    def __init__(self, items):
        self.items = items
        self.pulled = 0

    def __iter__(self):
        for item in self.items:
            self.pulled += 1
            yield item


def test_walks_everything_when_nothing_known():
    scan = SavedScan(posts("a", "b", "c"), lambda s: False, stop_after_known=2)
    result = list(scan)
    assert [(p.shortcode, new) for p, new in result] == [("a", True), ("b", True), ("c", True)]
    assert scan.reached_end
    assert scan.stop_reason is None


def test_stops_after_known_streak_without_pulling_more():
    known = {"c", "d", "e"}
    source = Counting(posts("a", "b", "c", "d", "e", "f", "g"))
    scan = SavedScan(source, known.__contains__, stop_after_known=2)
    result = [p.shortcode for p, _ in scan]
    assert result == ["a", "b", "c", "d"]
    assert source.pulled == 4
    assert not scan.reached_end
    assert scan.stop_reason == "caught up"


def test_known_streak_resets_on_new_post():
    known = {"a", "c"}
    scan = SavedScan(posts("a", "b", "c", "d"), known.__contains__, stop_after_known=2)
    assert len(list(scan)) == 4
    assert scan.reached_end


def test_stop_after_known_zero_never_stops_early():
    scan = SavedScan(posts("a", "b", "c"), lambda s: True, stop_after_known=0)
    assert len(list(scan)) == 3
    assert scan.reached_end


def test_limit():
    source = Counting(posts("a", "b", "c", "d"))
    scan = SavedScan(source, lambda s: False, limit=2)
    assert [p.shortcode for p, _ in scan] == ["a", "b"]
    assert source.pulled == 2
    assert scan.stop_reason == "limit"
    assert not scan.reached_end


def test_to_saved_post_maps_instaloader_fields():
    fake = SimpleNamespace(
        shortcode="XYZ",
        typename="GraphSidecar",
        is_video=False,
        owner_username="chef",
        date_utc=datetime(2026, 5, 2, 10, 30),  # instaloader returns naive UTC
        caption=None,
        caption_hashtags=["recipe"],
    )
    post = to_saved_post(fake)
    assert post.kind == "carousel"
    assert post.caption == ""
    assert post.posted_at.tzinfo is timezone.utc
    assert post.hashtags == ["recipe"]


def test_to_saved_post_unknown_typename_falls_back_to_is_video():
    fake = SimpleNamespace(
        shortcode="R1",
        typename="XDTClipsMedia",
        is_video=True,
        owner_username="c",
        date_utc=datetime(2026, 5, 2),
        caption="hi",
        caption_hashtags=[],
    )
    assert to_saved_post(fake).kind == "video"
