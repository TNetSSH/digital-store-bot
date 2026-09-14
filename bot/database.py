from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import aiosqlite

Record = dict[str, Any]

STOCK_MODES = {"unlimited", "quantity", "unique"}


class OutOfStockError(RuntimeError):
    """Raised when a limited product cannot reserve another unit."""


class StockReservationError(RuntimeError):
    """Raised when an order no longer owns a valid stock reservation."""


class StockModeChangeError(RuntimeError):
    """Raised when stock mode cannot be changed safely."""


def product_has_stock(product: Record) -> bool:
    return product.get("stock_mode") == "unlimited" or int(product.get("stock_available", 0)) > 0


def product_has_fulfillment(product: Record) -> bool:
    return int(product.get("delivery_count", 0)) > 0 or (
        product.get("stock_mode") == "unique" and product_has_stock(product)
    )


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
    stock_mode TEXT NOT NULL DEFAULT 'unlimited'
        CHECK (stock_mode IN ('unlimited', 'quantity', 'unique')),
    stock_quantity INTEGER NOT NULL DEFAULT 0 CHECK (stock_quantity >= 0),
    low_stock_threshold INTEGER NOT NULL DEFAULT 3 CHECK (low_stock_threshold >= 0),
    stock_alerted INTEGER NOT NULL DEFAULT 0 CHECK (stock_alerted IN (0, 1)),
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
    stock_mode TEXT NOT NULL DEFAULT 'unlimited'
        CHECK (stock_mode IN ('unlimited', 'quantity', 'unique')),
    stock_state TEXT NOT NULL DEFAULT 'none'
        CHECK (stock_state IN ('none', 'reserved', 'consumed', 'released')),
    reservation_expires_at TEXT,
    stock_item_delivered_at TEXT,
    stock_item_message_id INTEGER,
    payment_message_chat_id INTEGER,
    payment_message_id INTEGER,
    delivered_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS stock_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id),
    value TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'available'
        CHECK (status IN ('available', 'reserved', 'sold')),
    order_id INTEGER UNIQUE REFERENCES orders(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(product_id, value)
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
CREATE INDEX IF NOT EXISTS idx_stock_items_product
    ON stock_items(product_id, status, id);
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
        "low_stock_threshold",
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
        await self._migrate_stock_schema()
        settings = dict(DEFAULT_SETTINGS)
        settings["stars_enabled"] = "1" if stars_enabled else "0"
        settings["pix_enabled"] = "1" if pix_enabled else "0"
        await self.connection.executemany(
            "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", settings.items()
        )
        await self.connection.commit()

    async def _migrate_stock_schema(self) -> None:
        """Upgrade databases created by versions older than 1.1.0 in place."""

        db = self._db()
        migrations = {
            "products": {
                "stock_mode": (
                    "TEXT NOT NULL DEFAULT 'unlimited' "
                    "CHECK (stock_mode IN ('unlimited', 'quantity', 'unique'))"
                ),
                "stock_quantity": "INTEGER NOT NULL DEFAULT 0 CHECK (stock_quantity >= 0)",
                "low_stock_threshold": (
                    "INTEGER NOT NULL DEFAULT 3 CHECK (low_stock_threshold >= 0)"
                ),
                "stock_alerted": ("INTEGER NOT NULL DEFAULT 0 CHECK (stock_alerted IN (0, 1))"),
            },
            "orders": {
                "stock_mode": (
                    "TEXT NOT NULL DEFAULT 'unlimited' "
                    "CHECK (stock_mode IN ('unlimited', 'quantity', 'unique'))"
                ),
                "stock_state": (
                    "TEXT NOT NULL DEFAULT 'none' "
                    "CHECK (stock_state IN ('none', 'reserved', 'consumed', 'released'))"
                ),
                "reservation_expires_at": "TEXT",
                "stock_item_delivered_at": "TEXT",
                "stock_item_message_id": "INTEGER",
            },
        }
        for table, columns in migrations.items():
            async with db.execute(f"PRAGMA table_info({table})") as cursor:
                existing = {str(row[1]) for row in await cursor.fetchall()}
            for column, definition in columns.items():
                if column not in existing:
                    await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_orders_stock "
            "ON orders(stock_state, reservation_expires_at)"
        )

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

    async def _execute_affected(self, sql: str, params: Iterable[Any] = ()) -> int:
        async with self._write_lock:
            cursor = await self._db().execute(sql, tuple(params))
            await self._db().commit()
            return max(cursor.rowcount, 0)

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
        await self.release_expired_reservations()
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
                    AS delivery_count,
                CASE p.stock_mode
                    WHEN 'unlimited' THEN -1
                    WHEN 'quantity' THEN p.stock_quantity
                    ELSE (SELECT COUNT(*) FROM stock_items si
                          WHERE si.product_id = p.id AND si.status = 'available')
                END AS stock_available,
                (SELECT COUNT(*) FROM orders o
                 WHERE o.product_id = p.id AND o.stock_state = 'reserved') AS stock_reserved,
                (SELECT COUNT(*) FROM orders o
                 WHERE o.product_id = p.id AND o.stock_state = 'consumed') AS stock_sold
            FROM products p
            JOIN categories c ON c.id = p.category_id
            WHERE {" AND ".join(clauses)}
            ORDER BY c.position, c.id, p.position, p.id
            """,  # noqa: S608
            params,
        )

    async def get_product(self, product_id: int) -> Record | None:
        await self.release_expired_reservations(product_id=product_id)
        return await self._fetchone(
            """
            SELECT p.*, c.name AS category_name,
                (SELECT COUNT(*) FROM delivery_items d WHERE d.product_id = p.id)
                    AS delivery_count,
                CASE p.stock_mode
                    WHEN 'unlimited' THEN -1
                    WHEN 'quantity' THEN p.stock_quantity
                    ELSE (SELECT COUNT(*) FROM stock_items si
                          WHERE si.product_id = p.id AND si.status = 'available')
                END AS stock_available,
                (SELECT COUNT(*) FROM orders o
                 WHERE o.product_id = p.id AND o.stock_state = 'reserved') AS stock_reserved,
                (SELECT COUNT(*) FROM orders o
                 WHERE o.product_id = p.id AND o.stock_state = 'consumed') AS stock_sold
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

    async def set_stock_mode(self, product_id: int, mode: str) -> None:
        if mode not in STOCK_MODES:
            raise ValueError("modo de estoque invalido")
        await self.release_expired_reservations(product_id=product_id)
        async with self._write_lock:
            db = self._db()
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute(
                    "SELECT stock_mode FROM products WHERE id = ? AND is_archived = 0",
                    (product_id,),
                ) as cursor:
                    product = await cursor.fetchone()
                if product is None:
                    raise ValueError("produto nao encontrado")
                if str(product["stock_mode"]) != mode:
                    async with db.execute(
                        """
                        SELECT COUNT(*) FROM orders
                        WHERE product_id = ? AND stock_state = 'reserved'
                        """,
                        (product_id,),
                    ) as cursor:
                        reserved = int((await cursor.fetchone())[0])
                    if reserved:
                        raise StockModeChangeError(
                            "aguarde ou cancele as reservas pendentes antes de mudar o modo"
                        )
                await db.execute(
                    """
                    UPDATE products SET stock_mode = ?, stock_alerted = 0,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE id = ?
                    """,
                    (mode, product_id),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def set_quantity_stock(self, product_id: int, quantity: int) -> None:
        if quantity < 0:
            raise ValueError("a quantidade nao pode ser negativa")
        await self.release_expired_reservations(product_id=product_id)
        affected = await self._execute_affected(
            """
            UPDATE products SET stock_quantity = ?, stock_alerted = 0,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ? AND stock_mode = 'quantity' AND is_archived = 0
            """,
            (quantity, product_id),
        )
        if affected != 1:
            raise ValueError("selecione o modo por quantidade primeiro")

    async def set_low_stock_threshold(self, product_id: int, threshold: int) -> None:
        if threshold < 0:
            raise ValueError("o limite de aviso nao pode ser negativo")
        await self.update_product(product_id, "low_stock_threshold", threshold)
        await self._execute("UPDATE products SET stock_alerted = 0 WHERE id = ?", (product_id,))

    async def add_unique_stock_items(
        self, product_id: int, values: Iterable[str]
    ) -> tuple[int, int]:
        supplied_values = [value.strip() for value in values if value.strip()]
        unique_values = list(dict.fromkeys(supplied_values))
        if not unique_values:
            raise ValueError("envie ao menos um item")
        if any(len(value) > 3500 for value in unique_values):
            raise ValueError("cada item deve possuir no maximo 3500 caracteres")
        await self.release_expired_reservations(product_id=product_id)
        async with self._write_lock:
            db = self._db()
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute(
                    "SELECT stock_mode FROM products WHERE id = ? AND is_archived = 0",
                    (product_id,),
                ) as cursor:
                    product = await cursor.fetchone()
                if product is None or product["stock_mode"] != "unique":
                    raise ValueError("selecione o modo de itens unicos primeiro")
                added = 0
                for value in unique_values:
                    cursor = await db.execute(
                        """
                        INSERT OR IGNORE INTO stock_items(product_id, value)
                        VALUES (?, ?)
                        """,
                        (product_id, value),
                    )
                    added += max(cursor.rowcount, 0)
                if added:
                    await db.execute(
                        "UPDATE products SET stock_alerted = 0 WHERE id = ?", (product_id,)
                    )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return added, len(supplied_values) - added

    async def list_available_stock_items(self, product_id: int, limit: int = 30) -> list[Record]:
        await self.release_expired_reservations(product_id=product_id)
        return await self._fetchall(
            """
            SELECT * FROM stock_items
            WHERE product_id = ? AND status = 'available'
            ORDER BY id LIMIT ?
            """,
            (product_id, limit),
        )

    async def delete_available_stock_item(self, product_id: int, item_id: int) -> bool:
        affected = await self._execute_affected(
            """
            DELETE FROM stock_items
            WHERE id = ? AND product_id = ? AND status = 'available'
            """,
            (item_id, product_id),
        )
        return affected == 1

    async def clear_available_stock_items(self, product_id: int) -> int:
        return await self._execute_affected(
            "DELETE FROM stock_items WHERE product_id = ? AND status = 'available'",
            (product_id,),
        )

    async def get_order_stock_item(self, order_id: int) -> Record | None:
        return await self._fetchone(
            """
            SELECT * FROM stock_items
            WHERE order_id = ? AND status IN ('reserved', 'sold')
            """,
            (order_id,),
        )

    async def mark_stock_item_delivered(self, order_id: int, message_id: int) -> None:
        await self._execute(
            """
            UPDATE orders SET
                stock_item_delivered_at = COALESCE(
                    stock_item_delivered_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                ),
                stock_item_message_id = COALESCE(stock_item_message_id, ?),
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (message_id, order_id),
        )

    async def release_expired_reservations(self, *, product_id: int | None = None) -> int:
        params: list[Any] = []
        product_clause = ""
        if product_id is not None:
            product_clause = " AND product_id = ?"
            params.append(product_id)
        async with self._write_lock:
            db = self._db()
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute(
                    f"""
                    SELECT id, product_id, stock_mode FROM orders
                    WHERE stock_state = 'reserved' AND status = 'pending'
                      AND reservation_expires_at IS NOT NULL
                      AND reservation_expires_at <= strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                      {product_clause}
                    """,  # noqa: S608
                    params,
                ) as cursor:
                    expired = await cursor.fetchall()
                for order in expired:
                    if order["stock_mode"] == "quantity":
                        await db.execute(
                            """
                            UPDATE products SET stock_quantity = stock_quantity + 1,
                                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                            WHERE id = ?
                            """,
                            (order["product_id"],),
                        )
                    elif order["stock_mode"] == "unique":
                        await db.execute(
                            """
                            UPDATE stock_items SET status = 'available', order_id = NULL,
                                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                            WHERE order_id = ? AND status = 'reserved'
                            """,
                            (order["id"],),
                        )
                    await db.execute(
                        """
                        UPDATE orders SET status = 'expired', stock_state = 'released',
                            status_detail = 'stock_reservation_expired',
                            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                        WHERE id = ? AND stock_state = 'reserved'
                        """,
                        (order["id"],),
                    )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return len(expired)

    async def ensure_order_reservation(self, order_id: int) -> bool:
        await self.release_expired_reservations()
        order = await self.get_order_by_id(order_id)
        if order is None or order["status"] != "pending":
            return False
        if order["stock_mode"] == "unlimited":
            return order["stock_state"] == "none"
        return order["stock_state"] == "reserved"

    async def claim_low_stock_alert(self, product_id: int) -> int | None:
        await self.release_expired_reservations(product_id=product_id)
        async with self._write_lock:
            db = self._db()
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute(
                    """
                    SELECT stock_mode, stock_quantity, low_stock_threshold, stock_alerted
                    FROM products WHERE id = ?
                    """,
                    (product_id,),
                ) as cursor:
                    product = await cursor.fetchone()
                if product is None or product["stock_mode"] == "unlimited":
                    await db.commit()
                    return None
                if product["stock_mode"] == "quantity":
                    available = int(product["stock_quantity"])
                else:
                    async with db.execute(
                        """
                        SELECT COUNT(*) FROM stock_items
                        WHERE product_id = ? AND status = 'available'
                        """,
                        (product_id,),
                    ) as cursor:
                        available = int((await cursor.fetchone())[0])
                if available > int(product["low_stock_threshold"]) or product["stock_alerted"]:
                    await db.commit()
                    return None
                await db.execute(
                    "UPDATE products SET stock_alerted = 1 WHERE id = ?", (product_id,)
                )
                await db.commit()
                return available
            except Exception:
                await db.rollback()
                raise

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
        reservation_minutes: int = 30,
    ) -> Record:
        if reservation_minutes < 1:
            raise ValueError("o tempo de reserva deve ser positivo")
        await self.release_expired_reservations(product_id=int(product["id"]))
        public_id = str(uuid.uuid4())
        async with self._write_lock:
            db = self._db()
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute(
                    """
                    SELECT id, name, stock_mode, stock_quantity
                    FROM products WHERE id = ? AND is_archived = 0
                    """,
                    (product["id"],),
                ) as cursor:
                    current = await cursor.fetchone()
                if current is None:
                    raise ValueError("produto nao encontrado")
                stock_mode = str(current["stock_mode"])
                stock_state = "none" if stock_mode == "unlimited" else "reserved"
                stock_item_id: int | None = None
                if stock_mode == "quantity" and int(current["stock_quantity"]) <= 0:
                    raise OutOfStockError("produto esgotado")
                if stock_mode == "unique":
                    async with db.execute(
                        """
                        SELECT id FROM stock_items
                        WHERE product_id = ? AND status = 'available'
                        ORDER BY id LIMIT 1
                        """,
                        (product["id"],),
                    ) as cursor:
                        stock_item = await cursor.fetchone()
                    if stock_item is None:
                        raise OutOfStockError("produto esgotado")
                    stock_item_id = int(stock_item["id"])

                expires_modifier = f"+{reservation_minutes} minutes"
                cursor = await db.execute(
                    """
                    INSERT INTO orders(
                        public_id, telegram_user_id, product_id, product_name,
                        payment_method, amount, currency, stock_mode, stock_state,
                        reservation_expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,
                        CASE WHEN ? = 'reserved'
                            THEN strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ?)
                            ELSE NULL
                        END
                    )
                    """,
                    (
                        public_id,
                        telegram_user_id,
                        current["id"],
                        current["name"],
                        payment_method,
                        amount,
                        currency,
                        stock_mode,
                        stock_state,
                        stock_state,
                        expires_modifier,
                    ),
                )
                order_id = int(cursor.lastrowid)
                if stock_mode == "quantity":
                    cursor = await db.execute(
                        """
                        UPDATE products SET stock_quantity = stock_quantity - 1,
                            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                        WHERE id = ? AND stock_mode = 'quantity' AND stock_quantity > 0
                        """,
                        (current["id"],),
                    )
                    if cursor.rowcount != 1:
                        raise OutOfStockError("produto esgotado")
                elif stock_mode == "unique":
                    cursor = await db.execute(
                        """
                        UPDATE stock_items SET status = 'reserved', order_id = ?,
                            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                        WHERE id = ? AND status = 'available'
                        """,
                        (order_id, stock_item_id),
                    )
                    if cursor.rowcount != 1:
                        raise OutOfStockError("produto esgotado")
                await db.commit()
            except Exception:
                await db.rollback()
                raise
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
        release = status in {"error", "expired", "rejected", "cancelled"}
        async with self._write_lock:
            db = self._db()
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute(
                    "SELECT product_id, stock_mode, stock_state FROM orders WHERE id = ?",
                    (order_id,),
                ) as cursor:
                    order = await cursor.fetchone()
                if order is None:
                    await db.rollback()
                    return
                if release and order["stock_state"] == "reserved":
                    if order["stock_mode"] == "quantity":
                        await db.execute(
                            """
                            UPDATE products SET stock_quantity = stock_quantity + 1,
                                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                            WHERE id = ?
                            """,
                            (order["product_id"],),
                        )
                    elif order["stock_mode"] == "unique":
                        await db.execute(
                            """
                            UPDATE stock_items SET status = 'available', order_id = NULL,
                                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                            WHERE order_id = ? AND status = 'reserved'
                            """,
                            (order_id,),
                        )
                stock_state = "released" if release and order["stock_state"] == "reserved" else None
                await db.execute(
                    """
                    UPDATE orders SET status = ?, status_detail = ?,
                        stock_state = COALESCE(?, stock_state),
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE id = ?
                    """,
                    (status, status_detail, stock_state, order_id),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def approve_stars_order(
        self,
        order_id: int,
        *,
        telegram_charge_id: str,
        provider_charge_id: str,
    ) -> None:
        await self._approve_order(
            order_id,
            telegram_charge_id=telegram_charge_id,
            provider_charge_id=provider_charge_id,
        )

    async def approve_pix_order(self, order_id: int, payment_id: str) -> None:
        await self._approve_order(order_id, external_payment_id=payment_id)

    async def _approve_order(
        self,
        order_id: int,
        *,
        telegram_charge_id: str | None = None,
        provider_charge_id: str | None = None,
        external_payment_id: str | None = None,
    ) -> None:
        async with self._write_lock:
            db = self._db()
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)) as cursor:
                    order = await cursor.fetchone()
                if order is None:
                    raise ValueError("pedido nao encontrado")
                if order["status"] == "approved" and order["stock_state"] in {
                    "none",
                    "consumed",
                }:
                    await db.commit()
                    return
                if order["stock_mode"] != "unlimited" and order["stock_state"] != "reserved":
                    raise StockReservationError("a reserva de estoque deste pedido expirou")
                if order["stock_mode"] == "unique":
                    cursor = await db.execute(
                        """
                        UPDATE stock_items SET status = 'sold',
                            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                        WHERE order_id = ? AND status = 'reserved'
                        """,
                        (order_id,),
                    )
                    if cursor.rowcount != 1:
                        raise StockReservationError("item exclusivo reservado nao encontrado")
                stock_state = "none" if order["stock_mode"] == "unlimited" else "consumed"
                await db.execute(
                    """
                    UPDATE orders SET status = 'approved', status_detail = '',
                        telegram_charge_id = COALESCE(?, telegram_charge_id),
                        provider_charge_id = COALESCE(?, provider_charge_id),
                        external_payment_id = COALESCE(?, external_payment_id),
                        stock_state = ?,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE id = ?
                    """,
                    (
                        telegram_charge_id,
                        provider_charge_id,
                        external_payment_id,
                        stock_state,
                        order_id,
                    ),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

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
                    + CASE WHEN o.stock_mode = 'unique' AND o.stock_state = 'consumed'
                        THEN 1 ELSE 0 END AS expected,
                (SELECT COUNT(*) FROM delivery_log l WHERE l.order_id = o.id)
                    + CASE WHEN o.stock_item_delivered_at IS NOT NULL
                        THEN 1 ELSE 0 END AS delivered
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
