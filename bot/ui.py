from __future__ import annotations

from contextlib import suppress

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup


async def replace_menu(
    callback: CallbackQuery,
    bot: Bot,
    *,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    photo_file_id: str = "",
) -> None:
    await callback.answer()
    if callback.message:
        with suppress(TelegramBadRequest):
            await callback.message.delete()
    if photo_file_id:
        await bot.send_photo(
            callback.from_user.id,
            photo_file_id,
            caption=text,
            reply_markup=reply_markup,
        )
    else:
        await bot.send_message(callback.from_user.id, text, reply_markup=reply_markup)
