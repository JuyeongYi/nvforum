from nvforum.models import Board, TopicMeta, Post


def test_board_holds_fields():
    b = Board(alias="Spark", category_id=721, url="https://x/721")
    assert b.alias == "Spark" and b.category_id == 721


def test_topicmeta_and_post_construct():
    t = TopicMeta(
        topic_id=1, title="t", slug="s", created_at="2026-01-01T00:00:00Z",
        last_posted_at="2026-01-02T00:00:00Z", posts_count=2, views=5,
        like_count=1, url="https://x/t/1",
    )
    p = Post(
        post_id=10, topic_id=1, post_number=1, username="u",
        created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
        cooked_html="<p>hi</p>", plain_text="hi",
    )
    assert t.topic_id == 1 and p.post_id == 10
