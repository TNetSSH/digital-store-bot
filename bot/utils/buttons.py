from __future__ import annotations

from typing import Any

from aiogram.types import CopyTextButton, InlineKeyboardButton

ALLOWED_STYLES = {"default", "primary", "success", "danger"}


def normalize_style(style: str | None) -> str | None:
    candidate = (style or "default").strip().lower()
    if candidate not in ALLOWED_STYLES:
        return None
    return None if candidate == "default" else candidate


def button(
    text: str,
    *,
    callback_data: str | None = None,
    url: str | None = None,
    copy_text: str | None = None,
    style: str | None = None,
    emoji_id: str | None = None,
    pay: bool | None = None,
) -> InlineKeyboardButton:
    actions = [callback_data is not None, url is not None, copy_text is not None, pay is True]
    if sum(actions) != 1:
        raise ValueError("o botao deve possuir exatamente uma acao")
    kwargs: dict[str, Any] = {"text": text}
    normalized = normalize_style(style)
    if normalized:
        kwargs["style"] = normalized
    if emoji_id:
        kwargs["icon_custom_emoji_id"] = emoji_id
    if callback_data is not None:
        kwargs["callback_data"] = callback_data
    elif url is not None:
        kwargs["url"] = url
    elif copy_text is not None:
        kwargs["copy_text"] = CopyTextButton(text=copy_text)
    else:
        kwargs["pay"] = True
    return InlineKeyboardButton(**kwargs)
