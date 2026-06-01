from dataclasses import dataclass


@dataclass
class Board:
    alias: str
    category_id: int
    url: str


@dataclass
class TopicMeta:
    topic_id: int
    title: str
    slug: str
    created_at: str
    last_posted_at: str
    posts_count: int
    views: int
    like_count: int
    url: str
    pinned: bool = False


@dataclass
class Post:
    post_id: int
    topic_id: int
    post_number: int
    username: str
    created_at: str
    updated_at: str
    cooked_html: str
    plain_text: str
