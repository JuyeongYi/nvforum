# NVForum 수집기

NVIDIA Developer Forums(Discourse)의 지정 게시판에서 특정 시점 이후 글을 수집해
SQLite에 구조적으로 저장하는 Python CLI.

## 설치

```bash
pip install -e ".[dev]"
```

## 보드 설정 (`boards.yaml`)

별칭 → 카테고리 URL(끝 숫자가 category id). 새 보드는 한 줄 추가.

```yaml
boards:
  Spark: "https://forums.developer.nvidia.com/c/accelerated-computing/dgx-spark-gb10/dgx-spark-gb10/721"
```

## 사용법

```bash
# 모든 보드 증분 수집 (이전 수집 시점 이후 활동만)
python -m nvforum.cli collect

# 특정 보드 / 모든 보드 명시 / 다중 보드
python -m nvforum.cli collect --board Spark
python -m nvforum.cli collect --board "*"          # 또는 --board all
python -m nvforum.cli collect --board Spark,Other

# 시점 수동 지정 (재수집)
python -m nvforum.cli collect --board Spark --since 2026-05-01

# cutoff 무시 전체 수집
python -m nvforum.cli collect --full

# 보드 목록과 마지막 수집 시각
python -m nvforum.cli list-boards

# 저장 통계 (보드별 토픽/글 수, 최신 글 날짜)
python -m nvforum.cli stats
```

기본 DB 경로는 `data/forum.db`. `--db`, `--boards`로 변경 가능.

## 테스트

```bash
python -m pytest -v
```

## 동작 메모

- 시점 비교는 ISO 타임스탬프 문자열 비교(고정 포맷이라 사전식 = 시간순).
- 카테고리 목록의 고정(pinned) 토픽은 활동순 정렬을 깨므로, 오래된 고정 토픽에서는
  페이징을 중단하지 않고 건너뛴다.
- 모든 쓰기는 upsert(멱등) — 중복 실행해도 안전.

## 알려진 한계 (1단계)

- 글이 약 20개를 넘는 긴 토픽은 `/t/{id}.json` 첫 응답의 글(보통 첫 20개)만 저장한다.
  나머지는 `post_stream.stream`에만 있으며 추가 조회(`/t/{id}/posts.json`)는 미구현.
  수집 시 `토픽 N 부분 저장 (20/M)` 경고로 표시된다. Discourse는 글을 오래된 순으로
  반환하므로 활성 긴 스레드에서는 최근 글이 누락될 수 있다 — 후속 단계에서 보완 예정.
