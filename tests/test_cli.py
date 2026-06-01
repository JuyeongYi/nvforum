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
