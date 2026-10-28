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


def test_skip_after_leaves_processed_posts_alone(tmp_path):
    from datetime import datetime, timezone
    from reel_vault.models import SavedPost
    from reel_vault.state import State, Status

    with State(tmp_path / "s.db") as state:
        for sc in ["A", "B", "C", "D"]:
            state.add_post(SavedPost(sc, "video", "o", datetime(2026, 1, 1, tzinfo=timezone.utc), ""))
        state.save_transcript("D", "already pulled", "en")
        assert state.skip_after(2) == 1  # C skipped; D is past the cut but already processed
        assert state.get_post("C")["status"] == Status.SKIPPED
        assert [r["shortcode"] for r in state.work_queue()] == ["A", "B", "D"]
        assert state.unskip() == 1
        assert state.get_post("C")["status"] == Status.PENDING
