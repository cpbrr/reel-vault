from datetime import datetime, timezone

from reel_vault.models import SavedPost
from reel_vault.state import State, Status


def make_post(shortcode: str, kind: str = "video") -> SavedPost:
    return SavedPost(
        shortcode=shortcode,
        kind=kind,
        owner="someone",
        posted_at=datetime(2026, 9, 14, tzinfo=timezone.utc),
        caption="Tip of the day #career",
        hashtags=["career"],
    )


def test_add_post_records_pending_once(tmp_path):
    with State(tmp_path / "state.db") as state:
        assert not state.is_known("ABC")
        assert state.add_post(make_post("ABC")) is True
        assert state.add_post(make_post("ABC")) is False
        assert state.is_known("ABC")
        assert state.counts() == {Status.PENDING: 1}


def test_state_persists_across_connections(tmp_path):
    db = tmp_path / "nested" / "state.db"
    with State(db) as state:
        state.add_post(make_post("ABC"))
        state.mark_backfill_complete()
    with State(db) as state:
        assert state.is_known("ABC")
        assert state.backfill_complete


def test_meta_roundtrip_and_default(tmp_path):
    with State(tmp_path / "state.db") as state:
        assert state.get_meta("missing", "never") == "never"
        assert not state.backfill_complete
        state.set_meta("k", "1")
        state.set_meta("k", "2")
        assert state.get_meta("k") == "2"


def test_post_url_by_kind():
    assert make_post("ABC", "video").url == "https://www.instagram.com/reel/ABC/"
    assert make_post("ABC", "carousel").url == "https://www.instagram.com/p/ABC/"
