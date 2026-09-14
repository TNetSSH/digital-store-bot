from pathlib import Path

import pytest

from bot.database import Database


@pytest.mark.asyncio
async def test_catalog_order_and_delivery_idempotency(tmp_path: Path):
    db = Database(tmp_path / "store.db")
    await db.open(stars_enabled=True, pix_enabled=False)
    try:
        await db.upsert_user(42, "Cliente", "cliente")
        category_id = await db.create_category(
            name="Cursos",
            description="",
            button_text="Cursos",
            button_style="primary",
            button_emoji_id="",
        )
        product_id = await db.create_product(
            category_id=category_id,
            name="Curso A",
            description="Conteúdo",
            button_text="Curso A",
            button_style="success",
            button_emoji_id="",
            price_stars=50,
            price_brl_cents=1990,
        )
        delivery_id = await db.create_delivery_item(
            product_id=product_id,
            kind="text",
            payload="Material",
        )
        product = await db.get_product(product_id)
        order = await db.create_order(
            telegram_user_id=42,
            product=product,
            payment_method="stars",
            amount=50,
            currency="XTR",
        )
        await db.approve_stars_order(
            order["id"], telegram_charge_id="charge", provider_charge_id="provider"
        )
        await db.log_delivery(order["id"], delivery_id, 100)
        await db.log_delivery(order["id"], delivery_id, 101)

        assert await db.delivery_was_sent(order["id"], delivery_id)
        assert await db.mark_order_delivered_if_complete(order["id"])
        current = await db.get_order_by_id(order["id"])
        assert current["delivered_at"] is not None
    finally:
        await db.close()
