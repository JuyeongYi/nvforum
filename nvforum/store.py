import sqlite3

from nvforum.models import Board, TopicMeta, Post

_SCHEMA = """
CREATE TABLE IF NOT EXISTS boards (
    alias TEXT PRIMARY KEY,
    category_id INTEGER NOT NULL,
    url TEXT NOT NULL,
    last_collected_at TEXT
);
CREATE TABLE IF NOT EXISTS topics (
    topic_id INTEGER PRIMARY KEY,
    board_alias TEXT NOT NULL,
    title TEXT, slug TEXT,
    created_at TEXT, last_posted_at TEXT,
    posts_count INTEGER, views INTEGER, like_count INTEGER,
    url TEXT
);
CREATE TABLE IF NOT EXISTS posts (
    post_id INTEGER PRIMARY KEY,
    topic_id INTEGER NOT NULL,
    post_number INTEGER,
    username TEXT,
    created_at TEXT, updated_at TEXT,
    cooked_html TEXT, plain_text TEXT
);
CREATE INDEX IF NOT EXISTS idx_posts_topic ON posts(topic_id);
CREATE INDEX IF NOT EXISTS idx_posts_created ON posts(created_at);
CREATE INDEX IF NOT EXISTS idx_topics_board ON topics(board_alias);
"""


class Store:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA foreign_keys = ON")

    def init_schema(self) -> None:
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def upsert_board(self, board: Board) -> None:
        self.conn.execute(
            """INSERT INTO boards (alias, category_id, url) VALUES (?, ?, ?)
               ON CONFLICT(alias) DO UPDATE SET category_id=excluded.category_id,
                 url=excluded.url""",
            (board.alias, board.category_id, board.url),
        )
        self.conn.commit()

    def get_last_collected(self, alias: str) -> str | None:
        row = self.conn.execute(
            "SELECT last_collected_at FROM boards WHERE alias=?", (alias,)
        ).fetchone()
        return row[0] if row else None

    def set_last_collected(self, alias: str, ts: str) -> None:
        self.conn.execute(
            "UPDATE boards SET last_collected_at=? WHERE alias=?", (ts, alias)
        )
        self.conn.commit()

    def upsert_topic(self, board_alias: str, t: TopicMeta) -> None:
        self.conn.execute(
            """INSERT INTO topics (topic_id, board_alias, title, slug, created_at,
                 last_posted_at, posts_count, views, like_count, url)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(topic_id) DO UPDATE SET
                 title=excluded.title, slug=excluded.slug,
                 last_posted_at=excluded.last_posted_at,
                 posts_count=excluded.posts_count, views=excluded.views,
                 like_count=excluded.like_count, url=excluded.url""",
            (t.topic_id, board_alias, t.title, t.slug, t.created_at,
             t.last_posted_at, t.posts_count, t.views, t.like_count, t.url),
        )
        self.conn.commit()

    def upsert_post(self, p: Post) -> None:
        self.conn.execute(
            """INSERT INTO posts (post_id, topic_id, post_number, username,
                 created_at, updated_at, cooked_html, plain_text)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(post_id) DO UPDATE SET
                 post_number=excluded.post_number, username=excluded.username,
                 updated_at=excluded.updated_at, cooked_html=excluded.cooked_html,
                 plain_text=excluded.plain_text""",
            (p.post_id, p.topic_id, p.post_number, p.username, p.created_at,
             p.updated_at, p.cooked_html, p.plain_text),
        )
        self.conn.commit()

    def stats(self) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for alias, in self.conn.execute("SELECT alias FROM boards"):
            tc = self.conn.execute(
                "SELECT COUNT(*) FROM topics WHERE board_alias=?", (alias,)
            ).fetchone()[0]
            pc = self.conn.execute(
                """SELECT COUNT(*) FROM posts WHERE topic_id IN
                   (SELECT topic_id FROM topics WHERE board_alias=?)""", (alias,)
            ).fetchone()[0]
            latest = self.conn.execute(
                """SELECT MAX(created_at) FROM posts WHERE topic_id IN
                   (SELECT topic_id FROM topics WHERE board_alias=?)""", (alias,)
            ).fetchone()[0]
            result[alias] = {"topics": tc, "posts": pc, "latest_post": latest}
        return result
