import re
from html.parser import HTMLParser

_BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "blockquote", "pre"}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(html: str) -> str:
    """cooked HTML을 평문으로. 블록 태그는 줄바꿈, 줄 내부 공백은 단일 스페이스로."""
    parser = _TextExtractor()
    parser.feed(html)
    raw = "".join(parser.parts)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw.split("\n")]
    return "\n".join(line for line in lines if line)
