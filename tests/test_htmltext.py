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
