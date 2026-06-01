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
