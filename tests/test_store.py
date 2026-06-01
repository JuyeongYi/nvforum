from nvforum.models import Board, TopicMeta, Post
from nvforum.store import Store


def _store():
    s = Store(":memory:")
    s.init_schema()
    return s


def _topic(tid=1, last="2026-05-01T00:00:00Z"):
    return TopicMeta(
        topic_id=tid, title="t", slug="s", created_at="2026-04-01T00:00:00Z",
        last_posted_at=last, posts_count=1, views=0, like_count=0, url="u",
    )


def _post(pid=10, tid=1):
    return Post(
        post_id=pid, topic_id=tid, post_number=1, username="u",
        created_at="2026-04-01T00:00:00Z", updated_at="2026-04-01T00:00:00Z",
        cooked_html="<p>x</p>", plain_text="x",
    )


def test_board_cursor_roundtrip():
    s = _store()
    s.upsert_board(Board("Spark", 721, "u"))
    assert s.get_last_collected("Spark") is None
    s.set_last_collected("Spark", "2026-05-01T00:00:00Z")
    assert s.get_last_collected("Spark") == "2026-05-01T00:00:00Z"


def test_upsert_topic_is_idempotent():
    s = _store()
    s.upsert_board(Board("Spark", 721, "u"))
    s.upsert_topic("Spark", _topic())
    s.upsert_topic("Spark", _topic(last="2026-05-02T00:00:00Z"))
    rows = s.conn.execute("SELECT last_posted_at FROM topics").fetchall()
    assert len(rows) == 1 and rows[0][0] == "2026-05-02T00:00:00Z"


def test_upsert_post_is_idempotent():
    s = _store()
    s.upsert_board(Board("Spark", 721, "u"))
    s.upsert_topic("Spark", _topic())
    s.upsert_post(_post())
    s.upsert_post(_post())
    rows = s.conn.execute("SELECT COUNT(*) FROM posts").fetchall()
    assert rows[0][0] == 1


def test_stats_counts():
    s = _store()
    s.upsert_board(Board("Spark", 721, "u"))
    s.upsert_topic("Spark", _topic())
    s.upsert_post(_post(pid=10))
    s.upsert_post(_post(pid=11))
    stats = s.stats()
    assert stats["Spark"]["topics"] == 1
    assert stats["Spark"]["posts"] == 2
