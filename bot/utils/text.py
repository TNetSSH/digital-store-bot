from __future__ import annotations

import re
from html import escape, unescape
from html.parser import HTMLParser

_LEGACY_EMOJI = re.compile(
    r"<emoji\s+id=(?P<quote>['\"])(?P<id>\d+)(?P=quote)>"
    r"(?P<fallback>.*?)</(?:emoji)?>",
    flags=re.DOTALL | re.IGNORECASE,
)
_KNOWN_HTML = re.compile(
    r"</?(?:b|strong|i|em|u|ins|s|strike|del|tg-spoiler|code|pre|blockquote|a|tg-emoji)\b",
    flags=re.IGNORECASE,
)


def render_user_template(template: str, *, name: str, username: str | None, user_id: int) -> str:
    values = {
        "{NAME}": escape(name or "cliente"),
        "{USERNAME}": escape(f"@{username}" if username else "sem username"),
        "{ID}": str(user_id),
    }
    rendered = template
    for token, value in values.items():
        rendered = rendered.replace(token, value)
    return rendered


def shorten(value: str, maximum: int) -> str:
    value = " ".join(value.split())
    if len(value) <= maximum:
        return value
    return value[: maximum - 1].rstrip() + "…"


def normalize_legacy_emoji(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        return f'<tg-emoji emoji-id="{match.group("id")}">{match.group("fallback")}</tg-emoji>'

    return _LEGACY_EMOJI.sub(replace, value)


def admin_html(raw_text: str, entity_html: str) -> str:
    """Preserve Telegram entities while also accepting the old raw HTML notation."""
    raw_text = raw_text.strip()
    if _LEGACY_EMOJI.search(raw_text) or _KNOWN_HTML.search(raw_text):
        return normalize_legacy_emoji(raw_text)
    return entity_html.strip()


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def strip_html(value: str) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(normalize_legacy_emoji(value))
    parser.close()
    return " ".join(unescape("".join(parser.parts)).split())
