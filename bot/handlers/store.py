from __future__ import annotations

from html import escape

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from bot.config import Config
from bot.database import Database
from bot.keyboards import (
    back_button,
    categories_keyboard,
    main_keyboard,
    product_keyboard,
    products_keyboard,
)
from bot.services.delivery import DeliveryService
from bot.ui import replace_menu
from bot.utils.buttons import button
from bot.utils.money import format_brl
from bot.utils.text import render_user_template

router = Router(name="store")


async def _home_text(db: Database, message_user) -> tuple[str, InlineKeyboardMarkup]:
    settings = await db.get_settings()
    text = render_user_template(
        settings["welcome_text"],
        name=message_user.first_name,
        username=message_user.username,
        user_id=message_user.id,
    )
    return text, main_keyboard(settings, is_admin=False)


@router.message(CommandStart(), F.chat.type == ChatType.PRIVATE)
async def start(message: Message, db: Database, config: Config) -> None:
    if message.from_user is None:
        return
    await db.upsert_user(
        message.from_user.id, message.from_user.first_name, message.from_user.username
    )
    settings = await db.get_settings()
    text = render_user_template(
        settings["welcome_text"],
        name=message.from_user.first_name,
        username=message.from_user.username,
        user_id=message.from_user.id,
    )
    await message.answer(
        text,
        reply_markup=main_keyboard(settings, is_admin=message.from_user.id in config.admin_ids),
    )


@router.callback_query(F.data == "store:home")
async def home(callback: CallbackQuery, bot: Bot, db: Database, config: Config) -> None:
    settings = await db.get_settings()
    text = render_user_template(
        settings["welcome_text"],
        name=callback.from_user.first_name,
        username=callback.from_user.username,
        user_id=callback.from_user.id,
    )
    await replace_menu(
        callback,
        bot,
        text=text,
        reply_markup=main_keyboard(settings, is_admin=callback.from_user.id in config.admin_ids),
    )


@router.callback_query(F.data == "store:categories")
async def categories(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    settings = await db.get_settings()
    rows = await db.list_categories(visible_only=True)
    text = "<b>Produtos</b>\n\nEscolha uma categoria."
    if not rows:
        text += "\n\nNenhuma categoria está disponível no momento."
    await replace_menu(
        callback,
        bot,
        text=text,
        reply_markup=categories_keyboard(rows, settings),
    )


@router.callback_query(F.data.startswith("store:category:"))
async def category(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    category_id = int(callback.data.rsplit(":", 1)[1])
    selected = await db.get_category(category_id)
    if selected is None or not selected["is_visible"]:
        await callback.answer("Categoria indisponível.", show_alert=True)
        return
    products = await db.list_products(category_id=category_id, visible_only=True)
    settings = await db.get_settings()
    text = f"<b>{escape(str(selected['name']))}</b>"
    if selected["description"]:
        text += f"\n\n{selected['description']}"
    if not products:
        text += "\n\nNenhum produto está disponível nesta categoria."
    await replace_menu(
        callback,
        bot,
        text=text,
        reply_markup=products_keyboard(products, category_id, settings),
    )


@router.callback_query(F.data.startswith("store:product:"))
async def product(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    product_id = int(callback.data.rsplit(":", 1)[1])
    selected = await db.get_product(product_id)
    category = await db.get_category(int(selected["category_id"])) if selected else None
    if (
        selected is None
        or category is None
        or not selected["is_visible"]
        or not category["is_visible"]
    ):
        await callback.answer("Produto indisponível.", show_alert=True)
        return
    settings = await db.get_settings()
    stars_enabled = settings.get("stars_enabled") == "1"
    pix_enabled = settings.get("pix_enabled") == "1"
    prices = []
    if stars_enabled and selected["allow_stars"] and selected["price_stars"]:
        prices.append(f"⭐ {selected['price_stars']} Stars")
    if pix_enabled and selected["allow_pix"] and selected["price_brl_cents"]:
        prices.append(f"💠 {format_brl(int(selected['price_brl_cents']))}")
    text = f"<b>{escape(str(selected['name']))}</b>"
    if selected["description"]:
        text += f"\n\n{selected['description']}"
    if prices:
        text += "\n\n<b>Preço:</b> " + " ou ".join(prices)
    if not selected["delivery_count"]:
        text += "\n\n⚠️ Temporariamente indisponível para compra."
        stars_enabled = pix_enabled = False
    elif not prices:
        text += "\n\n⚠️ Nenhuma forma de pagamento está disponível."
    await replace_menu(
        callback,
        bot,
        text=text,
        photo_file_id=selected["photo_file_id"],
        reply_markup=product_keyboard(
            selected,
            settings,
            stars_enabled=stars_enabled,
            pix_enabled=pix_enabled,
        ),
    )


@router.callback_query(F.data == "store:purchases")
async def purchases(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    orders = await db.list_user_purchases(callback.from_user.id)
    settings = await db.get_settings()
    rows = [
        [
            button(
                str(order["product_name"])[:48],
                callback_data=f"store:redeliver:{order['public_id']}",
                style="primary",
            )
        ]
        for order in orders
    ]
    rows.append([back_button(settings, "store:home")])
    text = "<b>Minhas compras</b>\n\nSelecione um produto para receber o conteúdo novamente."
    if not orders:
        text = "<b>Minhas compras</b>\n\nVocê ainda não possui compras aprovadas."
    await replace_menu(
        callback,
        bot,
        text=text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("store:redeliver:"))
async def redeliver(callback: CallbackQuery, db: Database, delivery: DeliveryService) -> None:
    public_id = callback.data.removeprefix("store:redeliver:")
    order = await db.get_order_by_public_id(public_id)
    if (
        order is None
        or int(order["telegram_user_id"]) != callback.from_user.id
        or order["status"] != "approved"
    ):
        await callback.answer("Compra não encontrada.", show_alert=True)
        return
    await callback.answer("Reenviando conteúdo…")
    await delivery.deliver_order(order, force=True)


@router.message(F.chat.type != ChatType.PRIVATE)
async def ignore_groups(message: Message) -> None:
    return
