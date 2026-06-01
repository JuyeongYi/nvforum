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
