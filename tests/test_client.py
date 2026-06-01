import json
from pathlib import Path

from nvforum.client import DiscourseClient
from nvforum.models import Board

FIX = Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_fetch_category_page_parses_topics_and_has_more():
    pages = {"https://x/c/a/b/721.json?page=0": _load("category_page.json")}
    client = DiscourseClient("https://x", fetch_json=lambda url: pages[url])
    board = Board("Spark", 721, "https://x/c/a/b/721")
    topics, has_more = client.fetch_category_page(board, 0)
    assert has_more is True
    assert topics[0].topic_id == 363819
    assert topics[0].last_posted_at == "2026-05-10T14:19:44.134Z"
    assert topics[0].url == "https://x/t/introducing-nvidia-nemoclaw/363819"


def test_fetch_category_page_no_more_when_url_null():
    data = _load("category_page.json")
    data["topic_list"]["more_topics_url"] = None
    client = DiscourseClient("https://x", fetch_json=lambda url: data)
    board = Board("Spark", 721, "https://x/c/a/b/721")
    _, has_more = client.fetch_category_page(board, 0)
    assert has_more is False


def test_fetch_topic_parses_posts_with_plain_text():
    data = _load("topic_detail.json")
    client = DiscourseClient("https://x", fetch_json=lambda url: data)
    posts = client.fetch_topic(363819)
    assert [p.post_id for p in posts] == [1, 2]
    assert posts[0].plain_text == "Hello world"
    assert posts[0].topic_id == 363819


def test_fetch_topic_fetches_remaining_posts_via_stream():
    first = {
        "post_stream": {
            "posts": [
                {"id": 1, "post_number": 1, "username": "a",
                 "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
                 "cooked": "<p>one</p>"},
                {"id": 2, "post_number": 2, "username": "b",
                 "created_at": "2026-01-02T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z",
                 "cooked": "<p>two</p>"},
            ],
            "stream": [1, 2, 3, 4],
        }
    }
    extra = {
        "post_stream": {
            "posts": [
                {"id": 3, "post_number": 3, "username": "c",
                 "created_at": "2026-01-03T00:00:00Z", "updated_at": "2026-01-03T00:00:00Z",
                 "cooked": "<p>three</p>"},
                {"id": 4, "post_number": 4, "username": "d",
                 "created_at": "2026-01-04T00:00:00Z", "updated_at": "2026-01-04T00:00:00Z",
                 "cooked": "<p>four</p>"},
            ]
        }
    }

    def fake(url):
        if url == "https://x/t/99.json":
            return first
        if url.startswith("https://x/t/99/posts.json?"):
            assert "post_ids[]=3" in url and "post_ids[]=4" in url
            return extra
        raise AssertionError(f"unexpected url: {url}")

    client = DiscourseClient("https://x", fetch_json=fake)
    posts = client.fetch_topic(99)
    assert [p.post_id for p in posts] == [1, 2, 3, 4]
    assert posts[2].plain_text == "three"
    assert all(p.topic_id == 99 for p in posts)


def test_fetch_topic_returns_posts_sorted_by_post_number():
    # 첫 페이지가 순서가 어긋나도 post_number로 정렬
    data = {
        "post_stream": {
            "posts": [
                {"id": 2, "post_number": 2, "username": "b",
                 "created_at": "t", "updated_at": "t", "cooked": "<p>b</p>"},
                {"id": 1, "post_number": 1, "username": "a",
                 "created_at": "t", "updated_at": "t", "cooked": "<p>a</p>"},
            ],
            "stream": [1, 2],
        }
    }
    client = DiscourseClient("https://x", fetch_json=lambda url: data)
    posts = client.fetch_topic(5)
    assert [p.post_number for p in posts] == [1, 2]


def test_retry_then_success(monkeypatch):
    calls = {"n": 0}

    class FakeResp:
        def __init__(self, status):
            self.status_code = status

        def json(self):
            return {"ok": True}

        def raise_for_status(self):
            pass

    class FakeSession:
        headers = {}

        def get(self, url, timeout):
            calls["n"] += 1
            return FakeResp(503 if calls["n"] == 1 else 200)

    import nvforum.client as mod
    monkeypatch.setattr(mod.requests, "Session", lambda: FakeSession())
    fetch = mod._default_fetch_json(min_interval=0, max_retries=4, sleep=lambda s: None)
    assert fetch("https://x")["ok"] is True
    assert calls["n"] == 2
