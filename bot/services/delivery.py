from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from html import escape
from typing import Any

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup

from bot.config import Config
from bot.database import Database, Record
from bot.utils.buttons import button

logger = logging.getLogger(__name__)


class DeliveryService:
    def __init__(self, bot: Bot, db: Database, config: Config) -> None:
        self.bot = bot
        self.db = db
        self.config = config
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def deliver_order(self, order: Record, *, force: bool = False) -> bool:
        order_id = int(order["id"])
        async with self._locks[order_id]:
            current = await self.db.get_order_by_id(order_id)
            if current is None or current["status"] != "approved":
                return False
            items = await self.db.list_delivery_items(int(current["product_id"]))
            stock_item = (
                await self.db.get_order_stock_item(order_id)
                if current["stock_mode"] == "unique"
                else None
            )
            if not items and stock_item is None:
                await self._notify_missing_content(current)
                return False

            sent_any = False
            if stock_item is not None and (force or not current["stock_item_delivered_at"]):
                message = await self.bot.send_message(
                    int(current["telegram_user_id"]),
                    "🔑 <b>Seu item exclusivo:</b>\n\n"
                    f"<pre>{escape(str(stock_item['value']))}</pre>",
                )
                sent_any = True
                if not force:
                    await self.db.mark_stock_item_delivered(order_id, message.message_id)
            for item in items:
                if not force and await self.db.delivery_was_sent(order_id, int(item["id"])):
                    continue
                message = await self._send_item(int(current["telegram_user_id"]), item)
                sent_any = True
                if not force:
                    await self.db.log_delivery(order_id, int(item["id"]), message.message_id)

            if force:
                if sent_any:
                    await self.bot.send_message(
                        int(current["telegram_user_id"]),
                        f"✅ Reenvio de <b>{escape(str(current['product_name']))}</b> concluído.",
                    )
                return sent_any

            complete = await self.db.mark_order_delivered_if_complete(order_id)
            if sent_any and complete:
                await self.bot.send_message(
                    int(current["telegram_user_id"]),
                    f"✅ Entrega de <b>{escape(str(current['product_name']))}</b> concluída.",
                )
            return complete

    async def _send_item(self, chat_id: int, item: Record) -> Any:
        kind = item["kind"]
        if kind == "text":
            return await self.bot.send_message(chat_id, item["payload"])
        if kind == "link":
            markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        button(
                            item["button_text"] or "Abrir link",
                            url=item["payload"],
                            style=item["button_style"],
                            emoji_id=item["button_emoji_id"],
                        )
                    ]
                ]
            )
            return await self.bot.send_message(
                chat_id,
                item["caption"] or "Acesse o conteúdo pelo botão abaixo.",
                reply_markup=markup,
            )
        if kind != "file":
            raise RuntimeError(f"tipo de entrega desconhecido: {kind}")

        data = json.loads(item["payload"])
        media_type = data["media_type"]
        file_id = data["file_id"]
        caption = item["caption"] or None
        senders = {
            "document": self.bot.send_document,
            "photo": self.bot.send_photo,
            "video": self.bot.send_video,
            "audio": self.bot.send_audio,
            "animation": self.bot.send_animation,
            "voice": self.bot.send_voice,
        }
        sender = senders.get(media_type)
        if sender is None:
            raise RuntimeError(f"tipo de arquivo desconhecido: {media_type}")
        return await sender(chat_id=chat_id, **{media_type: file_id}, caption=caption)

    async def _notify_missing_content(self, order: Record) -> None:
        await self.bot.send_message(
            int(order["telegram_user_id"]),
            "⚠️ O pagamento foi confirmado, mas a entrega precisa de revisão. "
            "O administrador já foi avisado.",
        )
        text = (
            "⚠️ <b>Pedido aprovado sem conteúdo</b>\n\n"
            f"Pedido: <code>{order['public_id']}</code>\n"
            f"Produto: {escape(str(order['product_name']))}"
        )
        for admin_id in self.config.admin_ids:
            try:
                await self.bot.send_message(admin_id, text)
            except Exception:  # pragma: no cover - depends on Telegram availability
                logger.exception("failed to notify admin %s", admin_id)
