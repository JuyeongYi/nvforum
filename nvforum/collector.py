import logging

logger = logging.getLogger(__name__)


def _cutoff_for(store, board, since, full):
    if full:
        return None
    if since:
        return since
    return store.get_last_collected(board.alias)


def collect(client, store, boards: dict, since=None, full=False) -> dict:
    """대상 보드들을 수집. {alias: {topics, posts, skipped, partial, error}} 요약 반환."""
    summary = {}
    for alias, board in boards.items():
        result = {"topics": 0, "posts": 0, "skipped": 0, "partial": 0, "error": None}
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
                        if tm.pinned:
                            continue  # 고정 토픽은 정렬을 깨므로 중단 트리거 금지, 스킵
                        stop = True  # 비고정 + 오래됨 → 이후 비고정은 모두 더 오래됨
                        break
                    # 리스트에서 얻은 메타데이터는 포스트 조회 전에 먼저 저장
                    store.upsert_topic(alias, tm)
                    try:
                        posts = client.fetch_topic(tm.topic_id)
                    except Exception as exc:  # 토픽 단위 격리 (삭제/404 등)
                        logger.warning(
                            "토픽 %s 포스트 조회 실패, 건너뜀: %s", tm.topic_id, exc
                        )
                        result["skipped"] += 1
                        # 커서가 막히지 않도록 이 토픽 기준으로도 max_seen 전진
                        if max_seen is None or tm.last_posted_at > max_seen:
                            max_seen = tm.last_posted_at
                        continue
                    for post in posts:
                        store.upsert_post(post)
                        result["posts"] += 1
                    result["topics"] += 1
                    if len(posts) < tm.posts_count:
                        logger.warning(
                            "토픽 %s 부분 저장: 포스트 첫 페이지만 수집됨 (%s/%s)",
                            tm.topic_id, len(posts), tm.posts_count,
                        )
                        result["partial"] += 1
                    # 가장 최근 토픽은 다음 실행 때 재조회될 수 있으나 upsert라 무해
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
