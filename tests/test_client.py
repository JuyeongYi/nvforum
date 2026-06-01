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
