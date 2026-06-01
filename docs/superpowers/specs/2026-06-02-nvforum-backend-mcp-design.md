# NVForum 백엔드 서버 (MCP) 설계 — 하위 시스템 ①

작성일: 2026-06-02
작성자: jylee@krafton.com
선행: `2026-06-01-nvforum-collector-design.md` (수집기/저장소). 본 설계는 그 위에 얹힌다.

## 목적

수집된 `data/forum.db`를 읽고 검색·번역·수집 트리거하는 **단일 로컬 백엔드 서버**를
만든다. AI(Claude)와 PySide6 GUI(하위 시스템 ②)가 **동시에** 같은 서버에 접속해
사용한다. 인터페이스는 **MCP over streamable HTTP** 하나로 통일한다.

## 설계 결정 (확정)

- **단일 백엔드 서버**가 DB를 소유한다. 두 프로세스가 SQLite 파일을 직접 여는 방식은
  동시 쓰기(번역 저장·수집) 충돌·잠금 위험이 있어 배제.
- **전송 계층은 MCP 하나**. REST를 따로 두지 않는다. AI도 GUI도 같은 `/mcp`의
  MCP 클라이언트가 된다. 관리 표면: 서버 1, 인터페이스 1, 로직 1곳(`query.py`).
- **번역은 AI가 툴로 수행·저장**한다(별도 API 키·비용 없음). 번역문은 DB에 저장.
- 번역 저장은 **별도 테이블이 아니라 `posts` 컬럼**(`text_ko`). 한국어 단일 언어면
  충분하므로 YAGNI. 다국어가 필요해지면 그때 분리.
- 루프백 평문 HTTP(`127.0.0.1`). HTTPS는 로컬 자체서명 인증서 부담 대비 실익 없음.

## 검증된 전제

- SQLite **FTS5** 사용 가능(현 Python 3.12 환경에서 확인).
- `mcp` 파이썬 SDK 설치됨.
- PySide6는 미설치(하위 시스템 ②에서 설치).

## 전체 구조

```
nvforum-server (127.0.0.1:<port>, 장기 실행)   ← DB 소유 (SQLite WAL, 쓰기 직렬화)
  └─ /mcp : MCP over streamable HTTP            ← 유일한 인터페이스
        tools → query.py (단일 로직) → Store
AI(Claude)  ─┐
PySide6 GUI ─┴─ 둘 다 같은 /mcp 의 MCP 클라이언트
```

새 파일:
```
nvforum/query.py        # 순수 읽기/쓰기 쿼리 함수 (Store만 의존)
nvforum/mcp_server.py   # MCP 도구 정의 = query.py 얇은 래퍼 + streamable HTTP 서버
```
변경:
```
nvforum/store.py        # text_ko 컬럼, FTS5 가상테이블+트리거, reindex(), 검색/번역 메서드, WAL
nvforum/cli.py          # serve / reindex 명령 추가
pyproject.toml          # mcp, uvicorn 의존성 + 콘솔 스크립트
```

## 저장소 변경 (`store.py`)

### 스키마 추가
- `posts`에 컬럼: `text_ko TEXT`, `text_ko_at TEXT`(번역 시각 ISO). 기존 행 NULL.
- FTS5 가상 테이블(외부 콘텐츠):
  ```sql
  CREATE VIRTUAL TABLE IF NOT EXISTS posts_fts
    USING fts5(plain_text, content='posts', content_rowid='post_id');
  ```
- posts ↔ posts_fts 동기화 트리거(INSERT/UPDATE/DELETE):
  ```sql
  CREATE TRIGGER IF NOT EXISTS posts_ai AFTER INSERT ON posts BEGIN
    INSERT INTO posts_fts(rowid, plain_text) VALUES (new.post_id, new.plain_text);
  END;
  CREATE TRIGGER IF NOT EXISTS posts_ad AFTER DELETE ON posts BEGIN
    INSERT INTO posts_fts(posts_fts, rowid, plain_text) VALUES('delete', old.post_id, old.plain_text);
  END;
  CREATE TRIGGER IF NOT EXISTS posts_au AFTER UPDATE ON posts BEGIN
    INSERT INTO posts_fts(posts_fts, rowid, plain_text) VALUES('delete', old.post_id, old.plain_text);
    INSERT INTO posts_fts(rowid, plain_text) VALUES (new.post_id, new.plain_text);
  END;
  ```
- 마이그레이션: `init_schema()`는 IF NOT EXISTS로 신규 객체를 추가(기존 DB 호환).
  컬럼 추가는 `PRAGMA table_info(posts)`로 존재 확인 후 `ALTER TABLE ... ADD COLUMN`.
- `reindex()`: 기존 posts 전체를 posts_fts에 1회 채움(트리거 도입 전 데이터용).
  `INSERT INTO posts_fts(posts_fts) VALUES('rebuild')` 사용.

### 동시성
- 연결 시 `PRAGMA journal_mode=WAL`(동시 읽기 향상), `PRAGMA busy_timeout=5000`.
- 서버는 비동기 다중 요청을 받으므로 **쓰기는 단일 `threading.Lock`으로 직렬화**.
  읽기는 락 없이 WAL로 동시 처리. `Store`는 `check_same_thread=False`로 열고,
  쓰기 메서드(`upsert_*`, `save_translation`, `set_last_collected`)를 락으로 감싼다.

### 신규 메서드
- `save_translation(post_id, text_ko, at)` — `text_ko`/`text_ko_at` 갱신.
- `search(query, board=None, since=None, limit=50)` — FTS5 MATCH 조인, 토픽/스니펫 포함.
- 읽기 헬퍼: `list_topics_rows`, `get_topic_row`, `get_posts_rows(topic_id)`,
  `posts_since_rows`, `untranslated_rows`. (query.py가 dict로 정형화)

## 공통 쿼리 계층 (`query.py`)

Store만 의존하는 순수 함수. 모든 함수는 JSON 직렬화 가능한 dict/list 반환(MCP·GUI 공용).

- `list_topics(store, board=None, since=None, order="recent", limit=50, offset=0)`
  → `[{topic_id, board, title, url, created_at, last_posted_at, posts_count, views, like_count}]`
- `get_topic(store, topic_id)`
  → `{topic: {...}, posts: [{post_id, post_number, username, created_at, cooked_html, plain_text, text_ko}]}`
- `search_posts(store, query, board=None, since=None, limit=50)`
  → `[{post_id, topic_id, topic_title, username, created_at, snippet, text_ko}]`
- `posts_since(store, since, board=None, limit=200)`
  → 시간순 `[{post_id, topic_id, topic_title, username, created_at, plain_text, text_ko}]`
- `untranslated_posts(store, board=None, topic_id=None, limit=50)`
  → `text_ko IS NULL`인 `[{post_id, topic_id, plain_text}]`
- `save_translation(store, post_id, text_ko)` → `{post_id, saved: true}` (시각 자동 기록)
- `collect(store, boards_path, board=None, since=None, full=False)`
  → collector.collect 호출, `{alias: {topics, posts, skipped, partial, error}}`

## MCP 서버 (`mcp_server.py`)

`mcp` SDK로 Server 생성, 도구 = query 래퍼. streamable HTTP ASGI 앱을 uvicorn으로 서빙.

도구(이름 / 입력 / 반환은 위 query 결과를 structured content로):
- `list_topics(board?, since?, order?, limit?, offset?)`
- `get_topic(topic_id)`
- `search_posts(query, board?, since?, limit?)`
- `posts_since(since, board?, limit?)`
- `list_untranslated(board?, topic_id?, limit?)`
- `save_translation(post_id, text_ko)`
- `collect(board?, since?, full?)`

- 서버 기동 시 단일 `Store`(WAL, 쓰기 락) 인스턴스를 만들어 모든 도구가 공유.
- 도구 핸들러는 동기 query 함수를 `anyio.to_thread.run_sync`로 호출(이벤트 루프 비차단).
- 바인딩: `127.0.0.1`, 포트 기본 8000(설정 가능).

## CLI 변경 (`cli.py`)

- `nvforum serve [--host 127.0.0.1] [--port 8000] [--db ...] [--boards ...]`
  — 백엔드 서버 실행(장기 실행).
- `nvforum reindex [--db ...]` — 기존 DB의 FTS 인덱스 재구축.

## 등록 (사용자 1회)

```
claude mcp add --transport http nvforum http://127.0.0.1:8000/mcp
```
GUI(하위 시스템 ②)는 같은 URL에 MCP 파이썬 클라이언트로 접속.

## 에러 처리

- 도구 입력 검증 실패 → 명확한 에러 메시지(구조화 에러).
- `collect`는 보드 단위 격리(기존 collector 동작) — 한 보드 실패가 전체를 막지 않음.
- DB 잠금 → `busy_timeout`으로 대기, 쓰기 락으로 충돌 방지.
- 미존재 topic_id/post_id → 빈 결과 또는 `{error: "..."}`.

## 테스트

- `store`: 인메모리 DB로 (a) 컬럼 마이그레이션, (b) FTS 트리거 동기화(insert/update/delete 후 검색),
  (c) reindex, (d) save_translation, (e) search 결과/스니펫.
- `query`: 픽스처 데이터로 각 함수의 반환 형태·필터(board/since/limit)·번역 포함 검증.
- `mcp_server`: 도구 핸들러를 query 목/실DB로 직접 호출해 입출력 검증(서버 기동 없이).
  선택: `mcp` SDK의 인메모리 클라이언트로 1개 통합 테스트(list_topics 왕복).
- 동시성: 읽기 중 쓰기 락 직렬화 단위 테스트(스레드 2개로 save_translation 경쟁).

## 범위 밖 (이후)

- PySide6 GUI (하위 시스템 ② — 별도 spec).
- LLM 요약/분류 리포트.
- 임베딩/시맨틱 RAG(현 단계는 FTS5 키워드 검색).
- 다국어 번역(현재 한국어 단일).
