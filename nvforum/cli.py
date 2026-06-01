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
