from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import aiosqlite

Record = dict[str, Any]

DEFAULT_SETTINGS: dict[str, str] = {
    "welcome_text": (
        "<b>Digital Store</b>\n\n"
        "Olá, {NAME}! Escolha uma opção abaixo para acessar nossos produtos."
    ),
    "products_text": "🛍️ Produtos",
    "products_style": "primary",
    "products_emoji_id": "",
    "purchases_text": "📦 Minhas compras",
    "purchases_style": "default",
    "purchases_emoji_id": "",
    "support_text": "💬 Suporte",
    "support_style": "default",
    "support_emoji_id": "",
    "support_url": "",
    "stars_text": "Pagar com Stars",
    "stars_style": "primary",
    "stars_emoji_id": "",
    "pix_text": "Pagar com PIX",
    "pix_style": "success",
    "pix_emoji_id": "",
    "back_text": "Voltar",
    "back_style": "default",
    "back_emoji_id": "",
}


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    first_name TEXT NOT NULL,
    username TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    button_text TEXT NOT NULL,
    button_style TEXT NOT NULL DEFAULT 'default',
    button_emoji_id TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL DEFAULT 0,
    is_visible INTEGER NOT NULL DEFAULT 0 CHECK (is_visible IN (0, 1)),
    is_archived INTEGER NOT NULL DEFAULT 0 CHECK (is_archived IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    button_text TEXT NOT NULL,
    button_style TEXT NOT NULL DEFAULT 'default',
    button_emoji_id TEXT NOT NULL DEFAULT '',
    photo_file_id TEXT NOT NULL DEFAULT '',
    price_stars INTEGER NOT NULL DEFAULT 0 CHECK (price_stars >= 0),
    price_brl_cents INTEGER NOT NULL DEFAULT 0 CHECK (price_brl_cents >= 0),
    allow_stars INTEGER NOT NULL DEFAULT 1 CHECK (allow_stars IN (0, 1)),
    allow_pix INTEGER NOT NULL DEFAULT 0 CHECK (allow_pix IN (0, 1)),
    position INTEGER NOT NULL DEFAULT 0,
    is_visible INTEGER NOT NULL DEFAULT 0 CHECK (is_visible IN (0, 1)),
    is_archived INTEGER NOT NULL DEFAULT 0 CHECK (is_archived IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS delivery_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id),
    kind TEXT NOT NULL CHECK (kind IN ('text', 'file', 'link')),
    payload TEXT NOT NULL,
    caption TEXT NOT NULL DEFAULT '',
    button_text TEXT NOT NULL DEFAULT '',
    button_style TEXT NOT NULL DEFAULT 'primary',
    button_emoji_id TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    telegram_user_id INTEGER NOT NULL REFERENCES users(telegram_id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    product_name TEXT NOT NULL,
    payment_method TEXT NOT NULL CHECK (payment_method IN ('stars', 'pix')),
    amount INTEGER NOT NULL CHECK (amount >= 0),
    currency TEXT NOT NULL CHECK (currency IN ('XTR', 'BRL')),
    status TEXT NOT NULL DEFAULT 'pending',
    status_detail TEXT NOT NULL DEFAULT '',
    external_payment_id TEXT UNIQUE,
    telegram_charge_id TEXT UNIQUE,
    provider_charge_id TEXT,
    payment_message_chat_id INTEGER,
    payment_message_id INTEGER,
    delivered_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS delivery_log (
    order_id INTEGER NOT NULL REFERENCES orders(id),
    delivery_item_id INTEGER NOT NULL REFERENCES delivery_items(id) ON DELETE CASCADE,
    telegram_message_id INTEGER,
    delivered_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (order_id, delivery_item_id)
);

CREATE INDEX IF NOT EXISTS idx_categories_order
    ON categories(is_archived, is_visible, position, id);
CREATE INDEX IF NOT EXISTS idx_products_category
    ON products(category_id, is_archived, is_visible, position, id);
CREATE INDEX IF NOT EXISTS idx_delivery_product
    ON delivery_items(product_id, position, id);
CREATE INDEX IF NOT EXISTS idx_orders_user
    ON orders(telegram_user_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_status
    ON orders(status, created_at DESC);
"""


def _record(row: aiosqlite.Row | None) -> Record | None:
    return dict(row) if row is not None else None


class Database:
    CATEGORY_FIELDS = {
        "name",
        "description",
        "button_text",
        "button_style",
        "button_emoji_id",
        "is_visible",
    }
    PRODUCT_FIELDS = {
        "category_id",
        "name",
        "description",
        "button_text",
        "button_style",
        "button_emoji_id",
        "photo_file_id",
        "price_stars",
        "price_brl_cents",
        "allow_stars",
        "allow_pix",
        "is_visible",
    }

    def __init__(self, path: Path) -> None:
        self.path = path
        self.connection: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    async def open(self, *, stars_enabled: bool, pix_enabled: bool) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = await aiosqlite.connect(self.path)
        self.connection.row_factory = aiosqlite.Row
        await self.connection.execute("PRAGMA journal_mode = WAL")
        await self.connection.execute("PRAGMA busy_timeout = 5000")
        await self.connection.executescript(SCHEMA)
        settings = dict(DEFAULT_SETTINGS)
        settings["stars_enabled"] = "1" if stars_enabled else "0"
        settings["pix_enabled"] = "1" if pix_enabled else "0"
        await self.connection.executemany(
            "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", settings.items()
        )
        await self.connection.commit()

    async def close(self) -> None:
        if self.connection is not None:
            await self.connection.close()
            self.connection = None

    def _db(self) -> aiosqlite.Connection:
        if self.connection is None:
            raise RuntimeError("database is not open")
        return self.connection

    async def _fetchone(self, sql: str, params: Iterable[Any] = ()) -> Record | None:
        async with self._db().execute(sql, tuple(params)) as cursor:
            return _record(await cursor.fetchone())

    async def _fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[Record]:
        async with self._db().execute(sql, tuple(params)) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    async def _execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        async with self._write_lock:
            cursor = await self._db().execute(sql, tuple(params))
            await self._db().commit()
            return int(cursor.lastrowid or 0)

    async def get_setting(self, key: str, default: str = "") -> str:
        row = await self._fetchone("SELECT value FROM settings WHERE key = ?", (key,))
        return str(row["value"]) if row else default

    async def get_settings(self) -> dict[str, str]:
        rows = await self._fetchall("SELECT key, value FROM settings")
        return {str(row["key"]): str(row["value"]) for row in rows}

    async def set_setting(self, key: str, value: str) -> None:
        await self._execute(
            """
            INSERT INTO settings(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    async def toggle_setting(self, key: str) -> bool:
        current = await self.get_setting(key, "0") == "1"
        await self.set_setting(key, "0" if current else "1")
        return not current

    async def upsert_user(self, telegram_id: int, first_name: str, username: str | None) -> None:
        await self._execute(
            """
            INSERT INTO users(telegram_id, first_name, username) VALUES (?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                first_name = excluded.first_name,
                username = excluded.username,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (telegram_id, first_name, username),
        )

    async def list_categories(self, *, visible_only: bool = False) -> list[Record]:
        where = "is_archived = 0"
        if visible_only:
            where += " AND is_visible = 1"
        return await self._fetchall(
            f"SELECT * FROM categories WHERE {where} ORDER BY position, id"  # noqa: S608
        )

    async def get_category(self, category_id: int) -> Record | None:
        return await self._fetchone(
            "SELECT * FROM categories WHERE id = ? AND is_archived = 0", (category_id,)
        )

    async def create_category(
        self,
        *,
        name: str,
        description: str,
        button_text: str,
        button_style: str,
        button_emoji_id: str,
    ) -> int:
        position_row = await self._fetchone(
            "SELECT COALESCE(MAX(position), 0) + 10 AS position FROM categories"
        )
        return await self._execute(
            """
            INSERT INTO categories(
                name, description, button_text, button_style, button_emoji_id, position
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                description,
                button_text,
                button_style,
                button_emoji_id,
                int(position_row["position"] if position_row else 10),
            ),
        )

    async def update_category(self, category_id: int, field: str, value: Any) -> None:
        if field not in self.CATEGORY_FIELDS:
            raise ValueError("campo de categoria nao permitido")
        await self._execute(
            f"""UPDATE categories SET {field} = ?,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?""",  # noqa: S608
            (value, category_id),
        )

    async def archive_category(self, category_id: int) -> None:
        await self._execute(
            """
            UPDATE categories SET is_archived = 1, is_visible = 0,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (category_id,),
        )
        await self._execute(
            """
            UPDATE products SET is_archived = 1, is_visible = 0,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE category_id = ?
            """,
            (category_id,),
        )

    async def move_category(self, category_id: int, direction: int) -> None:
        await self._move("categories", category_id, direction, "is_archived = 0")

    async def list_products(
        self, *, category_id: int | None = None, visible_only: bool = False
    ) -> list[Record]:
        clauses = ["p.is_archived = 0"]
        params: list[Any] = []
        if category_id is not None:
            clauses.append("p.category_id = ?")
            params.append(category_id)
        if visible_only:
            clauses.extend(("p.is_visible = 1", "c.is_visible = 1", "c.is_archived = 0"))
        return await self._fetchall(
            f"""
            SELECT p.*, c.name AS category_name,
                (SELECT COUNT(*) FROM delivery_items d WHERE d.product_id = p.id)
                AS delivery_count
            FROM products p
            JOIN categories c ON c.id = p.category_id
            WHERE {" AND ".join(clauses)}
            ORDER BY c.position, c.id, p.position, p.id
            """,  # noqa: S608
            params,
        )

    async def get_product(self, product_id: int) -> Record | None:
        return await self._fetchone(
            """
            SELECT p.*, c.name AS category_name,
                (SELECT COUNT(*) FROM delivery_items d WHERE d.product_id = p.id)
                AS delivery_count
            FROM products p JOIN categories c ON c.id = p.category_id
            WHERE p.id = ? AND p.is_archived = 0
            """,
            (product_id,),
        )

    async def create_product(
        self,
        *,
        category_id: int,
        name: str,
        description: str,
        button_text: str,
        button_style: str,
        button_emoji_id: str,
        price_stars: int,
        price_brl_cents: int,
    ) -> int:
        position_row = await self._fetchone(
            """
            SELECT COALESCE(MAX(position), 0) + 10 AS position
            FROM products WHERE category_id = ?
            """,
            (category_id,),
        )
        return await self._execute(
            """
            INSERT INTO products(
                category_id, name, description, button_text, button_style,
                button_emoji_id, price_stars, price_brl_cents, allow_stars,
                allow_pix, position
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                category_id,
                name,
                description,
                button_text,
                button_style,
                button_emoji_id,
                price_stars,
                price_brl_cents,
                int(price_stars > 0),
                int(price_brl_cents > 0),
                int(position_row["position"] if position_row else 10),
            ),
        )

    async def update_product(self, product_id: int, field: str, value: Any) -> None:
        if field not in self.PRODUCT_FIELDS:
            raise ValueError("campo de produto nao permitido")
        await self._execute(
            f"""UPDATE products SET {field} = ?,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?""",  # noqa: S608
            (value, product_id),
        )

    async def archive_product(self, product_id: int) -> None:
        await self._execute(
            """
            UPDATE products SET is_archived = 1, is_visible = 0,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (product_id,),
        )

    async def move_product(self, product_id: int, direction: int) -> None:
        product = await self.get_product(product_id)
        if product:
            await self._move(
                "products",
                product_id,
                direction,
                "is_archived = 0 AND category_id = ?",
                (product["category_id"],),
            )

    async def _move(
        self,
        table: str,
        entity_id: int,
        direction: int,
        where: str,
        params: Iterable[Any] = (),
    ) -> None:
        if table not in {"categories", "products", "delivery_items"}:
            raise ValueError("tabela nao permitida")
        if direction not in {-1, 1}:
            raise ValueError("direcao invalida")
        rows = await self._fetchall(
            f"SELECT id, position FROM {table} WHERE {where} ORDER BY position, id",  # noqa: S608
            params,
        )
        index = next((i for i, row in enumerate(rows) if row["id"] == entity_id), None)
        if index is None:
            return
        target_index = index + direction
        if not 0 <= target_index < len(rows):
            return
        current, target = rows[index], rows[target_index]
        async with self._write_lock:
            db = self._db()
            await db.execute("BEGIN IMMEDIATE")
            try:
                await db.execute(
                    f"UPDATE {table} SET position = ? WHERE id = ?",  # noqa: S608
                    (target["position"], current["id"]),
                )
                await db.execute(
                    f"UPDATE {table} SET position = ? WHERE id = ?",  # noqa: S608
                    (current["position"], target["id"]),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def list_delivery_items(self, product_id: int) -> list[Record]:
        return await self._fetchall(
            "SELECT * FROM delivery_items WHERE product_id = ? ORDER BY position, id",
            (product_id,),
        )

    async def get_delivery_item(self, item_id: int) -> Record | None:
        return await self._fetchone("SELECT * FROM delivery_items WHERE id = ?", (item_id,))

    async def create_delivery_item(
        self,
        *,
        product_id: int,
        kind: str,
        payload: str | dict[str, Any],
        caption: str = "",
        button_text: str = "",
        button_style: str = "primary",
        button_emoji_id: str = "",
    ) -> int:
        if kind not in {"text", "file", "link"}:
            raise ValueError("tipo de entrega invalido")
        if isinstance(payload, dict):
            payload = json.dumps(payload, ensure_ascii=False)
        position_row = await self._fetchone(
            """
            SELECT COALESCE(MAX(position), 0) + 10 AS position
            FROM delivery_items WHERE product_id = ?
            """,
            (product_id,),
        )
        return await self._execute(
            """
            INSERT INTO delivery_items(
                product_id, kind, payload, caption, button_text, button_style,
                button_emoji_id, position
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                product_id,
                kind,
                payload,
                caption,
                button_text,
                button_style,
                button_emoji_id,
                int(position_row["position"] if position_row else 10),
            ),
        )

    async def delete_delivery_item(self, item_id: int) -> None:
        await self._execute("DELETE FROM delivery_items WHERE id = ?", (item_id,))

    async def move_delivery_item(self, item_id: int, direction: int) -> None:
        item = await self.get_delivery_item(item_id)
        if item:
            await self._move(
                "delivery_items",
                item_id,
                direction,
                "product_id = ?",
                (item["product_id"],),
            )

    async def create_order(
        self,
        *,
        telegram_user_id: int,
        product: Record,
        payment_method: str,
        amount: int,
        currency: str,
    ) -> Record:
        public_id = str(uuid.uuid4())
        order_id = await self._execute(
            """
            INSERT INTO orders(
                public_id, telegram_user_id, product_id, product_name,
                payment_method, amount, currency
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                public_id,
                telegram_user_id,
                product["id"],
                product["name"],
                payment_method,
                amount,
                currency,
            ),
        )
        order = await self.get_order_by_id(order_id)
        if order is None:
            raise RuntimeError("pedido criado mas nao encontrado")
        return order

    async def get_order_by_id(self, order_id: int) -> Record | None:
        return await self._fetchone("SELECT * FROM orders WHERE id = ?", (order_id,))

    async def get_order_by_public_id(self, public_id: str) -> Record | None:
        return await self._fetchone("SELECT * FROM orders WHERE public_id = ?", (public_id,))

    async def get_order_by_external_payment(self, payment_id: str) -> Record | None:
        return await self._fetchone(
            "SELECT * FROM orders WHERE external_payment_id = ?", (payment_id,)
        )

    async def set_external_payment(self, order_id: int, payment_id: str) -> None:
        await self._execute(
            """
            UPDATE orders SET external_payment_id = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (payment_id, order_id),
        )

    async def set_payment_message(self, order_id: int, chat_id: int, message_id: int) -> None:
        await self._execute(
            """
            UPDATE orders SET payment_message_chat_id = ?, payment_message_id = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (chat_id, message_id, order_id),
        )

    async def update_order_status(
        self, order_id: int, status: str, status_detail: str = ""
    ) -> None:
        await self._execute(
            """
            UPDATE orders SET status = ?, status_detail = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (status, status_detail, order_id),
        )

    async def approve_stars_order(
        self,
        order_id: int,
        *,
        telegram_charge_id: str,
        provider_charge_id: str,
    ) -> None:
        await self._execute(
            """
            UPDATE orders SET status = 'approved', status_detail = '',
                telegram_charge_id = ?, provider_charge_id = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (telegram_charge_id, provider_charge_id, order_id),
        )

    async def approve_pix_order(self, order_id: int, payment_id: str) -> None:
        await self._execute(
            """
            UPDATE orders SET status = 'approved', status_detail = '',
                external_payment_id = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (payment_id, order_id),
        )

    async def list_user_purchases(self, telegram_user_id: int, limit: int = 30) -> list[Record]:
        return await self._fetchall(
            """
            SELECT * FROM orders
            WHERE telegram_user_id = ? AND status = 'approved'
            ORDER BY created_at DESC LIMIT ?
            """,
            (telegram_user_id, limit),
        )

    async def list_orders(self, limit: int = 30) -> list[Record]:
        return await self._fetchall(
            "SELECT * FROM orders ORDER BY created_at DESC LIMIT ?", (limit,)
        )

    async def delivery_was_sent(self, order_id: int, delivery_item_id: int) -> bool:
        row = await self._fetchone(
            """
            SELECT 1 AS found FROM delivery_log
            WHERE order_id = ? AND delivery_item_id = ?
            """,
            (order_id, delivery_item_id),
        )
        return row is not None

    async def log_delivery(
        self, order_id: int, delivery_item_id: int, telegram_message_id: int | None
    ) -> None:
        await self._execute(
            """
            INSERT OR IGNORE INTO delivery_log(
                order_id, delivery_item_id, telegram_message_id
            ) VALUES (?, ?, ?)
            """,
            (order_id, delivery_item_id, telegram_message_id),
        )

    async def mark_order_delivered_if_complete(self, order_id: int) -> bool:
        row = await self._fetchone(
            """
            SELECT
                (SELECT COUNT(*) FROM delivery_items d WHERE d.product_id = o.product_id)
                    AS expected,
                (SELECT COUNT(*) FROM delivery_log l WHERE l.order_id = o.id)
                    AS delivered
            FROM orders o WHERE o.id = ?
            """,
            (order_id,),
        )
        complete = bool(row and row["expected"] > 0 and row["expected"] == row["delivered"])
        if complete:
            await self._execute(
                """
                UPDATE orders SET
                    delivered_at = COALESCE(
                        delivered_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    ),
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (order_id,),
            )
        return complete

    async def stats(self) -> Record:
        row = await self._fetchone(
            """
            SELECT
                (SELECT COUNT(*) FROM users) AS users,
                (SELECT COUNT(*) FROM categories WHERE is_archived = 0) AS categories,
                (SELECT COUNT(*) FROM products WHERE is_archived = 0) AS products,
                (SELECT COUNT(*) FROM orders) AS orders,
                (SELECT COUNT(*) FROM orders WHERE status = 'approved') AS approved
            """
        )
        return row or {"users": 0, "categories": 0, "products": 0, "orders": 0, "approved": 0}
