# NVForum 백엔드 MCP 서버 Implementation Plan (하위 시스템 ①)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 수집된 `data/forum.db`를 읽고 검색·번역·수집 트리거하는 단일 로컬 백엔드를 MCP(streamable HTTP)로 노출해 AI와 GUI가 동시에 쓰게 한다.

**Architecture:** `query.py`(순수 읽기/쓰기 로직, Store만 의존)를 단일 진실 공급원으로 두고, `mcp_server.py`(FastMCP)가 그 얇은 래퍼로 도구를 노출한다. Store는 SQLite WAL + 쓰기 락으로 동시성을, FTS5 + 트리거로 전문검색을, `text_ko` 컬럼으로 번역을 담당한다.

**Tech Stack:** Python 3.12, `mcp` 1.26 (FastMCP), `uvicorn`, 표준 `sqlite3`(FTS5)/`threading`. 테스트는 `pytest` + `anyio`(이미 설치됨).

---

## 선행 사실 (검증됨)

- `mcp` 1.26.0: `from mcp.server.fastmcp import FastMCP`; `@mcp.tool()` 데코레이터; 동기 도구 함수는 FastMCP가 워커 스레드에서 실행; `mcp.run(transport="streamable-http")`로 서빙; 엔드포인트는 기본 `/mcp`.
- 클라이언트: `from mcp.client.streamable_http import streamablehttp_client`, `from mcp import ClientSession`.
- `uvicorn` 0.38, `anyio` 설치됨. FTS5 사용 가능.
- 기존 `Store.__init__`는 현재 `self.conn = sqlite3.connect(db_path)` 한 줄(이전에 foreign_keys pragma 제거됨). 기존 메서드: `init_schema, close, upsert_board, get_last_collected, set_last_collected, upsert_topic, upsert_post, stats`. `_SCHEMA`에 boards/topics/posts + 인덱스 정의.
- 기존 `models.py`: `TopicMeta`에 `pinned: bool = False` 포함. `Post(post_id, topic_id, post_number, username, created_at, updated_at, cooked_html, plain_text)`.

## 파일 구조

```
nvforum/query.py        # 신규: 읽기/쓰기 쿼리 함수 (Store만 의존, dict 반환)
nvforum/mcp_server.py   # 신규: FastMCP 도구 = query 래퍼 + serve 진입
nvforum/store.py        # 변경: WAL/락/row_factory, text_ko 컬럼+마이그레이션, FTS5+트리거, reindex, search, save_translation, 읽기 메서드
nvforum/cli.py          # 변경: serve / reindex 명령
pyproject.toml          # 변경: mcp, uvicorn 의존성
tests/test_store_fts.py        # 신규
tests/test_store_translation.py# 신규
tests/test_query.py            # 신규
tests/test_mcp_server.py       # 신규
```

---

### Task 1: 의존성 + Store 동시성/마이그레이션/번역

**Files:**
- Modify: `pyproject.toml`
- Modify: `nvforum/store.py`
- Test: `tests/test_store_translation.py`

- [ ] **Step 1: pyproject 의존성 추가**

`pyproject.toml`의 `dependencies` 배열을 다음으로 변경:
```toml
dependencies = ["requests>=2.31", "PyYAML>=6.0", "mcp>=1.20", "uvicorn>=0.30"]
```
그 후 `pip install -e ".[dev]"` 실행(설치 확인).

- [ ] **Step 2: 실패하는 테스트 작성 — `tests/test_store_translation.py`**

```python
import threading
from nvforum.models import Board, TopicMeta, Post
from nvforum.store import Store


def _seed():
    s = Store(":memory:")
    s.init_schema()
    s.upsert_board(Board("Spark", 721, "u"))
    s.upsert_topic("Spark", TopicMeta(1, "t", "s", "2026-04-01T00:00:00Z",
                   "2026-05-01T00:00:00Z", 1, 0, 0, "u"))
    s.upsert_post(Post(10, 1, 1, "u", "2026-04-01T00:00:00Z",
                  "2026-04-01T00:00:00Z", "<p>hi</p>", "hi"))
    return s


def test_new_post_has_null_translation():
    s = _seed()
    row = s.conn.execute("SELECT text_ko, text_ko_at FROM posts WHERE post_id=10").fetchone()
    assert row["text_ko"] is None and row["text_ko_at"] is None


def test_save_translation_sets_text_and_timestamp():
    s = _seed()
    s.save_translation(10, "안녕", at="2026-06-02T00:00:00Z")
    row = s.conn.execute("SELECT text_ko, text_ko_at FROM posts WHERE post_id=10").fetchone()
    assert row["text_ko"] == "안녕"
    assert row["text_ko_at"] == "2026-06-02T00:00:00Z"


def test_row_factory_allows_named_and_index_access():
    s = _seed()
    row = s.conn.execute("SELECT post_id, username FROM posts").fetchone()
    assert row["post_id"] == 10 and row[0] == 10  # sqlite3.Row: 이름·인덱스 둘 다


def test_write_lock_serializes_concurrent_saves():
    s = _seed()
    s.upsert_post(Post(11, 1, 2, "u", "2026-04-02T00:00:00Z",
                  "2026-04-02T00:00:00Z", "<p>x</p>", "x"))
    errors = []

    def worker(pid, txt):
        try:
            s.save_translation(pid, txt, at="2026-06-02T00:00:00Z")
        except Exception as e:  # 동시 쓰기 충돌이 나면 안 됨
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(10, "가")),
               threading.Thread(target=worker, args=(11, "나"))]
    for t in threads: t.start()
    for t in threads: t.join()
    assert not errors
    vals = {r["post_id"]: r["text_ko"]
            for r in s.conn.execute("SELECT post_id, text_ko FROM posts")}
    assert vals == {10: "가", 11: "나"}
```

- [ ] **Step 3: 실패 확인**

Run: `python -m pytest tests/test_store_translation.py -v`
Expected: FAIL (`text_ko` 컬럼 없음 / `save_translation` 없음 / Row 접근 실패)

- [ ] **Step 4: store.py 변경**

상단 import에 `threading` 추가(파일 맨 위 `import sqlite3` 아래):
```python
import sqlite3
import threading
```

`_SCHEMA`의 posts 테이블 정의에 번역 컬럼 추가(기존 `cooked_html TEXT, plain_text TEXT` 줄 뒤에 컬럼 2개):
```python
CREATE TABLE IF NOT EXISTS posts (
    post_id INTEGER PRIMARY KEY,
    topic_id INTEGER NOT NULL,
    post_number INTEGER,
    username TEXT,
    created_at TEXT, updated_at TEXT,
    cooked_html TEXT, plain_text TEXT,
    text_ko TEXT, text_ko_at TEXT
);
```

`__init__`을 다음으로 교체:
```python
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self._write_lock = threading.Lock()
```

`init_schema`을 다음으로 교체(스크립트 실행 후 기존 DB 컬럼 보강):
```python
    def init_schema(self) -> None:
        self.conn.executescript(_SCHEMA)
        self._ensure_columns()
        self.conn.commit()

    def _ensure_columns(self) -> None:
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(posts)")}
        if "text_ko" not in cols:
            self.conn.execute("ALTER TABLE posts ADD COLUMN text_ko TEXT")
        if "text_ko_at" not in cols:
            self.conn.execute("ALTER TABLE posts ADD COLUMN text_ko_at TEXT")
```

기존 쓰기 메서드(`upsert_board`, `set_last_collected`, `upsert_topic`, `upsert_post`)의 본문 전체를 `with self._write_lock:`으로 감싼다. 예) `upsert_post`:
```python
    def upsert_post(self, p: Post) -> None:
        with self._write_lock:
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
```
(같은 방식으로 `upsert_board`, `set_last_collected`, `upsert_topic`도 `with self._write_lock:`로 감싼다. 들여쓰기만 추가하고 SQL은 그대로.)

새 메서드 추가(`stats` 위/아래 아무 곳):
```python
    def save_translation(self, post_id: int, text_ko: str, at: str) -> None:
        with self._write_lock:
            self.conn.execute(
                "UPDATE posts SET text_ko=?, text_ko_at=? WHERE post_id=?",
                (text_ko, at, post_id),
            )
            self.conn.commit()
```

- [ ] **Step 5: 통과 확인 + 회귀 없음**

Run: `python -m pytest tests/test_store_translation.py -v && python -m pytest -q`
Expected: 신규 4개 PASS, 전체 PASS(기존 36 + 4 = 40). `sqlite3.Row`는 인덱스 접근을 지원하므로 기존 store 테스트(`rows[0][0]`, `for alias, in ...`)도 통과.

- [ ] **Step 6: 커밋**

```
git -c user.name="jylee" -c user.email="jylee@krafton.com" add pyproject.toml nvforum/store.py tests/test_store_translation.py
git -c user.name="jylee" -c user.email="jylee@krafton.com" commit -m "feat: Store 동시성(WAL/락/Row)·text_ko 번역 컬럼

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: FTS5 전문검색 + reindex + search

**Files:**
- Modify: `nvforum/store.py`
- Test: `tests/test_store_fts.py`

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/test_store_fts.py`**

```python
from nvforum.models import Board, TopicMeta, Post
from nvforum.store import Store


def _store():
    s = Store(":memory:")
    s.init_schema()
    s.upsert_board(Board("Spark", 721, "u"))
    s.upsert_topic("Spark", TopicMeta(1, "Alpha", "s", "2026-04-01T00:00:00Z",
                   "2026-05-01T00:00:00Z", 2, 0, 0, "u"))
    return s


def _post(pid, text, created="2026-04-01T00:00:00Z"):
    return Post(pid, 1, pid, "u", created, created, f"<p>{text}</p>", text)


def test_search_finds_inserted_post():
    s = _store()
    s.upsert_post(_post(10, "the quick brown fox"))
    s.upsert_post(_post(11, "lazy dog sleeps"))
    rows = s.search("fox")
    assert [r["post_id"] for r in rows] == [10]
    assert "fox" in rows[0]["snippet"].lower()
    assert rows[0]["topic_title"] == "Alpha"


def test_search_reflects_update_and_delete():
    s = _store()
    s.upsert_post(_post(10, "alpha keyword here"))
    # 업데이트: 본문 교체 후 옛 키워드는 안 잡히고 새 키워드는 잡힘
    s.upsert_post(_post(10, "beta replaced text"))
    assert s.search("alpha") == []
    assert [r["post_id"] for r in s.search("beta")] == [10]
    # 삭제
    s.conn.execute("DELETE FROM posts WHERE post_id=10"); s.conn.commit()
    assert s.search("beta") == []


def test_search_filters_board_and_since():
    s = _store()
    s.upsert_post(_post(10, "needle one", created="2026-04-01T00:00:00Z"))
    s.upsert_post(_post(11, "needle two", created="2026-05-10T00:00:00Z"))
    rows = s.search("needle", since="2026-05-01")
    assert [r["post_id"] for r in rows] == [11]
    assert s.search("needle", board="Nope") == []


def test_reindex_populates_fts_for_preexisting_rows():
    # 트리거 없이 들어간 행을 가정: posts에 직접 INSERT 후 reindex
    s = _store()
    s.conn.execute(
        "INSERT INTO posts (post_id, topic_id, post_number, username, created_at, "
        "updated_at, cooked_html, plain_text) VALUES (10,1,1,'u','t','t','<p>z</p>','zeta unique')"
    )
    s.conn.commit()
    assert s.search("zeta") == []  # 아직 인덱스 안 됨(직접 INSERT는 트리거 우회 아님 → 사실 트리거가 잡음)
    s.reindex()
    assert [r["post_id"] for r in s.search("zeta")] == [10]
```
주의: 직접 INSERT도 트리거가 동작하므로 `test_reindex_...`의 첫 assert는 환경에 따라 이미 잡힐 수 있다. 그 경우 첫 `assert s.search("zeta") == []` 줄을 제거하고 reindex 후 검색만 확인하도록 조정한다(아래 Step 4 구현 후 Step 5에서 실제 동작에 맞춰 정리).

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_store_fts.py -v`
Expected: FAIL (`search`/`reindex` 없음, posts_fts 없음)

- [ ] **Step 3: store.py에 FTS 스키마/트리거/메서드 추가**

`_SCHEMA` 문자열 끝(마지막 인덱스 정의 뒤)에 FTS 가상 테이블과 트리거 추가:
```sql
CREATE VIRTUAL TABLE IF NOT EXISTS posts_fts
    USING fts5(plain_text, content='posts', content_rowid='post_id');
CREATE TRIGGER IF NOT EXISTS posts_ai AFTER INSERT ON posts BEGIN
    INSERT INTO posts_fts(rowid, plain_text) VALUES (new.post_id, new.plain_text);
END;
CREATE TRIGGER IF NOT EXISTS posts_ad AFTER DELETE ON posts BEGIN
    INSERT INTO posts_fts(posts_fts, rowid, plain_text)
      VALUES('delete', old.post_id, old.plain_text);
END;
CREATE TRIGGER IF NOT EXISTS posts_au AFTER UPDATE ON posts BEGIN
    INSERT INTO posts_fts(posts_fts, rowid, plain_text)
      VALUES('delete', old.post_id, old.plain_text);
    INSERT INTO posts_fts(rowid, plain_text) VALUES (new.post_id, new.plain_text);
END;
```

`store.py`에 메서드 추가:
```python
    def reindex(self) -> None:
        with self._write_lock:
            self.conn.execute("INSERT INTO posts_fts(posts_fts) VALUES('rebuild')")
            self.conn.commit()

    def search(self, query: str, board: str | None = None,
               since: str | None = None, limit: int = 50) -> list[dict]:
        sql = (
            "SELECT p.post_id AS post_id, p.topic_id AS topic_id, "
            "  t.title AS topic_title, p.username AS username, "
            "  p.created_at AS created_at, p.text_ko AS text_ko, "
            "  snippet(posts_fts, 0, '[', ']', '…', 12) AS snippet "
            "FROM posts_fts "
            "JOIN posts p ON p.post_id = posts_fts.rowid "
            "JOIN topics t ON t.topic_id = p.topic_id "
            "WHERE posts_fts MATCH ?"
        )
        params: list = [query]
        if board is not None:
            sql += " AND t.board_alias = ?"; params.append(board)
        if since is not None:
            sql += " AND p.created_at >= ?"; params.append(since)
        sql += " ORDER BY rank LIMIT ?"; params.append(limit)
        return [dict(r) for r in self.conn.execute(sql, params)]
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_store_fts.py -v`
Expected: PASS. (직접 INSERT가 트리거로 이미 인덱싱되면 `test_reindex_...`의 첫 `assert ... == []`를 제거하고 reindex 후 검색만 확인하도록 수정.)

- [ ] **Step 5: 전체 회귀 + 커밋**

Run: `python -m pytest -q` (전체 PASS, 40 + 4 = 44 전후)
```
git -c user.name="jylee" -c user.email="jylee@krafton.com" add nvforum/store.py tests/test_store_fts.py
git -c user.name="jylee" -c user.email="jylee@krafton.com" commit -m "feat: FTS5 전문검색·트리거 동기화·reindex

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: query.py — 읽기 함수 (list_topics/get_topic/posts_since/untranslated)

먼저 store에 읽기 헬퍼를 추가하고, query.py가 정책(기본값·정렬·형태)을 입힌다.

**Files:**
- Modify: `nvforum/store.py` (읽기 헬퍼)
- Create: `nvforum/query.py`
- Test: `tests/test_query.py`

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/test_query.py`**

```python
from nvforum.models import Board, TopicMeta, Post
from nvforum.store import Store
from nvforum import query


def _store():
    s = Store(":memory:")
    s.init_schema()
    s.upsert_board(Board("Spark", 721, "https://x/c/a/b/721"))
    s.upsert_topic("Spark", TopicMeta(1, "Alpha", "alpha", "2026-04-01T00:00:00Z",
                   "2026-05-10T00:00:00Z", 2, 7, 1, "https://x/t/alpha/1"))
    s.upsert_topic("Spark", TopicMeta(2, "Beta", "beta", "2026-03-01T00:00:00Z",
                   "2026-03-02T00:00:00Z", 1, 0, 0, "https://x/t/beta/2"))
    s.upsert_post(Post(10, 1, 1, "alice", "2026-04-01T00:00:00Z",
                  "2026-04-01T00:00:00Z", "<p>hello</p>", "hello"))
    s.upsert_post(Post(11, 1, 2, "bob", "2026-05-10T00:00:00Z",
                  "2026-05-10T00:00:00Z", "<p>world</p>", "world"))
    s.upsert_post(Post(20, 2, 1, "carol", "2026-03-01T00:00:00Z",
                  "2026-03-01T00:00:00Z", "<p>old</p>", "old"))
    return s


def test_list_topics_recent_order_and_fields():
    s = _store()
    rows = query.list_topics(s)
    assert [r["topic_id"] for r in rows] == [1, 2]  # last_posted_at desc
    assert rows[0]["title"] == "Alpha" and rows[0]["views"] == 7
    assert rows[0]["board"] == "Spark"


def test_list_topics_filters_board_and_since_and_limit():
    s = _store()
    assert [r["topic_id"] for r in query.list_topics(s, since="2026-05-01")] == [1]
    assert query.list_topics(s, board="Nope") == []
    assert len(query.list_topics(s, limit=1)) == 1


def test_get_topic_returns_topic_and_posts_in_order():
    s = _store()
    s.save_translation(10, "안녕", at="2026-06-02T00:00:00Z")
    result = query.get_topic(s, 1)
    assert result["topic"]["title"] == "Alpha"
    assert [p["post_number"] for p in result["posts"]] == [1, 2]
    assert result["posts"][0]["text_ko"] == "안녕"
    assert result["posts"][0]["plain_text"] == "hello"


def test_get_topic_missing_returns_none_topic():
    s = _store()
    assert query.get_topic(s, 999)["topic"] is None


def test_posts_since_chronological():
    s = _store()
    rows = query.posts_since(s, "2026-04-01")
    assert [r["post_id"] for r in rows] == [10, 11]  # created_at asc, old(20) 제외
    assert rows[0]["topic_title"] == "Alpha"


def test_untranslated_posts_lists_only_null_text_ko():
    s = _store()
    s.save_translation(10, "안녕", at="2026-06-02T00:00:00Z")
    rows = query.untranslated_posts(s)
    ids = {r["post_id"] for r in rows}
    assert 10 not in ids and {11, 20} <= ids


def test_untranslated_posts_filter_topic():
    s = _store()
    rows = query.untranslated_posts(s, topic_id=2)
    assert {r["post_id"] for r in rows} == {20}
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_query.py -v`
Expected: FAIL (`nvforum.query` 없음)

- [ ] **Step 3: store.py에 읽기 헬퍼 추가**

```python
    def topics_rows(self, board=None, since=None, order="recent", limit=50, offset=0):
        sql = ("SELECT topic_id, board_alias AS board, title, slug, created_at, "
               "last_posted_at, posts_count, views, like_count, url FROM topics")
        params: list = []
        clauses = []
        if board is not None:
            clauses.append("board_alias = ?"); params.append(board)
        if since is not None:
            clauses.append("last_posted_at >= ?"); params.append(since)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY last_posted_at " + ("ASC" if order == "oldest" else "DESC")
        sql += " LIMIT ? OFFSET ?"; params += [limit, offset]
        return [dict(r) for r in self.conn.execute(sql, params)]

    def topic_row(self, topic_id):
        r = self.conn.execute(
            "SELECT topic_id, board_alias AS board, title, slug, created_at, "
            "last_posted_at, posts_count, views, like_count, url FROM topics "
            "WHERE topic_id=?", (topic_id,)).fetchone()
        return dict(r) if r else None

    def posts_rows(self, topic_id):
        return [dict(r) for r in self.conn.execute(
            "SELECT post_id, topic_id, post_number, username, created_at, "
            "updated_at, cooked_html, plain_text, text_ko, text_ko_at "
            "FROM posts WHERE topic_id=? ORDER BY post_number", (topic_id,))]

    def posts_since_rows(self, since, board=None, limit=200):
        sql = ("SELECT p.post_id, p.topic_id, t.title AS topic_title, p.username, "
               "p.created_at, p.plain_text, p.text_ko "
               "FROM posts p JOIN topics t ON t.topic_id=p.topic_id "
               "WHERE p.created_at >= ?")
        params: list = [since]
        if board is not None:
            sql += " AND t.board_alias = ?"; params.append(board)
        sql += " ORDER BY p.created_at ASC LIMIT ?"; params.append(limit)
        return [dict(r) for r in self.conn.execute(sql, params)]

    def untranslated_rows(self, board=None, topic_id=None, limit=50):
        sql = ("SELECT p.post_id, p.topic_id, p.plain_text "
               "FROM posts p JOIN topics t ON t.topic_id=p.topic_id "
               "WHERE p.text_ko IS NULL")
        params: list = []
        if board is not None:
            sql += " AND t.board_alias = ?"; params.append(board)
        if topic_id is not None:
            sql += " AND p.topic_id = ?"; params.append(topic_id)
        sql += " ORDER BY p.created_at ASC LIMIT ?"; params.append(limit)
        return [dict(r) for r in self.conn.execute(sql, params)]
```

- [ ] **Step 4: query.py 작성**

```python
"""읽기/쓰기 쿼리 계층. Store만 의존하며 JSON 직렬화 가능한 dict를 반환한다.
MCP 서버와 GUI가 공유하는 단일 로직."""


def list_topics(store, board=None, since=None, order="recent", limit=50, offset=0):
    return store.topics_rows(board=board, since=since, order=order,
                             limit=limit, offset=offset)


def get_topic(store, topic_id):
    return {"topic": store.topic_row(topic_id), "posts": store.posts_rows(topic_id)}


def posts_since(store, since, board=None, limit=200):
    return store.posts_since_rows(since, board=board, limit=limit)


def untranslated_posts(store, board=None, topic_id=None, limit=50):
    return store.untranslated_rows(board=board, topic_id=topic_id, limit=limit)
```

- [ ] **Step 5: 통과 확인 + 커밋**

Run: `python -m pytest tests/test_query.py -v && python -m pytest -q`
Expected: 신규 7개 + 전체 PASS.
```
git -c user.name="jylee" -c user.email="jylee@krafton.com" add nvforum/store.py nvforum/query.py tests/test_query.py
git -c user.name="jylee" -c user.email="jylee@krafton.com" commit -m "feat: query.py 읽기 함수와 store 읽기 헬퍼

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: query.py — search_posts / save_translation / collect 래퍼

**Files:**
- Modify: `nvforum/query.py`
- Test: `tests/test_query.py` (추가)

- [ ] **Step 1: 테스트 추가 — `tests/test_query.py` 끝에 append**

```python
def test_search_posts_returns_snippets():
    s = _store()
    rows = query.search_posts(s, "world")
    assert [r["post_id"] for r in rows] == [11]
    assert "snippet" in rows[0] and rows[0]["topic_title"] == "Alpha"


def test_save_translation_records_timestamp(monkeypatch):
    s = _store()
    out = query.save_translation(s, 11, "세계", now=lambda: "2026-06-02T12:00:00Z")
    assert out == {"post_id": 11, "saved": True}
    row = s.conn.execute("SELECT text_ko, text_ko_at FROM posts WHERE post_id=11").fetchone()
    assert row["text_ko"] == "세계" and row["text_ko_at"] == "2026-06-02T12:00:00Z"


def test_collect_wrapper_invokes_collector(monkeypatch, tmp_path):
    # boards.yaml 작성
    bp = tmp_path / "boards.yaml"
    bp.write_text('boards:\n  Spark: "https://x/c/a/b/721"\n', encoding="utf-8")
    s = _store()
    calls = {}

    def fake_collect(client, store, boards, since=None, full=False):
        calls["boards"] = list(boards)
        calls["since"] = since
        calls["full"] = full
        return {"Spark": {"topics": 3, "posts": 9, "skipped": 0, "partial": 0, "error": None}}

    import nvforum.query as q
    monkeypatch.setattr(q, "_run_collect", fake_collect)
    summary = query.collect(s, str(bp), board="Spark", since="2026-05-01")
    assert summary["Spark"]["topics"] == 3
    assert calls["boards"] == ["Spark"] and calls["since"] == "2026-05-01"
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_query.py -k "search_posts or save_translation or collect_wrapper" -v`
Expected: FAIL (함수 없음)

- [ ] **Step 3: query.py에 추가**

상단에 import 추가, 함수 추가:
```python
from datetime import datetime, timezone

from nvforum.client import DiscourseClient
from nvforum.collector import collect as _run_collect
from nvforum.config import load_boards

_BASE_URL = "https://forums.developer.nvidia.com"


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def search_posts(store, query, board=None, since=None, limit=50):
    return store.search(query, board=board, since=since, limit=limit)


def save_translation(store, post_id, text_ko, now=_utc_now):
    store.save_translation(post_id, text_ko, at=now())
    return {"post_id": post_id, "saved": True}


def collect(store, boards_path, board=None, since=None, full=False):
    all_boards = load_boards(boards_path)
    targets = all_boards if board in (None, "*", "all") else {
        a: all_boards[a] for a in board.split(",") if a.strip() in all_boards}
    for b in targets.values():
        store.upsert_board(b)
    client = DiscourseClient(_BASE_URL)
    return _run_collect(client, store, targets, since=since, full=full)
```
주의: `search_posts`의 매개변수 `query`가 모듈명 `query`와 무관(이 파일 내부 함수). 모듈 자기참조는 없으므로 충돌 없음.

- [ ] **Step 4: 통과 확인 + 커밋**

Run: `python -m pytest tests/test_query.py -v && python -m pytest -q`
Expected: PASS.
```
git -c user.name="jylee" -c user.email="jylee@krafton.com" add nvforum/query.py tests/test_query.py
git -c user.name="jylee" -c user.email="jylee@krafton.com" commit -m "feat: query.py 검색·번역저장·수집 래퍼

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: MCP 서버 (`mcp_server.py`)

**Files:**
- Create: `nvforum/mcp_server.py`
- Test: `tests/test_mcp_server.py`

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/test_mcp_server.py`**

```python
import anyio
from nvforum.models import Board, TopicMeta, Post
from nvforum.store import Store
from nvforum.mcp_server import build_mcp


def _store():
    s = Store(":memory:")
    s.init_schema()
    s.upsert_board(Board("Spark", 721, "https://x/c/a/b/721"))
    s.upsert_topic("Spark", TopicMeta(1, "Alpha", "alpha", "2026-04-01T00:00:00Z",
                   "2026-05-10T00:00:00Z", 1, 0, 0, "https://x/t/alpha/1"))
    s.upsert_post(Post(10, 1, 1, "alice", "2026-05-10T00:00:00Z",
                  "2026-05-10T00:00:00Z", "<p>hello world</p>", "hello world"))
    return s


def test_build_mcp_registers_expected_tools():
    mcp = build_mcp(_store(), boards_path="boards.yaml")
    names = {t.name for t in anyio.run(mcp.list_tools)}
    assert {"list_topics", "get_topic", "search_posts", "posts_since",
            "list_untranslated", "save_translation", "collect"} <= names


def test_search_tool_roundtrip_in_memory():
    mcp = build_mcp(_store(), boards_path="boards.yaml")
    result = anyio.run(mcp.call_tool, "search_posts", {"query": "world"})
    # FastMCP.call_tool → (content_list, structured_dict) 또는 유사. 구조화 결과에서 확인.
    structured = result[1] if isinstance(result, tuple) else result
    # 구조화 결과는 {"result": [...]} 형태(반환 list가 result 키로 감싸짐)
    payload = structured["result"] if isinstance(structured, dict) and "result" in structured else structured
    ids = [row["post_id"] for row in payload]
    assert ids == [10]
```
주의: FastMCP 버전에 따라 `call_tool` 반환 형태가 다를 수 있다. Step 4에서 실제 반환을 출력해 보고 `test_search_tool_roundtrip_in_memory`의 언래핑을 실제 형태에 맞춰 1줄 조정한다(도구 자체는 그대로). `test_build_mcp_registers_expected_tools`는 버전 무관하게 통과해야 한다.

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_mcp_server.py -v`
Expected: FAIL (`nvforum.mcp_server` 없음)

- [ ] **Step 3: mcp_server.py 작성**

```python
"""MCP 서버 — query.py를 도구로 노출. streamable HTTP로 서빙.

AI(Claude)와 GUI가 같은 /mcp 엔드포인트에 접속한다."""
from mcp.server.fastmcp import FastMCP

from nvforum import query
from nvforum.store import Store

_DEFAULT_BOARDS = "boards.yaml"


def build_mcp(store: Store, boards_path: str = _DEFAULT_BOARDS,
              host: str = "127.0.0.1", port: int = 8000) -> FastMCP:
    mcp = FastMCP("nvforum", host=host, port=port)

    @mcp.tool()
    def list_topics(board: str | None = None, since: str | None = None,
                    order: str = "recent", limit: int = 50, offset: int = 0) -> list:
        """게시판 토픽 목록. board(별칭)·since(YYYY-MM-DD)·order(recent|oldest) 필터."""
        return query.list_topics(store, board=board, since=since, order=order,
                                 limit=limit, offset=offset)

    @mcp.tool()
    def get_topic(topic_id: int) -> dict:
        """특정 토픽과 그 글 전체(원문 plain_text + 번역 text_ko)."""
        return query.get_topic(store, topic_id)

    @mcp.tool()
    def search_posts(query_text: str, board: str | None = None,
                     since: str | None = None, limit: int = 50) -> list:
        """글 본문 전문검색(FTS5). query_text 키워드로 스니펫과 함께 반환."""
        return query.search_posts(store, query_text, board=board,
                                  since=since, limit=limit)

    @mcp.tool()
    def posts_since(since: str, board: str | None = None, limit: int = 200) -> list:
        """특정 시점(YYYY-MM-DD) 이후 글을 시간순으로 나열."""
        return query.posts_since(store, since, board=board, limit=limit)

    @mcp.tool()
    def list_untranslated(board: str | None = None, topic_id: int | None = None,
                          limit: int = 50) -> list:
        """아직 한국어 번역(text_ko)이 없는 글 목록. 번역 작업 대상."""
        return query.untranslated_posts(store, board=board, topic_id=topic_id,
                                        limit=limit)

    @mcp.tool()
    def save_translation(post_id: int, text_ko: str) -> dict:
        """글의 한국어 번역을 저장한다. 호출자(AI)가 번역문을 제공."""
        return query.save_translation(store, post_id, text_ko)

    @mcp.tool()
    def collect(board: str | None = None, since: str | None = None,
                full: bool = False) -> dict:
        """포럼에서 글을 수집(증분/--since/--full). board는 별칭/*/콤마목록."""
        return query.collect(store, boards_path, board=board, since=since, full=full)

    return mcp


def serve(db_path: str = "data/forum.db", boards_path: str = _DEFAULT_BOARDS,
          host: str = "127.0.0.1", port: int = 8000) -> None:
    import os
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    store = Store(db_path)
    store.init_schema()
    mcp = build_mcp(store, boards_path=boards_path, host=host, port=port)
    mcp.run(transport="streamable-http")
```
주의: `search_posts` 도구의 인자명은 `query_text`로 둔다(모듈 `query`와의 혼동 방지). 도구 설명에 키워드 의미를 적었다.

- [ ] **Step 4: 통과 확인 (반환 형태 정렬)**

Run: `python -m pytest tests/test_mcp_server.py::test_build_mcp_registers_expected_tools -v`
Expected: PASS.

그 다음 `call_tool` 실제 반환을 1회 확인:
Run: `python -X utf8 -c "import anyio; from tests.test_mcp_server import _store; from nvforum.mcp_server import build_mcp; m=build_mcp(_store()); print(repr(anyio.run(m.call_tool, 'search_posts', {'query_text':'world'})))"`
출력 형태에 맞춰 `test_search_tool_roundtrip_in_memory`의 언래핑 줄을 조정(인자명도 `query_text`로 수정)한 뒤:
Run: `python -m pytest tests/test_mcp_server.py -v`
Expected: PASS.

- [ ] **Step 5: 전체 회귀 + 커밋**

Run: `python -m pytest -q`
```
git -c user.name="jylee" -c user.email="jylee@krafton.com" add nvforum/mcp_server.py tests/test_mcp_server.py
git -c user.name="jylee" -c user.email="jylee@krafton.com" commit -m "feat: MCP 서버(FastMCP) — query 도구 노출

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: CLI — serve / reindex

**Files:**
- Modify: `nvforum/cli.py`
- Test: `tests/test_cli.py` (추가)

- [ ] **Step 1: 테스트 추가 — `tests/test_cli.py` 끝에 append**

```python
def test_reindex_command_runs(tmp_path, capsys, monkeypatch):
    from nvforum.models import Board, TopicMeta, Post
    from nvforum.store import Store
    db = tmp_path / "f.db"
    s = Store(str(db)); s.init_schema()
    s.upsert_board(Board("Spark", 721, "u"))
    s.upsert_topic("Spark", TopicMeta(1, "t", "s", "2026-01-01T00:00:00Z",
                   "2026-01-01T00:00:00Z", 1, 0, 0, "u"))
    s.upsert_post(Post(10, 1, 1, "u", "2026-01-01T00:00:00Z",
                  "2026-01-01T00:00:00Z", "<p>findme token</p>", "findme token"))
    s.close()
    from nvforum.cli import main
    rc = main(["--db", str(db), "reindex"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "재색인" in out or "reindex" in out.lower()


def test_serve_command_wires_serve(monkeypatch):
    called = {}

    def fake_serve(db_path, boards_path, host, port):
        called.update(db_path=db_path, host=host, port=port)

    import nvforum.cli as climod
    monkeypatch.setattr(climod, "_serve", fake_serve)
    from nvforum.cli import main
    rc = main(["--db", "d.db", "serve", "--host", "127.0.0.1", "--port", "9001"])
    assert rc == 0
    assert called["port"] == 9001 and called["host"] == "127.0.0.1"
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_cli.py -k "reindex or serve" -v`
Expected: FAIL (명령 없음)

- [ ] **Step 3: cli.py 변경**

상단 import 근처에 추가:
```python
from nvforum.mcp_server import serve as _serve
```

명령 함수 추가(`cmd_stats` 아래):
```python
def cmd_reindex(args) -> int:
    store = _open_store(args.db)
    store.reindex()
    store.close()
    print("FTS 재색인 완료")
    return 0


def cmd_serve(args) -> int:
    _serve(args.db, args.boards, args.host, args.port)
    return 0
```

`build_parser`의 서브파서 등록부에 추가(`stats` 등록 줄들 아래, `return p` 위):
```python
    sub.add_parser("reindex", help="FTS 인덱스 재색인").set_defaults(func=cmd_reindex)

    sv = sub.add_parser("serve", help="MCP 백엔드 서버 실행")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.set_defaults(func=cmd_serve)
```

- [ ] **Step 4: 통과 확인 + 커밋**

Run: `python -m pytest tests/test_cli.py -v && python -m pytest -q`
Expected: PASS.
```
git -c user.name="jylee" -c user.email="jylee@krafton.com" add nvforum/cli.py tests/test_cli.py
git -c user.name="jylee" -c user.email="jylee@krafton.com" commit -m "feat: CLI serve/reindex 명령

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: 라이브 통합 점검 + 등록 문서 (수동)

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 기존 DB 재색인**

Run: `python -X utf8 -m nvforum.cli reindex`
Expected: `FTS 재색인 완료` (기존 data/forum.db의 2550개 글이 인덱싱됨)

- [ ] **Step 2: 서버 기동(백그라운드) 후 클라이언트 왕복**

서버를 백그라운드로 실행:
Run(백그라운드): `python -m nvforum.cli serve --port 8000`

별도로 클라이언트 스크립트로 도구 호출 확인:
```python
# scripts/smoke_client.py (임시, 커밋 안 함)
import anyio
from mcp.client.streamable_http import streamablehttp_client
from mcp import ClientSession

async def main():
    async with streamablehttp_client("http://127.0.0.1:8000/mcp") as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print("tools:", [t.name for t in tools.tools])
            res = await s.call_tool("search_posts", {"query_text": "Spark"})
            print("search structured:", res.structuredContent)

anyio.run(main)
```
Run: `python -X utf8 scripts/smoke_client.py`
Expected: 7개 도구 이름 출력 + Spark 검색 결과(구조화) 출력. 확인 후 서버 종료.

- [ ] **Step 3: Claude Code 등록 안내 + README 갱신**

`README.md`에 "AI 백엔드(MCP)" 섹션 추가(설치·serve·등록·도구 목록·번역 흐름 8~15줄):
- `python -m nvforum.cli serve` 로 서버 실행
- `claude mcp add --transport http nvforum http://127.0.0.1:8000/mcp` 로 등록
- 도구: list_topics / get_topic / search_posts / posts_since / list_untranslated / save_translation / collect
- 번역 흐름: list_untranslated → (Claude 번역) → save_translation

- [ ] **Step 4: 커밋**

```
git -c user.name="jylee" -c user.email="jylee@krafton.com" add README.md
git -c user.name="jylee" -c user.email="jylee@krafton.com" commit -m "docs: AI 백엔드(MCP) 사용법·등록 안내

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review 결과

- **스펙 커버리지:** store 변경(text_ko·FTS5·트리거·reindex·WAL·락) = Task 1·2 / query.py 전 함수 = Task 3·4 / MCP 7개 도구 = Task 5 / serve·reindex CLI = Task 6 / 등록·라이브 = Task 7. 누락 없음.
- **Placeholder:** 없음. 모든 코드 스텝에 실제 코드 포함.
- **타입 일관성:** `save_translation(post_id, text_ko, at)`(store) ↔ `query.save_translation(store, post_id, text_ko, now=...)`(at=now() 주입) 일치. `search(query, board, since, limit)`(store) ↔ `query.search_posts(store, query, ...)` 일치. MCP 도구 인자 `query_text`는 모듈 `query` 혼동 방지용 의도적 명명. query 함수 반환 dict 키가 테스트 단언과 일치.
- **알려진 주의점(구현 중 정리):** (a) Task 2 `test_reindex_...`는 직접 INSERT도 트리거가 잡으므로 첫 빈-결과 단언을 실제 동작에 맞춰 제거/조정. (b) Task 5 `call_tool` 반환 형태는 FastMCP 버전별로 달라 Step 4에서 실제 출력 확인 후 언래핑 1줄 조정(도구 코드는 불변). 두 가지 모두 해당 스텝에 처리 지침 명시.
