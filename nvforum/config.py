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
