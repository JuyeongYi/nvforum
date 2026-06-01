# NVForum 수집기 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** NVIDIA Developer Forums(Discourse)의 지정 게시판에서 특정 시점 이후 글을 수집해 SQLite에 구조적으로 저장하는 Python CLI를 만든다.

**Architecture:** 책임별 4개 모듈(config/client/store/collector)과 CLI 진입점. `client`는 HTTP/파싱만, `store`는 SQLite만 담당하며 서로 모른다. `collector`가 둘을 조합해 증분/`--since` cutoff 판단과 페이지네이션을 수행한다. 의존성 주입(fake client, in-memory DB)으로 네트워크 없이 테스트한다.

**Tech Stack:** Python 3.12, `requests`, `PyYAML`, 표준 라이브러리 `sqlite3`/`argparse`/`html.parser`, 테스트는 `pytest`.

---

## 핵심 설계 결정 (전 태스크 공통)

- **타임스탬프 비교는 문자열 비교.** Discourse는 `2026-03-17T14:15:51.211Z` 고정 ISO 포맷을 준다. 같은 포맷이면 사전식 비교 = 시간순 비교. `--since 2026-05-01`도 prefix라 그대로 문자열 비교 가능. datetime 파싱 불필요.
- **cutoff 규칙:** `--full`이면 None(전체), `--since D`면 D, 아니면 보드의 `last_collected_at`(없으면 None).
- **토픽 선별:** 카테고리는 활동순(`last_posted_at` desc) 정렬. 토픽의 `last_posted_at >= cutoff`면 수집. cutoff보다 오래된 토픽에 닿으면 그 보드 페이징 중단. 선택된 토픽의 글은 전부 upsert.
- **모든 쓰기는 upsert(멱등).**

## 파일 구조

```
pyproject.toml              # 패키지/의존성/CLI 엔트리포인트(nvforum)
boards.yaml                 # 사용자 보드 목록 (별칭→URL)
nvforum/__init__.py
nvforum/models.py           # 데이터클래스: Board, TopicMeta, Post
nvforum/config.py           # boards.yaml 로드, URL→category_id 파싱
nvforum/htmltext.py         # cooked HTML → plain_text
nvforum/client.py           # DiscourseClient (HTTP + JSON 파싱, 재시도)
nvforum/store.py            # Store (SQLite 스키마/upsert/커서/통계)
nvforum/collector.py        # collect() 오케스트레이션
nvforum/cli.py              # argparse: collect / list-boards / stats
tests/fixtures/category_page.json
tests/fixtures/topic_detail.json
tests/test_config.py
tests/test_htmltext.py
tests/test_store.py
tests/test_client.py
tests/test_collector.py
tests/test_cli.py
```

---

### Task 1: 프로젝트 스캐폴드

**Files:**
- Create: `pyproject.toml`
- Create: `nvforum/__init__.py`
- Create: `boards.yaml`
- Create: `tests/__init__.py`
- Create: `tests/test_smoke.py`

- [ ] **Step 1: pyproject.toml 작성**

```toml
[project]
name = "nvforum"
version = "0.1.0"
description = "NVIDIA Developer Forums collector"
requires-python = ">=3.10"
dependencies = ["requests>=2.31", "PyYAML>=6.0"]

[project.scripts]
nvforum = "nvforum.cli:main"

[project.optional-dependencies]
dev = ["pytest>=8"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
packages = ["nvforum"]
```

- [ ] **Step 2: 패키지/설정 파일 생성**

`nvforum/__init__.py`:
```python
__version__ = "0.1.0"
```

`tests/__init__.py`: (빈 파일)

`boards.yaml`:
```yaml
boards:
  Spark: "https://forums.developer.nvidia.com/c/accelerated-computing/dgx-spark-gb10/dgx-spark-gb10/721"
```

- [ ] **Step 3: 스모크 테스트 작성**

`tests/test_smoke.py`:
```python
import nvforum


def test_package_imports():
    assert nvforum.__version__ == "0.1.0"
```

- [ ] **Step 4: 개발 설치 + 테스트 실행**

Run: `pip install -e ".[dev]" && pytest tests/test_smoke.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add pyproject.toml nvforum/__init__.py boards.yaml tests/__init__.py tests/test_smoke.py
git commit -m "chore: NVForum 프로젝트 스캐폴드"
```

---

### Task 2: 데이터 모델 (models.py)

**Files:**
- Create: `nvforum/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_models.py`:
```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_models.py -v`
Expected: FAIL with "No module named 'nvforum.models'"

- [ ] **Step 3: models.py 구현**

```python
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_models.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add nvforum/models.py tests/test_models.py
git commit -m "feat: 데이터 모델(Board/TopicMeta/Post) 추가"
```

---

### Task 3: 설정 로더 (config.py)

**Files:**
- Create: `nvforum/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_config.py`:
```python
import pytest
from nvforum.config import parse_category_id, load_boards


def test_parse_category_id_from_trailing_number():
    url = "https://forums.developer.nvidia.com/c/accelerated-computing/dgx-spark-gb10/dgx-spark-gb10/721"
    assert parse_category_id(url) == 721


def test_parse_category_id_ignores_trailing_slash():
    assert parse_category_id("https://x/c/foo/bar/42/") == 42


def test_parse_category_id_raises_without_number():
    with pytest.raises(ValueError):
        parse_category_id("https://x/c/foo/bar")


def test_load_boards(tmp_path):
    p = tmp_path / "boards.yaml"
    p.write_text(
        'boards:\n  Spark: "https://x/c/a/b/721"\n  Proj: "https://x/c/a/c/723"\n',
        encoding="utf-8",
    )
    boards = load_boards(str(p))
    assert set(boards) == {"Spark", "Proj"}
    assert boards["Spark"].category_id == 721
    assert boards["Proj"].url == "https://x/c/a/c/723"


def test_load_boards_empty_raises(tmp_path):
    p = tmp_path / "boards.yaml"
    p.write_text("boards: {}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_boards(str(p))
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with "No module named 'nvforum.config'"

- [ ] **Step 3: config.py 구현**

```python
import re

import yaml

from nvforum.models import Board


def parse_category_id(url: str) -> int:
    """카테고리 URL 끝의 숫자 세그먼트를 category id로 추출."""
    segments = [s for s in url.rstrip("/").split("/") if s]
    if not segments or not segments[-1].isdigit():
        raise ValueError(f"URL 끝에서 category id 숫자를 찾을 수 없습니다: {url}")
    return int(segments[-1])


def load_boards(path: str) -> dict[str, Board]:
    """boards.yaml 로드 → {별칭: Board}."""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    raw = data.get("boards") or {}
    if not raw:
        raise ValueError(f"{path} 에 boards 항목이 비어 있습니다.")
    boards: dict[str, Board] = {}
    for alias, url in raw.items():
        boards[alias] = Board(
            alias=alias, category_id=parse_category_id(url), url=url
        )
    return boards
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add nvforum/config.py tests/test_config.py
git commit -m "feat: boards.yaml 로더와 category id 파서"
```

---

### Task 4: HTML→텍스트 변환 (htmltext.py)

**Files:**
- Create: `nvforum/htmltext.py`
- Test: `tests/test_htmltext.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_htmltext.py`:
```python
from nvforum.htmltext import html_to_text


def test_strips_tags_and_unescapes():
    html = "<p>Hello <a href='x'>world</a> &amp; more</p>"
    assert html_to_text(html) == "Hello world & more"


def test_block_tags_become_newlines():
    html = "<p>line one</p><p>line two</p>"
    assert html_to_text(html) == "line one\nline two"


def test_collapses_whitespace_within_line():
    assert html_to_text("<p>a    b\tc</p>") == "a b c"


def test_empty():
    assert html_to_text("") == ""
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_htmltext.py -v`
Expected: FAIL with "No module named 'nvforum.htmltext'"

- [ ] **Step 3: htmltext.py 구현**

```python
import re
from html.parser import HTMLParser

_BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "blockquote", "pre"}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(html: str) -> str:
    """cooked HTML을 평문으로. 블록 태그는 줄바꿈, 줄 내부 공백은 단일 스페이스로."""
    parser = _TextExtractor()
    parser.feed(html)
    raw = "".join(parser.parts)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw.split("\n")]
    return "\n".join(line for line in lines if line)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_htmltext.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add nvforum/htmltext.py tests/test_htmltext.py
git commit -m "feat: cooked HTML → plain text 변환기"
```

---

### Task 5: 저장소 (store.py)

**Files:**
- Create: `nvforum/store.py`
- Test: `tests/test_store.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_store.py`:
```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_store.py -v`
Expected: FAIL with "No module named 'nvforum.store'"

- [ ] **Step 3: store.py 구현**

```python
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_store.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add nvforum/store.py tests/test_store.py
git commit -m "feat: SQLite 저장소(스키마/upsert/커서/통계)"
```

---

### Task 6: API 클라이언트 (client.py)

`client`는 HTTP 호출(`fetch_json`)과 JSON→모델 파싱을 분리한다. 테스트는 fixture JSON과 fake `fetch_json`을 주입해 네트워크 없이 검증한다. 먼저 fixture 파일을 만든다.

**Files:**
- Create: `tests/fixtures/category_page.json`
- Create: `tests/fixtures/topic_detail.json`
- Create: `nvforum/client.py`
- Test: `tests/test_client.py`

- [ ] **Step 1: fixture 파일 작성**

`tests/fixtures/category_page.json` (실제 응답을 축약한 형태):
```json
{
  "topic_list": {
    "more_topics_url": "/c/a/b/721?page=1",
    "per_page": 30,
    "topics": [
      {
        "id": 363819, "title": "Introducing NVIDIA NemoClaw",
        "slug": "introducing-nvidia-nemoclaw", "category_id": 721,
        "created_at": "2026-03-17T14:15:51.211Z",
        "last_posted_at": "2026-05-10T14:19:44.134Z",
        "posts_count": 3, "views": 2863, "like_count": 3
      },
      {
        "id": 100, "title": "Old topic", "slug": "old-topic",
        "category_id": 721,
        "created_at": "2026-01-01T00:00:00.000Z",
        "last_posted_at": "2026-01-02T00:00:00.000Z",
        "posts_count": 1, "views": 5, "like_count": 0
      }
    ]
  }
}
```

`tests/fixtures/topic_detail.json`:
```json
{
  "id": 363819, "title": "Introducing NVIDIA NemoClaw",
  "slug": "introducing-nvidia-nemoclaw",
  "post_stream": {
    "posts": [
      {
        "id": 1, "post_number": 1, "username": "alice",
        "created_at": "2026-03-17T14:15:51.211Z",
        "updated_at": "2026-03-17T14:15:51.211Z",
        "cooked": "<p>Hello <b>world</b></p>"
      },
      {
        "id": 2, "post_number": 2, "username": "bob",
        "created_at": "2026-05-10T14:19:44.134Z",
        "updated_at": "2026-05-10T14:19:44.134Z",
        "cooked": "<p>A reply</p>"
      }
    ],
    "stream": [1, 2]
  }
}
```

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_client.py`:
```python
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
```

- [ ] **Step 3: 실패 확인**

Run: `pytest tests/test_client.py -v`
Expected: FAIL with "No module named 'nvforum.client'"

- [ ] **Step 4: client.py 구현**

```python
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
        for attempt in range(max_retries):
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
        )
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `pytest tests/test_client.py -v`
Expected: PASS

- [ ] **Step 6: 재시도 로직 테스트 추가**

`tests/test_client.py`에 추가:
```python
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
```

Run: `pytest tests/test_client.py -v`
Expected: PASS

- [ ] **Step 7: 커밋**

```bash
git add nvforum/client.py tests/test_client.py tests/fixtures
git commit -m "feat: Discourse API 클라이언트(파싱+재시도)"
```

---

### Task 7: 수집 오케스트레이션 (collector.py)

**Files:**
- Create: `nvforum/collector.py`
- Test: `tests/test_collector.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_collector.py`:
```python
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
    # 페이지0: 토픽 newer(2026-05-10), older(2026-04-01). cutoff=2026-05-01
    pages = [([_tm(1, "2026-05-10T00:00:00Z"), _tm(2, "2026-04-01T00:00:00Z")], False)]
    client = FakeClient(pages, {1: [_p(10, 1)], 2: [_p(20, 2)]})
    store = _store()
    board = Board("Spark", 721, "u")
    store.upsert_board(board)
    summary = collect(client, store, {"Spark": board}, since="2026-05-01")
    assert client.fetched_topics == [1]  # 토픽2는 cutoff 이전이라 스킵
    assert summary["Spark"]["topics"] == 1
    assert store.get_last_collected("Spark") == "2026-05-10T00:00:00Z"


def test_collect_full_ignores_cutoff():
    pages = [([_tm(1, "2026-05-10T00:00:00Z"), _tm(2, "2026-04-01T00:00:00Z")], False)]
    client = FakeClient(pages, {1: [_p(10, 1)], 2: [_p(20, 2)]})
    store = _store()
    board = Board("Spark", 721, "u")
    store.upsert_board(board)
    store.set_last_collected("Spark", "2026-09-01T00:00:00Z")  # 미래 커서도 무시
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
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_collector.py -v`
Expected: FAIL with "No module named 'nvforum.collector'"

- [ ] **Step 3: collector.py 구현**

```python
def _cutoff_for(store, board, since, full):
    if full:
        return None
    if since:
        return since
    return store.get_last_collected(board.alias)


def collect(client, store, boards: dict, since=None, full=False) -> dict:
    """대상 보드들을 수집. {alias: {topics, posts, error}} 요약 반환."""
    summary = {}
    for alias, board in boards.items():
        result = {"topics": 0, "posts": 0, "error": None}
        try:
            cutoff = _cutoff_for(store, board, since, full)
            max_seen = None
            page = 0
            stop = False
            while not stop:
                topics, has_more = client.fetch_category_page(board, page)
                if not topics:
                    break
                for tm in topics:
                    if cutoff is not None and tm.last_posted_at < cutoff:
                        stop = True  # 활동순 정렬 → 이후는 모두 더 오래됨
                        break
                    store.upsert_topic(alias, tm)
                    for post in client.fetch_topic(tm.topic_id):
                        store.upsert_post(post)
                        result["posts"] += 1
                    result["topics"] += 1
                    if max_seen is None or tm.last_posted_at > max_seen:
                        max_seen = tm.last_posted_at
                if not has_more:
                    break
                page += 1
            if max_seen is not None:
                store.set_last_collected(alias, max_seen)
        except Exception as exc:  # 보드 단위 격리
            result["error"] = str(exc)
        summary[alias] = result
    return summary
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_collector.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: 커밋**

```bash
git add nvforum/collector.py tests/test_collector.py
git commit -m "feat: 수집 오케스트레이션(증분/cutoff/페이지네이션/격리)"
```

---

### Task 8: CLI (cli.py)

**Files:**
- Create: `nvforum/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: 실패하는 테스트 작성 (보드 셀렉터 파싱)**

`tests/test_cli.py`:
```python
import pytest
from nvforum.cli import resolve_boards
from nvforum.models import Board


def _boards():
    return {
        "Spark": Board("Spark", 721, "u1"),
        "Proj": Board("Proj", 723, "u2"),
    }


def test_resolve_none_returns_all():
    assert set(resolve_boards(None, _boards())) == {"Spark", "Proj"}


def test_resolve_star_returns_all():
    assert set(resolve_boards("*", _boards())) == {"Spark", "Proj"}


def test_resolve_all_keyword_returns_all():
    assert set(resolve_boards("all", _boards())) == {"Spark", "Proj"}


def test_resolve_single():
    assert set(resolve_boards("Spark", _boards())) == {"Spark"}


def test_resolve_comma_list():
    assert set(resolve_boards("Spark,Proj", _boards())) == {"Spark", "Proj"}


def test_resolve_unknown_raises():
    with pytest.raises(ValueError):
        resolve_boards("Nope", _boards())
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL with "No module named 'nvforum.cli'"

- [ ] **Step 3: cli.py 구현**

```python
import argparse
import sys

from nvforum.client import DiscourseClient
from nvforum.collector import collect
from nvforum.config import load_boards
from nvforum.store import Store

BASE_URL = "https://forums.developer.nvidia.com"
DEFAULT_DB = "data/forum.db"
DEFAULT_BOARDS = "boards.yaml"


def resolve_boards(spec, all_boards: dict) -> dict:
    """--board 셀렉터를 {alias: Board}로 해석."""
    if spec is None or spec in ("*", "all"):
        return dict(all_boards)
    selected = {}
    for alias in spec.split(","):
        alias = alias.strip()
        if alias not in all_boards:
            raise ValueError(f"알 수 없는 보드: {alias} (설정: {list(all_boards)})")
        selected[alias] = all_boards[alias]
    return selected


def _open_store(db_path: str) -> Store:
    import os
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    store = Store(db_path)
    store.init_schema()
    return store


def cmd_collect(args) -> int:
    all_boards = load_boards(args.boards)
    targets = resolve_boards(args.board, all_boards)
    store = _open_store(args.db)
    for b in targets.values():
        store.upsert_board(b)
    client = DiscourseClient(BASE_URL)
    summary = collect(client, store, targets, since=args.since, full=args.full)
    for alias, r in summary.items():
        if r["error"]:
            print(f"[{alias}] 실패: {r['error']}")
        else:
            print(f"[{alias}] 토픽 {r['topics']}개, 글 {r['posts']}개 수집")
    store.close()
    return 0


def cmd_list_boards(args) -> int:
    all_boards = load_boards(args.boards)
    store = _open_store(args.db)
    for alias, b in all_boards.items():
        last = store.get_last_collected(alias) or "(없음)"
        print(f"{alias}: category {b.category_id}, 마지막 수집 {last}")
    store.close()
    return 0


def cmd_stats(args) -> int:
    store = _open_store(args.db)
    for alias, s in store.stats().items():
        print(f"{alias}: 토픽 {s['topics']}, 글 {s['posts']}, "
              f"최신 글 {s['latest_post'] or '(없음)'}")
    store.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nvforum")
    p.add_argument("--boards", default=DEFAULT_BOARDS)
    p.add_argument("--db", default=DEFAULT_DB)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="글 수집")
    c.add_argument("--board", default=None, help="별칭 / * / all / 콤마목록")
    c.add_argument("--since", default=None, help="YYYY-MM-DD 이후만")
    c.add_argument("--full", action="store_true", help="cutoff 무시 전체 수집")
    c.set_defaults(func=cmd_collect)

    sub.add_parser("list-boards", help="보드 목록/마지막 수집 시각").set_defaults(
        func=cmd_list_boards)
    sub.add_parser("stats", help="저장 통계").set_defaults(func=cmd_stats)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: 전체 테스트 실행**

Run: `pytest -v`
Expected: 전 테스트 PASS

- [ ] **Step 6: 커밋**

```bash
git add nvforum/cli.py tests/test_cli.py
git commit -m "feat: CLI(collect/list-boards/stats)와 --board 셀렉터(* 포함)"
```

---

### Task 9: 라이브 스모크 점검 (수동, 1회)

**Files:**
- (코드 변경 없음 — 실제 네트워크 동작 확인)

- [ ] **Step 1: 실제 수집 1회 실행**

Run: `python -m nvforum.cli collect --board Spark --since 2026-05-01`
Expected: `[Spark] 토픽 N개, 글 M개 수집` 형태 출력(네트워크 필요).

- [ ] **Step 2: 통계 확인**

Run: `python -m nvforum.cli stats`
Expected: `Spark: 토픽 N, 글 M, 최신 글 2026-...` 출력.

- [ ] **Step 3: 증분 동작 확인 (재실행)**

Run: `python -m nvforum.cli collect --board Spark`
Expected: 두 번째 실행은 새 글이 없으면 0개 또는 소수만 수집(증분 커서 적용).

- [ ] **Step 4: README 간단 작성 후 커밋**

`README.md`에 설치(`pip install -e .`)와 위 3개 명령 사용법을 5~10줄로 기록.

```bash
git add README.md
git commit -m "docs: 사용법 README 추가"
```

---

## Self-Review 결과

- **스펙 커버리지:** 보드 목록 관리(Task 3), SQLite 저장(Task 5), HTML→텍스트(Task 4), API 클라이언트/페이지네이션(Task 6), 증분+`--since`+`--full` cutoff(Task 7), `--board *`/all/콤마/단일(Task 8), 통계(Task 5+8), 에러 격리(Task 7) — 모두 태스크로 매핑됨.
- **Placeholder:** 없음. 모든 코드 스텝에 실제 코드 포함.
- **타입 일관성:** `TopicMeta`/`Post`/`Board` 필드명이 models.py 정의와 store/client/collector 전반에서 일치. `collect()` 반환 요약 `{topics, posts, error}` 일관.
- **알려진 주의점:** 글이 매우 많은 토픽(글 약 20개 초과)은 `/t/{id}.json`이 `post_stream.posts`를 일부만 주고 나머지 id는 `post_stream.stream`에만 있다. 1단계는 첫 응답에 포함된 글만 저장(대부분 토픽은 충분). 누락 글 보충(`/t/{id}/posts.json?post_ids[]=...`)은 필요 시 후속 태스크로 분리.
