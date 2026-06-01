import time

import requests

from nvforum.htmltext import html_to_text
from nvforum.models import Board, TopicMeta, Post

_UA = "NVForum-collector/0.1 (personal research tool)"


def _default_fetch_json(min_interval: float, max_retries: int, sleep):
    session = requests.Session()
    session.headers.update({"User-Agent": _UA})

    def fetch(url: str) -> dict:
        delay = 1.0
        for _ in range(max_retries):
            resp = session.get(url, timeout=30)
            if resp.status_code in (429, 500, 502, 503, 504):
                sleep(delay)
                delay *= 2
                continue
            resp.raise_for_status()
            sleep(min_interval)  # 예의: 성공 후 짧은 간격
            return resp.json()
        resp.raise_for_status()
        return resp.json()

    return fetch


class DiscourseClient:
    def __init__(self, base_url: str, fetch_json=None, min_interval=0.5,
                 max_retries=4, sleep=time.sleep):
        self.base_url = base_url.rstrip("/")
        self.fetch_json = fetch_json or _default_fetch_json(
            min_interval, max_retries, sleep
        )

    def fetch_category_page(self, board: Board, page: int):
        """(topics: list[TopicMeta], has_more: bool) 반환.

        board.url은 끝이 category id인 카테고리 URL이므로 .json만 붙인다.
        예: https://x/c/a/b/721 → https://x/c/a/b/721.json?page=0
        """
        url = f"{board.url.rstrip('/')}.json?page={page}"
        data = self.fetch_json(url)
        tl = data["topic_list"]
        topics = [self._to_topic_meta(t) for t in tl["topics"]]
        return topics, bool(tl.get("more_topics_url"))

    def fetch_topic(self, topic_id: int) -> list[Post]:
        url = f"{self.base_url}/t/{topic_id}.json"
        data = self.fetch_json(url)
        posts = []
        for p in data["post_stream"]["posts"]:
            cooked = p.get("cooked", "")
            posts.append(Post(
                post_id=p["id"], topic_id=topic_id,
                post_number=p.get("post_number", 0),
                username=p.get("username", ""),
                created_at=p["created_at"], updated_at=p.get("updated_at", ""),
                cooked_html=cooked, plain_text=html_to_text(cooked),
            ))
        return posts

    def _to_topic_meta(self, t: dict) -> TopicMeta:
        return TopicMeta(
            topic_id=t["id"], title=t.get("title", ""), slug=t.get("slug", ""),
            created_at=t["created_at"], last_posted_at=t["last_posted_at"],
            posts_count=t.get("posts_count", 0), views=t.get("views", 0),
            like_count=t.get("like_count", 0),
            url=f"{self.base_url}/t/{t.get('slug', '')}/{t['id']}",
            pinned=bool(t.get("pinned", False)),
        )
