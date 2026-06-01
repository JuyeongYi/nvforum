# NVForum 수집기 설계 (1단계 — 원문 구조적 저장)

작성일: 2026-06-01
작성자: jylee@krafton.com

## 목적

NVIDIA Developer Forums(Discourse 기반)의 지정된 게시판들에서 특정 시점 이후
글을 수집해 구조적으로 저장한다. 이후 단계에서 이 저장소 위에 요약/분류 리포트,
그리고 검색/질의 에이전트(RAG)를 얹는다.

**1단계 범위**: 수집 + 구조적 저장까지. LLM 요약/리포트는 이후 단계.

## 검증된 사실 (대상 API)

대상 포럼은 `forums.developer.nvidia.com` (Discourse). 공개 JSON API 사용, 로그인 불필요.

- **카테고리 목록**: `<category-url>/<category_id>.json?page=N`
  - 예: `https://forums.developer.nvidia.com/c/accelerated-computing/dgx-spark-gb10/dgx-spark-gb10/721.json?page=0`
  - 페이지당 30개 토픽, `topic_list.more_topics_url`로 다음 페이지 유무 판단
  - 기본 정렬: 활동순(`last_posted_at` 내림차순)
  - 토픽 필드: `id`, `title`, `slug`, `created_at`, `last_posted_at`, `posts_count`, `views`, `like_count`, `category_id`
- **개별 토픽(글 본문)**: `/t/<topic_id>.json`
  - `post_stream.posts[]`: `id`, `username`, `created_at`, `updated_at`, `cooked`(렌더된 HTML), `post_number`, `reply_count`
  - `post_stream.stream`: 전체 post id 목록 (글이 많으면 posts가 일부만 옴 → 추가 조회 필요)
  - 추가 글 조회: `/t/<topic_id>/posts.json?post_ids[]=<id>&post_ids[]=<id>...`
  - `raw`(원본 마크다운)은 기본 미포함. `cooked` HTML을 보존하고 태그 제거 텍스트를 별도 저장.

## 전체 구조

Python CLI 단일 프로젝트. 책임별 4개 모듈로 분리, 각 모듈 단독 테스트 가능.

```
nvforum/
  config.py     # boards.yaml 로드, URL에서 category id 추출
  client.py     # Discourse JSON API 호출 (User-Agent, rate-limit, 재시도)
  store.py      # SQLite 저장/조회 (boards, topics, posts)
  collector.py  # 수집 오케스트레이션 (증분/--since 판단, 페이지네이션)
  cli.py        # 명령 진입점 (collect / list-boards / stats)
boards.yaml     # 사용자가 관리하는 보드 목록
data/forum.db   # SQLite 저장소
```

의존 방향: `cli` → `collector` → (`client`, `store`, `config`). `client`는 HTTP만,
`store`는 DB만 알며 서로 모른다. `collector`가 둘을 조합한다.

## 저장소 — SQLite

구조적 질의가 핵심 요구. 파일 하나로 백업이 쉽고, 이후 임베딩 컬럼/FTS5 전문검색을
같은 DB에 얹어 검색/질의 단계로 확장 가능.

스키마(개념):

- `boards(alias PK, category_id, url, last_collected_at)`
- `topics(topic_id PK, board_alias FK, title, slug, created_at, last_posted_at,
   posts_count, views, like_count, url)`
- `posts(post_id PK, topic_id FK, post_number, username, created_at, updated_at,
   cooked_html, plain_text)`

- `cooked` HTML은 원본 보존용으로 저장.
- `plain_text`는 HTML 태그 제거 결과(이후 요약·검색용).
- 모든 쓰기는 upsert(멱등). 중복 실행해도 안전.

## 보드 목록 관리 (`boards.yaml`)

```yaml
boards:
  Spark: "https://forums.developer.nvidia.com/c/accelerated-computing/dgx-spark-gb10/dgx-spark-gb10/721"
  # 별칭: 카테고리 URL (끝 숫자가 category id). .json은 코드가 자동으로 붙인다.
```

URL 끝 경로 세그먼트의 숫자를 category id로 자동 파싱. 새 보드는 한 줄 추가.

## 시점 기준 (증분 + 수동 둘 다 지원)

- **증분(기본)**: 보드별 `last_collected_at` 저장. 다음 실행 시 그 이후 활동
  (`last_posted_at`)이 있는 토픽만 수집. 카테고리가 활동순 정렬이므로, 페이징 커서가
  cutoff보다 오래된 토픽에 닿으면 그 보드 페이징을 중단.
- **수동 `--since YYYY-MM-DD`**: 해당 날짜를 cutoff로 강제(증분 커서 무시, 재수집).
- **`--full`**: cutoff 없이 전체 재수집.
- 글(post) 단위로 `created_at`을 저장하므로, "특정 시점 이후 글"은 이후
  `WHERE created_at >= ?` 질의로 정확히 추출 가능.
- 증분 커서는 토픽의 `last_posted_at` 기준(활동 정렬과 일치). cutoff 비교는 토픽의
  `last_posted_at >= cutoff`로 토픽 선별 → 토픽 내 글은 전부 저장(upsert).

## 수집 흐름

`collect` 실행 → 대상 보드 각각에 대해:

1. 카테고리 페이지를 `page=0`부터 순회.
2. 각 토픽의 `last_posted_at`이 cutoff 이상이면 수집 대상. cutoff보다 오래된 토픽에
   닿으면(정렬상 이후는 모두 더 오래됨) 페이징 중단.
3. 대상 토픽마다 `/t/<topic_id>.json` 조회, 글 전부 저장(필요 시 `posts.json`으로
   누락 글 보충).
4. 보드 `last_collected_at`을 이번 실행에서 본 최대 `last_posted_at`으로 갱신.

예의: 요청 사이 짧은 sleep. 5xx/429 시 지수 백오프 재시도. User-Agent 명시.

## CLI 명령

- `nvforum collect [--board <spec>] [--since YYYY-MM-DD] [--full]`
  - `--board` 생략 → 모든 보드 수집(기본)
  - `--board *` 또는 `--board all` → 모든 보드 명시적 수집
  - `--board Spark` → 단일 보드
  - `--board Spark,Other` → 콤마 구분 다중 보드
- `nvforum list-boards` — 설정된 보드와 마지막 수집 시각
- `nvforum stats` — 보드별 토픽/글 수, 최신 글 날짜

## 에러 처리

- 네트워크 오류/타임아웃: 지수 백오프로 N회 재시도 후 해당 항목 스킵하고 로그 남김.
- 한 보드 실패가 다른 보드 수집을 막지 않음(보드 단위 격리).
- 잘못된 boards.yaml(파싱 불가/URL에서 id 추출 실패): 시작 시 명확한 에러로 중단.
- DB 쓰기는 토픽 단위 트랜잭션. 중단되어도 다음 실행에서 upsert로 이어서 진행.

## 테스트 (TDD)

- `config`: URL→category id 파싱, boards.yaml 로드/검증.
- `client`: 저장된 샘플 JSON fixture로 파싱 검증(네트워크 없이), 재시도/백오프 로직.
- `store`: 인메모리 SQLite로 upsert 멱등성, 증분 커서 갱신 로직.
- `collector`: 가짜 client(fixture) 주입으로 페이지네이션·cutoff 중단·보드 격리 검증.

## 범위 밖 (이후 단계)

- LLM 요약/분류 리포트 생성 (2~3단계)
- 검색/질의 에이전트, 임베딩/RAG, FTS5 (4단계)
- 스케줄링/자동 실행(필요 시 OS 스케줄러로 `collect` 호출)
