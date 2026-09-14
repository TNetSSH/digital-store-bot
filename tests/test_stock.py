import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.database import (
    Database,
    OutOfStockError,
    StockModeChangeError,
)
from bot.services.delivery import DeliveryService


async def make_product(db: Database, *, mode: str) -> int:
    category_id = await db.create_category(
        name="Categoria",
        description="",
        button_text="Categoria",
        button_style="default",
        button_emoji_id="",
    )
    product_id = await db.create_product(
        category_id=category_id,
        name="Produto limitado",
        description="",
        button_text="Comprar",
        button_style="primary",
        button_emoji_id="",
        price_stars=10,
        price_brl_cents=500,
    )
    await db.set_stock_mode(product_id, mode)
    return product_id


async def make_order(db: Database, product_id: int, user_id: int = 1):
    await db.upsert_user(user_id, f"Cliente {user_id}", None)
    product = await db.get_product(product_id)
    return await db.create_order(
        telegram_user_id=user_id,
        product=product,
        payment_method="stars",
        amount=10,
        currency="XTR",
        reservation_minutes=30,
    )


@pytest.mark.asyncio
async def test_quantity_stock_is_reserved_released_and_consumed(tmp_path: Path):
    db = Database(tmp_path / "quantity.db")
    await db.open(stars_enabled=True, pix_enabled=False)
    try:
        product_id = await make_product(db, mode="quantity")
        await db.set_quantity_stock(product_id, 1)

        first = await make_order(db, product_id)
        product = await db.get_product(product_id)
        assert product["stock_available"] == 0
        assert product["stock_reserved"] == 1

        with pytest.raises(OutOfStockError):
            await make_order(db, product_id, user_id=2)
        with pytest.raises(StockModeChangeError):
            await db.set_stock_mode(product_id, "unlimited")

        await db.update_order_status(first["id"], "cancelled", "customer_cancelled")
        product = await db.get_product(product_id)
        assert product["stock_available"] == 1
        assert product["stock_reserved"] == 0

        second = await make_order(db, product_id, user_id=2)
        await db.approve_stars_order(
            second["id"], telegram_charge_id="charge", provider_charge_id="provider"
        )
        current = await db.get_order_by_id(second["id"])
        product = await db.get_product(product_id)
        assert current["stock_state"] == "consumed"
        assert product["stock_available"] == 0
        assert product["stock_sold"] == 1
        assert await db.claim_low_stock_alert(product_id) == 0
        assert await db.claim_low_stock_alert(product_id) is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_unique_stock_assigns_one_item_and_releases_expired_reservation(tmp_path: Path):
    db = Database(tmp_path / "unique.db")
    await db.open(stars_enabled=True, pix_enabled=False)
    try:
        product_id = await make_product(db, mode="unique")
        assert await db.add_unique_stock_items(product_id, ["LOGIN-A", "LOGIN-B", "LOGIN-A"]) == (
            2,
            1,
        )

        first = await make_order(db, product_id)
        first_item = await db.get_order_stock_item(first["id"])
        assert first_item["value"] == "LOGIN-A"
        await db.approve_stars_order(
            first["id"], telegram_charge_id="charge-a", provider_charge_id="provider-a"
        )
        assert (await db.get_order_stock_item(first["id"]))["status"] == "sold"

        second = await make_order(db, product_id, user_id=2)
        await db._execute(
            """
            UPDATE orders SET reservation_expires_at = '2000-01-01T00:00:00.000Z'
            WHERE id = ?
            """,
            (second["id"],),
        )
        assert await db.release_expired_reservations(product_id=product_id) == 1
        assert (await db.get_order_by_id(second["id"]))["stock_state"] == "released"
        product = await db.get_product(product_id)
        assert product["stock_available"] == 1
        assert product["stock_sold"] == 1
    finally:
        await db.close()


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        message = SimpleNamespace(message_id=len(self.sent) + 1)
        self.sent.append((chat_id, text, kwargs))
        return message


@pytest.mark.asyncio
async def test_unique_item_is_idempotent_and_can_be_redelivered(tmp_path: Path):
    db = Database(tmp_path / "unique-delivery.db")
    await db.open(stars_enabled=True, pix_enabled=False)
    try:
        product_id = await make_product(db, mode="unique")
        await db.add_unique_stock_items(product_id, ["usuario:senha-secreta"])
        order = await make_order(db, product_id, user_id=7)
        await db.approve_stars_order(
            order["id"], telegram_charge_id="charge", provider_charge_id="provider"
        )
        current = await db.get_order_by_id(order["id"])
        bot = FakeBot()
        service = DeliveryService(bot, db, SimpleNamespace(admin_ids=set()))

        assert await service.deliver_order(current)
        assert await service.deliver_order(current)
        assert len(bot.sent) == 2
        assert "usuario:senha-secreta" in bot.sent[0][1]

        assert await service.deliver_order(current, force=True)
        assert len(bot.sent) == 4
        assert "usuario:senha-secreta" in bot.sent[2][1]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_existing_database_is_migrated_without_losing_rows(tmp_path: Path):
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE products (
            id INTEGER PRIMARY KEY,
            category_id INTEGER NOT NULL,
            is_archived INTEGER NOT NULL DEFAULT 0,
            is_visible INTEGER NOT NULL DEFAULT 0,
            position INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            telegram_user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00.000Z'
        );
        INSERT INTO products(id, category_id) VALUES (99, 1);
        """
    )
    connection.commit()
    connection.close()

    db = Database(path)
    await db.open(stars_enabled=True, pix_enabled=False)
    try:
        columns = await db._fetchall("PRAGMA table_info(products)")
        names = {row["name"] for row in columns}
        assert {"stock_mode", "stock_quantity", "low_stock_threshold", "stock_alerted"} <= names
        row = await db._fetchone("SELECT * FROM products WHERE id = 99")
        assert row["stock_mode"] == "unlimited"
        assert row["stock_quantity"] == 0
    finally:
        await db.close()
