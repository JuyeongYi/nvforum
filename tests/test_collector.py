from nvforum.models import Board, TopicMeta, Post
from nvforum.collector import collect
from nvforum.store import Store


class FakeClient:
    """페이지/토픽을 메모리에서 제공. 활동순(desc) 정렬된 페이지를 가정."""

    def __init__(self, pages, posts_by_topic):
        self.pages = pages  # list[tuple[list[TopicMeta], has_more]]
        self.posts_by_topic = posts_by_topic
        self.fetched_topics = []

    def fetch_category_page(self, board, page):
        return self.pages[page]

    def fetch_topic(self, topic_id):
        self.fetched_topics.append(topic_id)
        return self.posts_by_topic[topic_id]


def _tm(tid, last):
    return TopicMeta(tid, f"t{tid}", f"s{tid}", "2026-01-01T00:00:00Z", last,
                     1, 0, 0, f"u{tid}")


def _p(pid, tid):
    return Post(pid, tid, 1, "u", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
               "<p>x</p>", "x")


def _store():
    s = Store(":memory:")
    s.init_schema()
    return s


def test_collect_stops_at_cutoff():
    pages = [([_tm(1, "2026-05-10T00:00:00Z"), _tm(2, "2026-04-01T00:00:00Z")], False)]
    client = FakeClient(pages, {1: [_p(10, 1)], 2: [_p(20, 2)]})
    store = _store()
    board = Board("Spark", 721, "u")
    store.upsert_board(board)
    summary = collect(client, store, {"Spark": board}, since="2026-05-01")
    assert client.fetched_topics == [1]
    assert summary["Spark"]["topics"] == 1
    assert store.get_last_collected("Spark") == "2026-05-10T00:00:00Z"


def test_collect_full_ignores_cutoff():
    pages = [([_tm(1, "2026-05-10T00:00:00Z"), _tm(2, "2026-04-01T00:00:00Z")], False)]
    client = FakeClient(pages, {1: [_p(10, 1)], 2: [_p(20, 2)]})
    store = _store()
    board = Board("Spark", 721, "u")
    store.upsert_board(board)
    store.set_last_collected("Spark", "2026-09-01T00:00:00Z")
    collect(client, store, {"Spark": board}, full=True)
    assert client.fetched_topics == [1, 2]


def test_collect_uses_incremental_cursor():
    pages = [([_tm(1, "2026-05-10T00:00:00Z"), _tm(2, "2026-04-01T00:00:00Z")], False)]
    client = FakeClient(pages, {1: [_p(10, 1)], 2: [_p(20, 2)]})
    store = _store()
    board = Board("Spark", 721, "u")
    store.upsert_board(board)
    store.set_last_collected("Spark", "2026-05-01T00:00:00Z")
    collect(client, store, {"Spark": board})
    assert client.fetched_topics == [1]


def test_collect_paginates_until_has_more_false():
    page0 = ([_tm(1, "2026-05-10T00:00:00Z")], True)
    page1 = ([_tm(2, "2026-05-09T00:00:00Z")], False)
    client = FakeClient([page0, page1], {1: [_p(10, 1)], 2: [_p(20, 2)]})
    store = _store()
    board = Board("Spark", 721, "u")
    store.upsert_board(board)
    collect(client, store, {"Spark": board}, full=True)
    assert client.fetched_topics == [1, 2]


def test_collect_isolates_board_failure():
    class BoomClient(FakeClient):
        def fetch_category_page(self, board, page):
            if board.alias == "Bad":
                raise RuntimeError("boom")
            return super().fetch_category_page(board, page)

    pages = [([_tm(1, "2026-05-10T00:00:00Z")], False)]
    client = BoomClient(pages, {1: [_p(10, 1)]})
    store = _store()
    good = Board("Spark", 721, "u")
    bad = Board("Bad", 999, "u")
    store.upsert_board(good)
    store.upsert_board(bad)
    summary = collect(client, store, {"Bad": bad, "Spark": good}, full=True)
    assert summary["Bad"]["error"] is not None
    assert summary["Spark"]["topics"] == 1


def test_collect_skips_failing_topic_and_continues():
    pages = [([_tm(1, "2026-05-10T00:00:00Z"), _tm(2, "2026-05-09T00:00:00Z")], False)]

    class PartialClient(FakeClient):
        def fetch_topic(self, topic_id):
            if topic_id == 1:
                raise RuntimeError("404 deleted")
            return super().fetch_topic(topic_id)

    client = PartialClient(pages, {2: [_p(20, 2)]})
    store = _store()
    board = Board("Spark", 721, "u")
    store.upsert_board(board)
    summary = collect(client, store, {"Spark": board}, full=True)
    assert summary["Spark"]["skipped"] == 1
    assert summary["Spark"]["topics"] == 1  # 토픽2는 정상 수집
    assert summary["Spark"]["error"] is None  # 보드는 중단되지 않음
    assert store.get_last_collected("Spark") == "2026-05-10T00:00:00Z"  # 커서 전진


def test_collect_flags_partial_large_topic():
    big = TopicMeta(
        topic_id=1, title="t", slug="s", created_at="2026-01-01T00:00:00Z",
        last_posted_at="2026-05-10T00:00:00Z", posts_count=50, views=0,
        like_count=0, url="u",
    )
    client = FakeClient([([big], False)], {1: [_p(10, 1)]})  # 50개 중 1개만 반환
    store = _store()
    board = Board("Spark", 721, "u")
    store.upsert_board(board)
    summary = collect(client, store, {"Spark": board}, full=True)
    assert summary["Spark"]["partial"] == 1
    assert summary["Spark"]["topics"] == 1
