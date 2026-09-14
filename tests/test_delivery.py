from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.database import Database
from bot.services.delivery import DeliveryService


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        message = SimpleNamespace(message_id=len(self.sent) + 1)
        self.sent.append((chat_id, text, kwargs))
        return message


@pytest.mark.asyncio
async def test_delivery_items_are_sent_once_and_can_be_manually_resent(tmp_path: Path):
    db = Database(tmp_path / "delivery.db")
    await db.open(stars_enabled=True, pix_enabled=False)
    try:
        await db.upsert_user(7, "Cliente", None)
        category_id = await db.create_category(
            name="Categoria",
            description="",
            button_text="Categoria",
            button_style="default",
            button_emoji_id="",
        )
        product_id = await db.create_product(
            category_id=category_id,
            name="Produto",
            description="",
            button_text="Produto",
            button_style="default",
            button_emoji_id="",
            price_stars=10,
            price_brl_cents=0,
        )
        await db.create_delivery_item(product_id=product_id, kind="text", payload="Texto")
        await db.create_delivery_item(
            product_id=product_id,
            kind="link",
            payload="https://example.com/material",
            button_text="Abrir",
        )
        product = await db.get_product(product_id)
        order = await db.create_order(
            telegram_user_id=7,
            product=product,
            payment_method="stars",
            amount=10,
            currency="XTR",
        )
        await db.approve_stars_order(
            order["id"], telegram_charge_id="charge", provider_charge_id="provider"
        )
        current = await db.get_order_by_id(order["id"])
        bot = FakeBot()
        service = DeliveryService(bot, db, SimpleNamespace(admin_ids=set()))

        assert await service.deliver_order(current)
        assert await service.deliver_order(current)
        assert len(bot.sent) == 3  # dois conteúdos e uma confirmação

        assert await service.deliver_order(current, force=True)
        assert len(bot.sent) == 6  # reenvio dos dois itens e confirmação
    finally:
        await db.close()
