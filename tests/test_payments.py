from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.database import Database
from bot.services.payments import PaymentProcessor


class FakeBot:
    def __init__(self):
        self.messages = []
        self.deleted = []

    async def send_message(self, chat_id, text):
        self.messages.append((chat_id, text))

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))


class FakeMercadoPago:
    def __init__(self, payment):
        self.payment = payment

    async def get_payment(self, payment_id):
        assert payment_id == str(self.payment["id"])
        return self.payment


class FakeDelivery:
    def __init__(self):
        self.orders = []

    async def deliver_order(self, order):
        self.orders.append(order["id"])


async def make_order(db: Database, method: str):
    await db.upsert_user(42, "Cliente", "cliente")
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
        price_stars=50,
        price_brl_cents=1990,
    )
    product = await db.get_product(product_id)
    return await db.create_order(
        telegram_user_id=42,
        product=product,
        payment_method=method,
        amount=50 if method == "stars" else 1990,
        currency="XTR" if method == "stars" else "BRL",
    )


@pytest.mark.asyncio
async def test_pre_checkout_validates_user_currency_and_amount(tmp_path: Path):
    db = Database(tmp_path / "stars.db")
    await db.open(stars_enabled=True, pix_enabled=False)
    try:
        order = await make_order(db, "stars")
        processor = PaymentProcessor(
            bot=FakeBot(),
            db=db,
            config=SimpleNamespace(admin_ids=set()),
            mercado_pago=None,
            delivery=FakeDelivery(),
        )
        query = SimpleNamespace(
            invoice_payload=f"dsb:{order['public_id']}",
            from_user=SimpleNamespace(id=42),
            currency="XTR",
            total_amount=50,
        )
        assert await processor.validate_pre_checkout(query) == (True, "")
        query.total_amount = 51
        valid, error = await processor.validate_pre_checkout(query)
        assert not valid
        assert "valor" in error.lower()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_pix_approval_is_validated_and_confirmation_is_idempotent(tmp_path: Path):
    db = Database(tmp_path / "pix.db")
    await db.open(stars_enabled=True, pix_enabled=True)
    try:
        order = await make_order(db, "pix")
        await db.set_external_payment(order["id"], "9001")
        await db.set_payment_message(order["id"], 42, 77)
        payment = {
            "id": 9001,
            "external_reference": order["public_id"],
            "transaction_amount": "19.90",
            "currency_id": "BRL",
            "status": "approved",
            "status_detail": "accredited",
        }
        bot = FakeBot()
        delivery = FakeDelivery()
        processor = PaymentProcessor(
            bot=bot,
            db=db,
            config=SimpleNamespace(admin_ids=set()),
            mercado_pago=FakeMercadoPago(payment),
            delivery=delivery,
        )

        assert await processor.process_pix("9001") == "approved"
        assert await processor.process_pix("9001") == "approved"
        assert len(bot.messages) == 1
        assert bot.deleted == [(42, 77)]
        current = await db.get_order_by_id(order["id"])
        assert current["status"] == "approved"
    finally:
        await db.close()
