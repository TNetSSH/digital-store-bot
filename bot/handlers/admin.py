from __future__ import annotations

import json
from html import escape
from urllib.parse import urlparse

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import BaseFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from bot.config import Config
from bot.database import (
    Database,
    Record,
    StockModeChangeError,
    product_has_fulfillment,
)
from bot.services.delivery import DeliveryService
from bot.states import AdminInput
from bot.ui import replace_menu
from bot.utils.buttons import ALLOWED_STYLES, button
from bot.utils.money import InvalidMoney, format_brl, parse_brl_to_cents
from bot.utils.text import admin_html

router = Router(name="admin")


class AdminOnly(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery, config: Config) -> bool:
        if not event.from_user or event.from_user.id not in config.admin_ids:
            return False
        if isinstance(event, Message):
            return event.chat.type == ChatType.PRIVATE
        return bool(event.message and event.message.chat.type == ChatType.PRIVATE)


router.message.filter(AdminOnly())
router.callback_query.filter(AdminOnly())


STYLE_LABELS = {
    "default": "Padrão",
    "primary": "Azul",
    "success": "Verde",
    "danger": "Vermelho",
}

STOCK_LABELS = {
    "unlimited": "Ilimitado",
    "quantity": "Por quantidade",
    "unique": "Itens únicos",
}


def _markup(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _cancel_markup() -> InlineKeyboardMarkup:
    return _markup([[button("Cancelar", callback_data="admin:cancel", style="danger")]])


def _style_markup(context: str) -> InlineKeyboardMarkup:
    return _markup(
        [
            [
                button(
                    label,
                    callback_data=f"admin:style:{context}:{style}",
                    style=style,
                )
            ]
            for style, label in STYLE_LABELS.items()
        ]
        + [[button("Cancelar", callback_data="admin:cancel", style="danger")]]
    )


def _yes_no(value: object) -> str:
    return "Sim" if bool(value) else "Não"


def _content_name(item: Record) -> str:
    names = {"text": "Texto", "file": "Arquivo", "link": "Link"}
    return f"#{item['position']} · {names.get(item['kind'], item['kind'])}"


def _stock_summary(item: Record) -> str:
    mode = str(item["stock_mode"])
    if mode == "unlimited":
        return "Ilimitado"
    return (
        f"{STOCK_LABELS[mode]} · {item['stock_available']} disponível(is) · "
        f"{item['stock_reserved']} reservado(s) · {item['stock_sold']} vendido(s)"
    )


async def _admin_home(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    stats = await db.stats()
    text = (
        "<b>Digital Store Bot — Administração</b>\n\n"
        f"Usuários: <b>{stats['users']}</b>\n"
        f"Categorias: <b>{stats['categories']}</b>\n"
        f"Produtos: <b>{stats['products']}</b>\n"
        f"Pedidos: <b>{stats['orders']}</b>\n"
        f"Aprovados: <b>{stats['approved']}</b>"
    )
    rows = [
        [button("Categorias", callback_data="admin:categories", style="primary")],
        [button("Produtos", callback_data="admin:products", style="primary")],
        [button("Pedidos", callback_data="admin:orders")],
        [button("Personalização", callback_data="admin:settings")],
        [button("Ver loja", callback_data="store:home", style="success")],
    ]
    await replace_menu(callback, bot, text=text, reply_markup=_markup(rows))


@router.message(Command("admin"))
async def admin_command(message: Message, db: Database) -> None:
    stats = await db.stats()
    text = (
        "<b>Digital Store Bot — Administração</b>\n\n"
        f"Usuários: <b>{stats['users']}</b>\n"
        f"Categorias: <b>{stats['categories']}</b>\n"
        f"Produtos: <b>{stats['products']}</b>\n"
        f"Pedidos: <b>{stats['orders']}</b>\n"
        f"Aprovados: <b>{stats['approved']}</b>"
    )
    await message.answer(
        text,
        reply_markup=_markup(
            [
                [button("Categorias", callback_data="admin:categories", style="primary")],
                [button("Produtos", callback_data="admin:products", style="primary")],
                [button("Pedidos", callback_data="admin:orders")],
                [button("Personalização", callback_data="admin:settings")],
                [button("Ver loja", callback_data="store:home", style="success")],
            ]
        ),
    )


@router.callback_query(F.data == "admin:home")
async def admin_home(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    await _admin_home(callback, bot, db)


@router.callback_query(F.data == "admin:cancel")
async def cancel(callback: CallbackQuery, bot: Bot, db: Database, state: FSMContext) -> None:
    await state.clear()
    await _admin_home(callback, bot, db)


@router.callback_query(F.data == "admin:categories")
async def categories(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    items = await db.list_categories()
    rows = [
        [
            button(
                ("🟢 " if item["is_visible"] else "⚪ ") + str(item["name"])[:42],
                callback_data=f"admin:category:{item['id']}",
            )
        ]
        for item in items
    ]
    rows.extend(
        [
            [button("Nova categoria", callback_data="admin:category:new", style="success")],
            [button("Voltar", callback_data="admin:home")],
        ]
    )
    text = "<b>Categorias</b>\n\n🟢 Visível · ⚪ Oculta"
    if not items:
        text += "\n\nNenhuma categoria cadastrada."
    await replace_menu(callback, bot, text=text, reply_markup=_markup(rows))


async def _category_detail(
    callback: CallbackQuery, bot: Bot, db: Database, category_id: int
) -> None:
    item = await db.get_category(category_id)
    if item is None:
        await callback.answer("Categoria não encontrada.", show_alert=True)
        return
    text = (
        f"<b>Categoria: {escape(str(item['name']))}</b>\n\n"
        f"Descrição: {item['description'] or '—'}\n"
        f"Botão: {escape(str(item['button_text']))}\n"
        f"Cor: {STYLE_LABELS.get(item['button_style'], item['button_style'])}\n"
        f"Emoji premium ID: <code>{item['button_emoji_id'] or '—'}</code>\n"
        f"Visível: <b>{_yes_no(item['is_visible'])}</b>"
    )
    rows = [
        [
            button("Nome", callback_data=f"admin:cedit:{category_id}:name"),
            button("Descrição", callback_data=f"admin:cedit:{category_id}:description"),
        ],
        [
            button("Texto do botão", callback_data=f"admin:cedit:{category_id}:button_text"),
            button("Emoji premium", callback_data=f"admin:cedit:{category_id}:button_emoji_id"),
        ],
        [button("Cor do botão", callback_data=f"admin:cstyle:{category_id}")],
        [
            button("⬆️", callback_data=f"admin:cmove:{category_id}:-1"),
            button("⬇️", callback_data=f"admin:cmove:{category_id}:1"),
            button(
                "Ocultar" if item["is_visible"] else "Publicar",
                callback_data=f"admin:ctoggle:{category_id}",
                style="danger" if item["is_visible"] else "success",
            ),
        ],
        [button("Produtos desta categoria", callback_data=f"admin:cproducts:{category_id}")],
        [button("Arquivar", callback_data=f"admin:carchive:{category_id}", style="danger")],
        [button("Voltar", callback_data="admin:categories")],
    ]
    await replace_menu(callback, bot, text=text, reply_markup=_markup(rows))


@router.callback_query(F.data.startswith("admin:category:") & ~F.data.endswith(":new"))
async def category_detail(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    await _category_detail(callback, bot, db, int(callback.data.rsplit(":", 1)[1]))


@router.callback_query(F.data == "admin:category:new")
async def category_new(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    await state.set_state(AdminInput.waiting)
    await state.set_data({"action": "category_new_name", "draft": {}})
    await replace_menu(
        callback,
        bot,
        text="Envie o <b>nome</b> da nova categoria.",
        reply_markup=_cancel_markup(),
    )


@router.callback_query(F.data.startswith("admin:cedit:"))
async def category_edit(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    _, _, raw_id, field = callback.data.split(":", 3)
    labels = {
        "name": "novo nome",
        "description": "nova descrição em HTML",
        "button_text": "novo texto do botão",
        "button_emoji_id": "ID do emoji premium ou - para remover",
    }
    await state.set_state(AdminInput.waiting)
    await state.set_data({"action": "category_edit", "entity_id": int(raw_id), "field": field})
    await replace_menu(
        callback,
        bot,
        text=f"Envie {labels[field]}.",
        reply_markup=_cancel_markup(),
    )


@router.callback_query(F.data.startswith("admin:cstyle:"))
async def category_style(callback: CallbackQuery, bot: Bot) -> None:
    category_id = int(callback.data.rsplit(":", 1)[1])
    await replace_menu(
        callback,
        bot,
        text="Escolha a cor nativa do botão.",
        reply_markup=_style_markup(f"category.{category_id}"),
    )


@router.callback_query(F.data.startswith("admin:ctoggle:"))
async def category_toggle(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    category_id = int(callback.data.rsplit(":", 1)[1])
    item = await db.get_category(category_id)
    if item:
        await db.update_category(category_id, "is_visible", int(not item["is_visible"]))
    await _category_detail(callback, bot, db, category_id)


@router.callback_query(F.data.startswith("admin:cmove:"))
async def category_move(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    _, _, raw_id, raw_direction = callback.data.split(":")
    await db.move_category(int(raw_id), int(raw_direction))
    await _category_detail(callback, bot, db, int(raw_id))


@router.callback_query(F.data.startswith("admin:carchive:"))
async def category_archive_confirm(callback: CallbackQuery, bot: Bot) -> None:
    category_id = int(callback.data.rsplit(":", 1)[1])
    await replace_menu(
        callback,
        bot,
        text="Arquivar esta categoria e todos os produtos dela? As compras antigas serão mantidas.",
        reply_markup=_markup(
            [
                [
                    button(
                        "Confirmar arquivamento",
                        callback_data=f"admin:carchiveok:{category_id}",
                        style="danger",
                    )
                ],
                [button("Cancelar", callback_data=f"admin:category:{category_id}")],
            ]
        ),
    )


@router.callback_query(F.data.startswith("admin:carchiveok:"))
async def category_archive(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    category_id = int(callback.data.rsplit(":", 1)[1])
    await db.archive_category(category_id)
    await categories(callback, bot, db)


@router.callback_query((F.data == "admin:products") | F.data.startswith("admin:cproducts:"))
async def products(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    category_id = None
    if callback.data.startswith("admin:cproducts:"):
        category_id = int(callback.data.rsplit(":", 1)[1])
    items = await db.list_products(category_id=category_id)
    rows = [
        [
            button(
                ("🟢 " if item["is_visible"] else "⚪ ") + str(item["name"])[:42],
                callback_data=f"admin:product:{item['id']}",
            )
        ]
        for item in items
    ]
    rows.append([button("Novo produto", callback_data="admin:product:new", style="success")])
    back = f"admin:category:{category_id}" if category_id else "admin:home"
    rows.append([button("Voltar", callback_data=back)])
    text = "<b>Produtos</b>\n\n🟢 Visível · ⚪ Oculto"
    if not items:
        text += "\n\nNenhum produto cadastrado."
    await replace_menu(callback, bot, text=text, reply_markup=_markup(rows))


async def _product_detail(callback: CallbackQuery, bot: Bot, db: Database, product_id: int) -> None:
    item = await db.get_product(product_id)
    if item is None:
        await callback.answer("Produto não encontrado.", show_alert=True)
        return
    text = (
        f"<b>Produto: {escape(str(item['name']))}</b>\n\n"
        f"Categoria: {escape(str(item['category_name']))}\n"
        f"Descrição: {item['description'] or '—'}\n"
        f"Stars: <b>{item['price_stars']}</b> ({_yes_no(item['allow_stars'])})\n"
        f"PIX: <b>{format_brl(int(item['price_brl_cents']))}</b> "
        f"({_yes_no(item['allow_pix'])})\n"
        f"Estoque: <b>{escape(_stock_summary(item))}</b>\n"
        f"Conteúdos: <b>{item['delivery_count']}</b>\n"
        f"Botão: {escape(str(item['button_text']))}\n"
        f"Cor: {STYLE_LABELS.get(item['button_style'], item['button_style'])}\n"
        f"Emoji premium ID: <code>{item['button_emoji_id'] or '—'}</code>\n"
        f"Imagem: <b>{'Sim' if item['photo_file_id'] else 'Não'}</b>\n"
        f"Visível: <b>{_yes_no(item['is_visible'])}</b>"
    )
    rows = [
        [
            button("Nome", callback_data=f"admin:pedit:{product_id}:name"),
            button("Descrição", callback_data=f"admin:pedit:{product_id}:description"),
        ],
        [
            button("Preço Stars", callback_data=f"admin:pedit:{product_id}:price_stars"),
            button("Preço PIX", callback_data=f"admin:pedit:{product_id}:price_brl_cents"),
        ],
        [
            button(
                "Stars ON" if item["allow_stars"] else "Stars OFF",
                callback_data=f"admin:ptogglepay:{product_id}:allow_stars",
                style="success" if item["allow_stars"] else "danger",
            ),
            button(
                "PIX ON" if item["allow_pix"] else "PIX OFF",
                callback_data=f"admin:ptogglepay:{product_id}:allow_pix",
                style="success" if item["allow_pix"] else "danger",
            ),
        ],
        [
            button("Texto do botão", callback_data=f"admin:pedit:{product_id}:button_text"),
            button("Emoji premium", callback_data=f"admin:pedit:{product_id}:button_emoji_id"),
        ],
        [button("Cor do botão", callback_data=f"admin:pstyle:{product_id}")],
        [
            button("Definir imagem", callback_data=f"admin:pphoto:{product_id}"),
            button("Remover imagem", callback_data=f"admin:pnophoto:{product_id}"),
        ],
        [
            button(
                "Conteúdos de entrega",
                callback_data=f"admin:contents:{product_id}",
                style="primary",
            )
        ],
        [
            button(
                "Estoque",
                callback_data=f"admin:stock:{product_id}",
                style="primary",
            )
        ],
        [
            button("⬆️", callback_data=f"admin:pmove:{product_id}:-1"),
            button("⬇️", callback_data=f"admin:pmove:{product_id}:1"),
            button(
                "Ocultar" if item["is_visible"] else "Publicar",
                callback_data=f"admin:ptoggle:{product_id}",
                style="danger" if item["is_visible"] else "success",
            ),
        ],
        [button("Arquivar", callback_data=f"admin:parchive:{product_id}", style="danger")],
        [button("Voltar", callback_data=f"admin:cproducts:{item['category_id']}")],
    ]
    await replace_menu(
        callback,
        bot,
        text=text,
        reply_markup=_markup(rows),
    )


@router.callback_query(F.data.startswith("admin:product:") & ~F.data.endswith(":new"))
async def product_detail(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    await _product_detail(callback, bot, db, int(callback.data.rsplit(":", 1)[1]))


@router.callback_query(F.data == "admin:product:new")
async def product_new(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    categories_list = await db.list_categories()
    if not categories_list:
        await callback.answer("Cadastre uma categoria primeiro.", show_alert=True)
        return
    rows = [
        [button(c["name"][:45], callback_data=f"admin:pnewcat:{c['id']}")] for c in categories_list
    ]
    rows.append([button("Cancelar", callback_data="admin:products", style="danger")])
    await replace_menu(
        callback,
        bot,
        text="Escolha a categoria do novo produto.",
        reply_markup=_markup(rows),
    )


@router.callback_query(F.data.startswith("admin:pnewcat:"))
async def product_new_category(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    category_id = int(callback.data.rsplit(":", 1)[1])
    await state.set_state(AdminInput.waiting)
    await state.set_data({"action": "product_new_name", "draft": {"category_id": category_id}})
    await replace_menu(
        callback,
        bot,
        text="Envie o <b>nome</b> do produto.",
        reply_markup=_cancel_markup(),
    )


@router.callback_query(F.data.startswith("admin:pedit:"))
async def product_edit(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    _, _, raw_id, field = callback.data.split(":", 3)
    labels = {
        "name": "novo nome",
        "description": "nova descrição em HTML",
        "button_text": "novo texto do botão",
        "button_emoji_id": "ID do emoji premium ou - para remover",
        "price_stars": "novo preço inteiro em Stars (0 para desativar)",
        "price_brl_cents": "novo preço em reais, por exemplo 19,90 (0 para desativar)",
    }
    await state.set_state(AdminInput.waiting)
    await state.set_data({"action": "product_edit", "entity_id": int(raw_id), "field": field})
    await replace_menu(
        callback,
        bot,
        text=f"Envie {labels[field]}.",
        reply_markup=_cancel_markup(),
    )


@router.callback_query(F.data.startswith("admin:pstyle:"))
async def product_style(callback: CallbackQuery, bot: Bot) -> None:
    product_id = int(callback.data.rsplit(":", 1)[1])
    await replace_menu(
        callback,
        bot,
        text="Escolha a cor nativa do botão.",
        reply_markup=_style_markup(f"product.{product_id}"),
    )


@router.callback_query(F.data.startswith("admin:pphoto:"))
async def product_photo(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    product_id = int(callback.data.rsplit(":", 1)[1])
    await state.set_state(AdminInput.waiting)
    await state.set_data({"action": "product_photo", "entity_id": product_id})
    await replace_menu(
        callback,
        bot,
        text="Envie a nova imagem como <b>foto</b>.",
        reply_markup=_cancel_markup(),
    )


@router.callback_query(F.data.startswith("admin:pnophoto:"))
async def product_no_photo(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    product_id = int(callback.data.rsplit(":", 1)[1])
    await db.update_product(product_id, "photo_file_id", "")
    await _product_detail(callback, bot, db, product_id)


async def _stock_detail(callback: CallbackQuery, bot: Bot, db: Database, product_id: int) -> None:
    item = await db.get_product(product_id)
    if item is None:
        await callback.answer("Produto não encontrado.", show_alert=True)
        return
    mode = str(item["stock_mode"])
    text = f"<b>Estoque — {escape(str(item['name']))}</b>\n\nModo: <b>{STOCK_LABELS[mode]}</b>\n"
    if mode == "unlimited":
        text += "O produto pode ser vendido sem limite de unidades."
    else:
        text += (
            f"Disponíveis: <b>{item['stock_available']}</b>\n"
            f"Reservados: <b>{item['stock_reserved']}</b>\n"
            f"Vendidos: <b>{item['stock_sold']}</b>\n"
            f"Avisar quando restarem: <b>{item['low_stock_threshold']}</b>"
        )
    rows = [
        [
            button(
                "Ilimitado",
                callback_data=f"admin:stockmode:{product_id}:unlimited",
                style="success" if mode == "unlimited" else "default",
            ),
            button(
                "Quantidade",
                callback_data=f"admin:stockmode:{product_id}:quantity",
                style="success" if mode == "quantity" else "default",
            ),
        ],
        [
            button(
                "Itens únicos",
                callback_data=f"admin:stockmode:{product_id}:unique",
                style="success" if mode == "unique" else "default",
            )
        ],
    ]
    if mode == "quantity":
        rows.append(
            [
                button(
                    "Definir unidades disponíveis",
                    callback_data=f"admin:stockqty:{product_id}",
                    style="primary",
                )
            ]
        )
    elif mode == "unique":
        rows.extend(
            [
                [
                    button(
                        "Adicionar itens",
                        callback_data=f"admin:stockadd:{product_id}",
                        style="success",
                    )
                ],
                [
                    button(
                        "Gerenciar disponíveis",
                        callback_data=f"admin:stockitems:{product_id}",
                    )
                ],
            ]
        )
    if mode != "unlimited":
        rows.append(
            [
                button(
                    "Limite do aviso",
                    callback_data=f"admin:stockthreshold:{product_id}",
                )
            ]
        )
    rows.append([button("Voltar", callback_data=f"admin:product:{product_id}")])
    await replace_menu(callback, bot, text=text, reply_markup=_markup(rows))


@router.callback_query(F.data.startswith("admin:stock:"))
async def stock_detail(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    await _stock_detail(callback, bot, db, int(callback.data.rsplit(":", 1)[1]))


@router.callback_query(F.data.startswith("admin:stockmode:"))
async def stock_mode(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    _, _, raw_id, mode = callback.data.split(":")
    product_id = int(raw_id)
    try:
        await db.set_stock_mode(product_id, mode)
    except (ValueError, StockModeChangeError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await _stock_detail(callback, bot, db, product_id)


@router.callback_query(F.data.startswith("admin:stockqty:"))
async def stock_quantity(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    product_id = int(callback.data.rsplit(":", 1)[1])
    await state.set_state(AdminInput.waiting)
    await state.set_data({"action": "stock_quantity", "product_id": product_id})
    await replace_menu(
        callback,
        bot,
        text=(
            "Envie a quantidade de unidades <b>disponíveis</b>.\n\n"
            "As unidades já reservadas por pedidos pendentes não serão alteradas."
        ),
        reply_markup=_cancel_markup(),
    )


@router.callback_query(F.data.startswith("admin:stockthreshold:"))
async def stock_threshold(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    product_id = int(callback.data.rsplit(":", 1)[1])
    await state.set_state(AdminInput.waiting)
    await state.set_data({"action": "stock_threshold", "product_id": product_id})
    await replace_menu(
        callback,
        bot,
        text="Avise o saldo em que deseja receber o alerta de estoque baixo (0 ou mais).",
        reply_markup=_cancel_markup(),
    )


@router.callback_query(F.data.startswith("admin:stockadd:"))
async def stock_add(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    product_id = int(callback.data.rsplit(":", 1)[1])
    await state.set_state(AdminInput.waiting)
    await state.set_data({"action": "stock_add_items", "product_id": product_id})
    await replace_menu(
        callback,
        bot,
        text=(
            "Envie os códigos, contas, licenças ou links. Use <b>um item por linha</b>.\n\n"
            "Cada compra receberá automaticamente uma linha diferente."
        ),
        reply_markup=_cancel_markup(),
    )


@router.callback_query(F.data.startswith("admin:stockitems:"))
async def stock_items(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    product_id = int(callback.data.rsplit(":", 1)[1])
    product = await db.get_product(product_id)
    if product is None:
        await callback.answer("Produto não encontrado.", show_alert=True)
        return
    items = await db.list_available_stock_items(product_id)
    rows = [
        [
            button(
                f"Remover #{item['id']} · {str(item['value']).replace(chr(10), ' ')[:28]}",
                callback_data=f"admin:stockitemconfirm:{product_id}:{item['id']}",
                style="danger",
            )
        ]
        for item in items
    ]
    if items:
        rows.append(
            [
                button(
                    "Remover todos disponíveis",
                    callback_data=f"admin:stockclearconfirm:{product_id}",
                    style="danger",
                )
            ]
        )
    rows.append([button("Voltar", callback_data=f"admin:stock:{product_id}")])
    text = (
        f"<b>Itens disponíveis — {escape(str(product['name']))}</b>\n\n"
        f"Exibindo até 30 de <b>{product['stock_available']}</b>. "
        "Itens reservados ou vendidos não podem ser removidos."
    )
    if not items:
        text += "\n\nNenhum item disponível."
    await replace_menu(callback, bot, text=text, reply_markup=_markup(rows))


@router.callback_query(F.data.startswith("admin:stockitemconfirm:"))
async def stock_item_confirm(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    _, _, raw_product_id, raw_item_id = callback.data.split(":")
    product_id, item_id = int(raw_product_id), int(raw_item_id)
    items = await db.list_available_stock_items(product_id, limit=1000)
    item = next((row for row in items if int(row["id"]) == item_id), None)
    if item is None:
        await callback.answer("Item indisponível ou já reservado.", show_alert=True)
        return
    await replace_menu(
        callback,
        bot,
        text=f"Remover este item disponível?\n\n<pre>{escape(str(item['value']))}</pre>",
        reply_markup=_markup(
            [
                [
                    button(
                        "Confirmar remoção",
                        callback_data=f"admin:stockitemdelete:{product_id}:{item_id}",
                        style="danger",
                    )
                ],
                [button("Cancelar", callback_data=f"admin:stockitems:{product_id}")],
            ]
        ),
    )


@router.callback_query(F.data.startswith("admin:stockitemdelete:"))
async def stock_item_delete(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    _, _, raw_product_id, raw_item_id = callback.data.split(":")
    product_id, item_id = int(raw_product_id), int(raw_item_id)
    removed = await db.delete_available_stock_item(product_id, item_id)
    await callback.answer("Item removido." if removed else "Item não pôde ser removido.")
    callback = callback.model_copy(update={"data": f"admin:stockitems:{product_id}"})
    await stock_items(callback, bot, db)


@router.callback_query(F.data.startswith("admin:stockclearconfirm:"))
async def stock_clear_confirm(callback: CallbackQuery, bot: Bot) -> None:
    product_id = int(callback.data.rsplit(":", 1)[1])
    await replace_menu(
        callback,
        bot,
        text="Remover todos os itens disponíveis? Reservados e vendidos serão preservados.",
        reply_markup=_markup(
            [
                [
                    button(
                        "Confirmar remoção",
                        callback_data=f"admin:stockclear:{product_id}",
                        style="danger",
                    )
                ],
                [button("Cancelar", callback_data=f"admin:stockitems:{product_id}")],
            ]
        ),
    )


@router.callback_query(F.data.startswith("admin:stockclear:"))
async def stock_clear(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    product_id = int(callback.data.rsplit(":", 1)[1])
    removed = await db.clear_available_stock_items(product_id)
    await callback.answer(f"{removed} item(ns) removido(s).")
    callback = callback.model_copy(update={"data": f"admin:stockitems:{product_id}"})
    await stock_items(callback, bot, db)


@router.callback_query(F.data.startswith("admin:ptogglepay:"))
async def product_toggle_payment(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    _, _, raw_id, field = callback.data.split(":")
    product_id = int(raw_id)
    item = await db.get_product(product_id)
    if item is None:
        await callback.answer("Produto não encontrado.", show_alert=True)
        return
    price_field = "price_stars" if field == "allow_stars" else "price_brl_cents"
    if not item[field] and int(item[price_field]) <= 0:
        await callback.answer("Defina um preço maior que zero primeiro.", show_alert=True)
        return
    await db.update_product(product_id, field, int(not item[field]))
    await _product_detail(callback, bot, db, product_id)


@router.callback_query(F.data.startswith("admin:ptoggle:"))
async def product_toggle(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    product_id = int(callback.data.rsplit(":", 1)[1])
    item = await db.get_product(product_id)
    if item is None:
        await callback.answer("Produto não encontrado.", show_alert=True)
        return
    if not item["is_visible"]:
        valid_price = (item["allow_stars"] and item["price_stars"] > 0) or (
            item["allow_pix"] and item["price_brl_cents"] > 0
        )
        if not product_has_fulfillment(item) or not valid_price:
            await callback.answer(
                "Adicione conteúdo (ou itens únicos) e ao menos um pagamento com preço "
                "antes de publicar.",
                show_alert=True,
            )
            return
    await db.update_product(product_id, "is_visible", int(not item["is_visible"]))
    await _product_detail(callback, bot, db, product_id)


@router.callback_query(F.data.startswith("admin:pmove:"))
async def product_move(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    _, _, raw_id, raw_direction = callback.data.split(":")
    await db.move_product(int(raw_id), int(raw_direction))
    await _product_detail(callback, bot, db, int(raw_id))


@router.callback_query(F.data.startswith("admin:parchive:"))
async def product_archive_confirm(callback: CallbackQuery, bot: Bot) -> None:
    product_id = int(callback.data.rsplit(":", 1)[1])
    await replace_menu(
        callback,
        bot,
        text="Arquivar este produto? Os pedidos e as compras antigas serão mantidos.",
        reply_markup=_markup(
            [
                [
                    button(
                        "Confirmar arquivamento",
                        callback_data=f"admin:parchiveok:{product_id}",
                        style="danger",
                    )
                ],
                [button("Cancelar", callback_data=f"admin:product:{product_id}")],
            ]
        ),
    )


@router.callback_query(F.data.startswith("admin:parchiveok:"))
async def product_archive(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    product_id = int(callback.data.rsplit(":", 1)[1])
    item = await db.get_product(product_id)
    await db.archive_product(product_id)
    if item:
        callback = callback.model_copy(update={"data": f"admin:cproducts:{item['category_id']}"})
    await products(callback, bot, db)


@router.callback_query(F.data.startswith("admin:contents:"))
async def contents(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    product_id = int(callback.data.rsplit(":", 1)[1])
    product = await db.get_product(product_id)
    if product is None:
        await callback.answer("Produto não encontrado.", show_alert=True)
        return
    items = await db.list_delivery_items(product_id)
    rows = [
        [button(_content_name(item), callback_data=f"admin:content:{item['id']}")] for item in items
    ]
    rows.extend(
        [
            [
                button(
                    "Adicionar conteúdo",
                    callback_data=f"admin:contentnew:{product_id}",
                    style="success",
                )
            ],
            [button("Voltar", callback_data=f"admin:product:{product_id}")],
        ]
    )
    text = (
        f"<b>Entrega: {escape(str(product['name']))}</b>\n\nOs itens são enviados na ordem exibida."
    )
    if not items:
        text += "\n\nNenhum conteúdo adicionado."
    await replace_menu(callback, bot, text=text, reply_markup=_markup(rows))


@router.callback_query(F.data.startswith("admin:contentnew:"))
async def content_new(callback: CallbackQuery, bot: Bot) -> None:
    product_id = int(callback.data.rsplit(":", 1)[1])
    await replace_menu(
        callback,
        bot,
        text="Qual conteúdo será entregue?",
        reply_markup=_markup(
            [
                [button("Texto", callback_data=f"admin:contenttype:{product_id}:text")],
                [button("Arquivo", callback_data=f"admin:contenttype:{product_id}:file")],
                [button("Link", callback_data=f"admin:contenttype:{product_id}:link")],
                [button("Cancelar", callback_data=f"admin:contents:{product_id}")],
            ]
        ),
    )


@router.callback_query(F.data.startswith("admin:contenttype:"))
async def content_type(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    _, _, raw_product_id, kind = callback.data.split(":")
    product_id = int(raw_product_id)
    await state.set_state(AdminInput.waiting)
    action = f"content_{kind}"
    await state.set_data({"action": action, "product_id": product_id, "draft": {}})
    prompts = {
        "text": "Envie a mensagem formatada que será entregue.",
        "file": "Envie o documento, foto, vídeo, áudio, animação ou voz.",
        "link": "Envie a URL completa do conteúdo.",
    }
    await replace_menu(
        callback,
        bot,
        text=prompts[kind],
        reply_markup=_cancel_markup(),
    )


@router.callback_query(
    F.data.startswith("admin:content:") & ~F.data.startswith("admin:content:new")
)
async def content_detail(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    item_id = int(callback.data.rsplit(":", 1)[1])
    item = await db.get_delivery_item(item_id)
    if item is None:
        await callback.answer("Conteúdo não encontrado.", show_alert=True)
        return
    preview = str(item["payload"])
    if item["kind"] == "file":
        try:
            data = json.loads(preview)
            preview = f"{data.get('media_type')} · {data.get('filename') or 'arquivo Telegram'}"
        except json.JSONDecodeError:
            preview = "arquivo"
    text = f"<b>{_content_name(item)}</b>\n\nTipo: {item['kind']}\nPrévia: {escape(preview[:300])}"
    rows = [
        [
            button("⬆️", callback_data=f"admin:contentmove:{item_id}:-1"),
            button("⬇️", callback_data=f"admin:contentmove:{item_id}:1"),
        ],
        [button("Excluir", callback_data=f"admin:contentdelete:{item_id}", style="danger")],
        [button("Voltar", callback_data=f"admin:contents:{item['product_id']}")],
    ]
    await replace_menu(callback, bot, text=text, reply_markup=_markup(rows))


@router.callback_query(F.data.startswith("admin:contentmove:"))
async def content_move(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    _, _, raw_id, raw_direction = callback.data.split(":")
    await db.move_delivery_item(int(raw_id), int(raw_direction))
    callback = callback.model_copy(update={"data": f"admin:content:{raw_id}"})
    await content_detail(callback, bot, db)


@router.callback_query(F.data.startswith("admin:contentdelete:"))
async def content_delete_confirm(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    item_id = int(callback.data.rsplit(":", 1)[1])
    item = await db.get_delivery_item(item_id)
    if item is None:
        await callback.answer("Conteúdo não encontrado.", show_alert=True)
        return
    await replace_menu(
        callback,
        bot,
        text="Excluir definitivamente este item de entrega?",
        reply_markup=_markup(
            [
                [
                    button(
                        "Confirmar exclusão",
                        callback_data=f"admin:contentdeleteok:{item_id}",
                        style="danger",
                    )
                ],
                [button("Cancelar", callback_data=f"admin:content:{item_id}")],
            ]
        ),
    )


@router.callback_query(F.data.startswith("admin:contentdeleteok:"))
async def content_delete(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    item_id = int(callback.data.rsplit(":", 1)[1])
    item = await db.get_delivery_item(item_id)
    if item:
        await db.delete_delivery_item(item_id)
        callback = callback.model_copy(update={"data": f"admin:contents:{item['product_id']}"})
        await contents(callback, bot, db)


@router.callback_query(F.data == "admin:orders")
async def orders(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    items = await db.list_orders()
    rows = [
        [
            button(
                f"{item['status']} · {str(item['product_name'])[:28]}",
                callback_data=f"admin:order:{item['id']}",
            )
        ]
        for item in items
    ]
    rows.append([button("Voltar", callback_data="admin:home")])
    text = "<b>Pedidos recentes</b>"
    if not items:
        text += "\n\nNenhum pedido registrado."
    await replace_menu(callback, bot, text=text, reply_markup=_markup(rows))


@router.callback_query(F.data.startswith("admin:order:"))
async def order_detail(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    order_id = int(callback.data.rsplit(":", 1)[1])
    item = await db.get_order_by_id(order_id)
    if item is None:
        await callback.answer("Pedido não encontrado.", show_alert=True)
        return
    amount = (
        f"{item['amount']} Stars" if item["currency"] == "XTR" else format_brl(int(item["amount"]))
    )
    text = (
        f"<b>Pedido #{item['id']}</b>\n\n"
        f"ID: <code>{item['public_id']}</code>\n"
        f"Cliente: <code>{item['telegram_user_id']}</code>\n"
        f"Produto: {escape(str(item['product_name']))}\n"
        f"Pagamento: {item['payment_method']} · {amount}\n"
        f"Status: <b>{item['status']}</b>\n"
        f"Entregue: <b>{'Sim' if item['delivered_at'] else 'Não'}</b>"
    )
    rows = []
    if item["status"] == "approved":
        rows.append(
            [
                button(
                    "Reenviar produto",
                    callback_data=f"admin:orderdeliver:{order_id}",
                    style="success",
                )
            ]
        )
    rows.append([button("Voltar", callback_data="admin:orders")])
    await replace_menu(callback, bot, text=text, reply_markup=_markup(rows))


@router.callback_query(F.data.startswith("admin:orderdeliver:"))
async def order_deliver(callback: CallbackQuery, db: Database, delivery: DeliveryService) -> None:
    order_id = int(callback.data.rsplit(":", 1)[1])
    item = await db.get_order_by_id(order_id)
    if item is None or item["status"] != "approved":
        await callback.answer("Pedido não pode ser reenviado.", show_alert=True)
        return
    await callback.answer("Reenviando…")
    await delivery.deliver_order(item, force=True)


@router.callback_query(F.data == "admin:settings")
async def settings(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    values = await db.get_settings()
    text = (
        "<b>Personalização</b>\n\n"
        f"Stars global: <b>{'ON' if values.get('stars_enabled') == '1' else 'OFF'}</b>\n"
        f"PIX global: <b>{'ON' if values.get('pix_enabled') == '1' else 'OFF'}</b>"
    )
    rows = [
        [button("Mensagem inicial", callback_data="admin:settext:welcome_text")],
        [button("Suporte", callback_data="admin:support")],
        [button("Botões", callback_data="admin:buttons")],
        [button("Pagamentos", callback_data="admin:paymentsettings")],
        [button("Voltar", callback_data="admin:home")],
    ]
    await replace_menu(callback, bot, text=text, reply_markup=_markup(rows))


@router.callback_query(F.data == "admin:support")
async def support_settings(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    values = await db.get_settings()
    text = (
        "<b>Botão de suporte</b>\n\n"
        f"Texto: {escape(values['support_text'])}\n"
        f"URL: {escape(values['support_url'] or '—')}\n"
        f"Cor: {STYLE_LABELS.get(values['support_style'])}\n"
        f"Emoji premium: <code>{values['support_emoji_id'] or '—'}</code>"
    )
    rows = [
        [button("Texto", callback_data="admin:settext:support_text")],
        [button("URL", callback_data="admin:settext:support_url")],
        [button("Cor", callback_data="admin:setstyle:support")],
        [button("Emoji premium", callback_data="admin:setemoji:support")],
        [button("Voltar", callback_data="admin:settings")],
    ]
    await replace_menu(callback, bot, text=text, reply_markup=_markup(rows))


@router.callback_query(F.data == "admin:buttons")
async def buttons_settings(callback: CallbackQuery, bot: Bot) -> None:
    rows = [
        [button("Produtos", callback_data="admin:buttoncfg:products")],
        [button("Minhas compras", callback_data="admin:buttoncfg:purchases")],
        [button("Pagar com Stars", callback_data="admin:buttoncfg:stars")],
        [button("Pagar com PIX", callback_data="admin:buttoncfg:pix")],
        [button("Voltar", callback_data="admin:buttoncfg:back")],
        [button("Voltar ao painel", callback_data="admin:settings")],
    ]
    await replace_menu(
        callback,
        bot,
        text="<b>Botões personalizáveis</b>\n\nEscolha um botão.",
        reply_markup=_markup(rows),
    )


@router.callback_query(F.data.startswith("admin:buttoncfg:"))
async def button_config(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    key = callback.data.rsplit(":", 1)[1]
    values = await db.get_settings()
    text = (
        f"<b>Botão: {escape(values[f'{key}_text'])}</b>\n\n"
        f"Cor: {STYLE_LABELS.get(values[f'{key}_style'])}\n"
        f"Emoji premium: <code>{values[f'{key}_emoji_id'] or '—'}</code>"
    )
    rows = [
        [button("Texto", callback_data=f"admin:settext:{key}_text")],
        [button("Cor", callback_data=f"admin:setstyle:{key}")],
        [button("Emoji premium", callback_data=f"admin:setemoji:{key}")],
        [button("Voltar", callback_data="admin:buttons")],
    ]
    await replace_menu(callback, bot, text=text, reply_markup=_markup(rows))


@router.callback_query(F.data.startswith("admin:settext:"))
async def setting_text(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    key = callback.data.rsplit(":", 1)[1]
    await state.set_state(AdminInput.waiting)
    await state.set_data({"action": "setting_text", "key": key})
    prompt = "Envie o novo valor."
    if key == "welcome_text":
        prompt += " Você pode usar HTML e as variáveis {NAME}, {USERNAME} e {ID}."
    if key == "support_url":
        prompt += " Envie - para ocultar o botão."
    await replace_menu(callback, bot, text=prompt, reply_markup=_cancel_markup())


@router.callback_query(F.data.startswith("admin:setemoji:"))
async def setting_emoji(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    key = callback.data.rsplit(":", 1)[1] + "_emoji_id"
    await state.set_state(AdminInput.waiting)
    await state.set_data({"action": "setting_emoji", "key": key})
    await replace_menu(
        callback,
        bot,
        text="Envie o ID numérico do emoji premium ou - para remover.",
        reply_markup=_cancel_markup(),
    )


@router.callback_query(F.data.startswith("admin:setstyle:"))
async def setting_style(callback: CallbackQuery, bot: Bot) -> None:
    key = callback.data.rsplit(":", 1)[1]
    await replace_menu(
        callback,
        bot,
        text="Escolha a cor nativa do botão.",
        reply_markup=_style_markup(f"setting.{key}"),
    )


@router.callback_query(F.data == "admin:paymentsettings")
async def payment_settings(callback: CallbackQuery, bot: Bot, db: Database, config: Config) -> None:
    values = await db.get_settings()
    missing = ", ".join(config.mercado_pago_missing) or "nenhuma"
    text = (
        "<b>Formas de pagamento</b>\n\n"
        f"Stars: <b>{'ON' if values.get('stars_enabled') == '1' else 'OFF'}</b>\n"
        f"PIX: <b>{'ON' if values.get('pix_enabled') == '1' else 'OFF'}</b>\n"
        f"Configurações PIX ausentes no .env: <code>{missing}</code>"
    )
    rows = [
        [button("Alternar Stars", callback_data="admin:paytoggle:stars", style="primary")],
        [button("Alternar PIX", callback_data="admin:paytoggle:pix", style="success")],
        [button("Voltar", callback_data="admin:settings")],
    ]
    await replace_menu(callback, bot, text=text, reply_markup=_markup(rows))


@router.callback_query(F.data.startswith("admin:paytoggle:"))
async def payment_toggle(callback: CallbackQuery, bot: Bot, db: Database, config: Config) -> None:
    method = callback.data.rsplit(":", 1)[1]
    if method == "pix" and not config.mercado_pago_ready:
        await callback.answer(
            "Preencha as quatro configurações do Mercado Pago no .env primeiro.",
            show_alert=True,
        )
        return
    await db.toggle_setting(f"{method}_enabled")
    callback = callback.model_copy(update={"data": "admin:paymentsettings"})
    await payment_settings(callback, bot, db, config)


@router.callback_query(F.data.startswith("admin:style:"))
async def apply_style(callback: CallbackQuery, bot: Bot, db: Database, state: FSMContext) -> None:
    parts = callback.data.split(":")
    style = parts[-1]
    context = ":".join(parts[2:-1])
    if style not in ALLOWED_STYLES:
        await callback.answer("Cor inválida.", show_alert=True)
        return
    if context == "category_new":
        data = await state.get_data()
        draft = data.get("draft", {})
        category_id = await db.create_category(button_style=style, **draft)
        await state.clear()
        await _category_detail(callback, bot, db, category_id)
        return
    if context == "product_new":
        data = await state.get_data()
        draft = data.get("draft", {})
        product_id = await db.create_product(button_style=style, **draft)
        await state.clear()
        await _product_detail(callback, bot, db, product_id)
        return
    if context == "link_new":
        data = await state.get_data()
        draft = data.get("draft", {})
        product_id = int(data["product_id"])
        await db.create_delivery_item(
            product_id=product_id,
            kind="link",
            button_style=style,
            **draft,
        )
        await state.clear()
        callback = callback.model_copy(update={"data": f"admin:contents:{product_id}"})
        await contents(callback, bot, db)
        return
    entity, _, raw_id = context.partition(".")
    if entity == "category":
        await db.update_category(int(raw_id), "button_style", style)
        await _category_detail(callback, bot, db, int(raw_id))
    elif entity == "product":
        await db.update_product(int(raw_id), "button_style", style)
        await _product_detail(callback, bot, db, int(raw_id))
    elif entity == "setting":
        await db.set_setting(f"{raw_id}_style", style)
        if raw_id == "support":
            callback = callback.model_copy(update={"data": "admin:support"})
            await support_settings(callback, bot, db)
        else:
            callback = callback.model_copy(update={"data": f"admin:buttoncfg:{raw_id}"})
            await button_config(callback, bot, db)


def _plain_text(message: Message) -> str:
    return (message.text or "").strip()


def _html_text(message: Message) -> str:
    raw = message.text or message.caption or ""
    return admin_html(raw, message.html_text or raw)


def _emoji_id(value: str) -> str:
    if value == "-":
        return ""
    if not value.isdigit() or len(value) > 30:
        raise ValueError("envie apenas o ID numérico do emoji ou -")
    return value


def _limited(value: str, maximum: int, label: str) -> str:
    if len(value) > maximum:
        raise ValueError(f"{label} deve possuir no máximo {maximum} caracteres")
    return value


def _valid_url(value: str, *, allow_empty: bool = False) -> str:
    if value == "-" and allow_empty:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https", "tg"} or not parsed.netloc:
        raise ValueError("envie uma URL completa iniciada por https://")
    return _limited(value, 2048, "a URL")


async def _next_message(message: Message, state: FSMContext, *, action: str, text: str) -> None:
    await state.update_data(action=action)
    await message.answer(text, reply_markup=_cancel_markup())


@router.message(AdminInput.waiting)
async def admin_input(message: Message, bot: Bot, db: Database, state: FSMContext) -> None:
    data = await state.get_data()
    action = data.get("action", "")
    draft = dict(data.get("draft", {}))
    try:
        if action == "category_new_name":
            value = _plain_text(message)
            if not 1 <= len(value) <= 100:
                raise ValueError("o nome deve possuir entre 1 e 100 caracteres")
            draft["name"] = value
            await state.update_data(draft=draft)
            await _next_message(
                message,
                state,
                action="category_new_description",
                text="Envie a descrição em HTML ou - para deixar vazia.",
            )
        elif action == "category_new_description":
            description = "" if _plain_text(message) == "-" else _html_text(message)
            draft["description"] = _limited(description, 3000, "a descrição")
            await state.update_data(draft=draft)
            await _next_message(
                message,
                state,
                action="category_new_button",
                text="Envie o texto do botão da categoria.",
            )
        elif action == "category_new_button":
            value = _plain_text(message)
            if not 1 <= len(value) <= 64:
                raise ValueError("o botão deve possuir entre 1 e 64 caracteres")
            draft["button_text"] = value
            await state.update_data(draft=draft)
            await _next_message(
                message,
                state,
                action="category_new_emoji",
                text="Envie o ID do emoji premium ou - para não usar.",
            )
        elif action == "category_new_emoji":
            draft["button_emoji_id"] = _emoji_id(_plain_text(message))
            await state.update_data(draft=draft, action="category_new_style")
            await message.answer(
                "Escolha a cor do botão.", reply_markup=_style_markup("category_new")
            )
        elif action == "category_edit":
            field = data["field"]
            value = _html_text(message) if field == "description" else _plain_text(message)
            if field == "button_emoji_id":
                value = _emoji_id(value)
            elif field == "name":
                if not value:
                    raise ValueError("o valor não pode ficar vazio")
                value = _limited(value, 100, "o nome")
            elif field == "button_text":
                if not value:
                    raise ValueError("o valor não pode ficar vazio")
                value = _limited(value, 64, "o botão")
            elif field == "description":
                value = _limited(value, 3000, "a descrição")
            await db.update_category(int(data["entity_id"]), field, value)
            await state.clear()
            await message.answer("✅ Categoria atualizada.")
            await _send_category_detail(bot, message.from_user.id, db, int(data["entity_id"]))
        elif action == "product_new_name":
            value = _plain_text(message)
            if not 1 <= len(value) <= 100:
                raise ValueError("o nome deve possuir entre 1 e 100 caracteres")
            draft["name"] = value
            await state.update_data(draft=draft)
            await _next_message(
                message,
                state,
                action="product_new_description",
                text="Envie a descrição em HTML ou - para deixar vazia.",
            )
        elif action == "product_new_description":
            description = "" if _plain_text(message) == "-" else _html_text(message)
            draft["description"] = _limited(description, 700, "a descrição")
            await state.update_data(draft=draft)
            await _next_message(
                message,
                state,
                action="product_new_stars",
                text="Informe o preço inteiro em Stars ou 0 para desativar.",
            )
        elif action == "product_new_stars":
            value = int(_plain_text(message))
            if value < 0:
                raise ValueError("o preço não pode ser negativo")
            draft["price_stars"] = value
            await state.update_data(draft=draft)
            await _next_message(
                message,
                state,
                action="product_new_pix",
                text="Informe o preço PIX em reais (ex.: 19,90) ou 0.",
            )
        elif action == "product_new_pix":
            draft["price_brl_cents"] = parse_brl_to_cents(_plain_text(message))
            await state.update_data(draft=draft)
            await _next_message(
                message,
                state,
                action="product_new_button",
                text="Envie o texto do botão do produto.",
            )
        elif action == "product_new_button":
            value = _plain_text(message)
            if not 1 <= len(value) <= 64:
                raise ValueError("o botão deve possuir entre 1 e 64 caracteres")
            draft["button_text"] = value
            await state.update_data(draft=draft)
            await _next_message(
                message,
                state,
                action="product_new_emoji",
                text="Envie o ID do emoji premium ou - para não usar.",
            )
        elif action == "product_new_emoji":
            draft["button_emoji_id"] = _emoji_id(_plain_text(message))
            await state.update_data(draft=draft, action="product_new_style")
            await message.answer(
                "Escolha a cor do botão.", reply_markup=_style_markup("product_new")
            )
        elif action == "product_edit":
            field = data["field"]
            value: str | int = (
                _html_text(message) if field == "description" else _plain_text(message)
            )
            if field == "button_emoji_id":
                value = _emoji_id(str(value))
            elif field == "price_stars":
                value = int(str(value))
                if value < 0:
                    raise ValueError("o preço não pode ser negativo")
            elif field == "price_brl_cents":
                value = parse_brl_to_cents(str(value))
            elif field == "name":
                if not value:
                    raise ValueError("o valor não pode ficar vazio")
                value = _limited(str(value), 100, "o nome")
            elif field == "button_text":
                if not value:
                    raise ValueError("o valor não pode ficar vazio")
                value = _limited(str(value), 64, "o botão")
            elif field == "description":
                value = _limited(str(value), 700, "a descrição")
            product_id = int(data["entity_id"])
            await db.update_product(product_id, field, value)
            if field == "price_stars" and int(value) == 0:
                await db.update_product(product_id, "allow_stars", 0)
            elif field == "price_brl_cents" and int(value) == 0:
                await db.update_product(product_id, "allow_pix", 0)
            await state.clear()
            await message.answer("✅ Produto atualizado.")
            await _send_product_detail(bot, message.from_user.id, db, product_id)
        elif action == "product_photo":
            if not message.photo:
                raise ValueError("envie a imagem usando o tipo Foto do Telegram")
            product_id = int(data["entity_id"])
            await db.update_product(product_id, "photo_file_id", message.photo[-1].file_id)
            await state.clear()
            await message.answer("✅ Imagem atualizada.")
            await _send_product_detail(bot, message.from_user.id, db, product_id)
        elif action == "stock_quantity":
            quantity = int(_plain_text(message))
            if not 0 <= quantity <= 1_000_000_000:
                raise ValueError("a quantidade deve estar entre 0 e 1.000.000.000")
            product_id = int(data["product_id"])
            await db.set_quantity_stock(product_id, quantity)
            await state.clear()
            await message.answer("✅ Quantidade disponível atualizada.")
            await _send_stock_detail(bot, message.from_user.id, db, product_id)
        elif action == "stock_threshold":
            threshold = int(_plain_text(message))
            if not 0 <= threshold <= 1_000_000_000:
                raise ValueError("o limite deve estar entre 0 e 1.000.000.000")
            product_id = int(data["product_id"])
            await db.set_low_stock_threshold(product_id, threshold)
            await state.clear()
            await message.answer("✅ Limite do aviso atualizado.")
            await _send_stock_detail(bot, message.from_user.id, db, product_id)
        elif action == "stock_add_items":
            raw = _plain_text(message)
            values = [line.strip() for line in raw.splitlines() if line.strip()]
            if len(values) > 500:
                raise ValueError("envie no máximo 500 itens por mensagem")
            product_id = int(data["product_id"])
            added, duplicates = await db.add_unique_stock_items(product_id, values)
            await state.clear()
            result = f"✅ {added} item(ns) adicionado(s)."
            if duplicates:
                result += f" {duplicates} duplicado(s) ignorado(s)."
            await message.answer(result)
            await _send_stock_detail(bot, message.from_user.id, db, product_id)
        elif action == "content_text":
            value = _limited(_html_text(message), 4000, "o texto")
            if not value:
                raise ValueError("envie uma mensagem de texto")
            product_id = int(data["product_id"])
            await db.create_delivery_item(product_id=product_id, kind="text", payload=value)
            await state.clear()
            await message.answer("✅ Texto adicionado à entrega.")
            await _send_contents(bot, message.from_user.id, db, product_id)
        elif action == "content_file":
            media_type, file_id, filename = _extract_file(message)
            product_id = int(data["product_id"])
            caption = _limited(
                (message.html_text or message.caption or "").strip(), 900, "a legenda"
            )
            await db.create_delivery_item(
                product_id=product_id,
                kind="file",
                payload={"media_type": media_type, "file_id": file_id, "filename": filename},
                caption=caption,
            )
            await state.clear()
            await message.answer("✅ Arquivo adicionado à entrega.")
            await _send_contents(bot, message.from_user.id, db, product_id)
        elif action == "content_link":
            draft["payload"] = _valid_url(_plain_text(message))
            await state.update_data(draft=draft)
            await _next_message(
                message,
                state,
                action="content_link_caption",
                text="Envie a mensagem exibida acima do botão ou - para usar o texto padrão.",
            )
        elif action == "content_link_caption":
            caption = "" if _plain_text(message) == "-" else _html_text(message)
            draft["caption"] = _limited(caption, 3500, "a mensagem")
            await state.update_data(draft=draft)
            await _next_message(
                message,
                state,
                action="content_link_button",
                text="Envie o texto do botão do link.",
            )
        elif action == "content_link_button":
            value = _plain_text(message)
            if not value:
                raise ValueError("o botão não pode ficar vazio")
            draft["button_text"] = _limited(value, 64, "o botão")
            await state.update_data(draft=draft)
            await _next_message(
                message,
                state,
                action="content_link_emoji",
                text="Envie o ID do emoji premium ou - para não usar.",
            )
        elif action == "content_link_emoji":
            draft["button_emoji_id"] = _emoji_id(_plain_text(message))
            await state.update_data(draft=draft, action="content_link_style")
            await message.answer("Escolha a cor do botão.", reply_markup=_style_markup("link_new"))
        elif action == "setting_text":
            key = str(data["key"])
            value = _html_text(message) if key == "welcome_text" else _plain_text(message)
            if key == "support_url":
                value = _valid_url(value, allow_empty=True)
            elif not value:
                raise ValueError("o valor não pode ficar vazio")
            elif key == "welcome_text":
                value = _limited(value, 3500, "a mensagem inicial")
            elif key.endswith("_text"):
                value = _limited(value, 64, "o botão")
            await db.set_setting(key, value)
            await state.clear()
            await message.answer("✅ Configuração atualizada. Use /admin para retornar ao painel.")
        elif action == "setting_emoji":
            await db.set_setting(str(data["key"]), _emoji_id(_plain_text(message)))
            await state.clear()
            await message.answer("✅ Emoji atualizado. Use /admin para retornar ao painel.")
        else:
            await state.clear()
            await message.answer("A edição expirou. Use /admin para recomeçar.")
    except (ValueError, InvalidMoney) as exc:
        await message.answer(
            f"⚠️ {escape(str(exc))}\n\nTente novamente.", reply_markup=_cancel_markup()
        )


def _extract_file(message: Message) -> tuple[str, str, str]:
    if message.document:
        return "document", message.document.file_id, message.document.file_name or "documento"
    if message.photo:
        return "photo", message.photo[-1].file_id, "foto"
    if message.video:
        return "video", message.video.file_id, message.video.file_name or "video"
    if message.audio:
        return "audio", message.audio.file_id, message.audio.file_name or "audio"
    if message.animation:
        return "animation", message.animation.file_id, message.animation.file_name or "animacao"
    if message.voice:
        return "voice", message.voice.file_id, "audio de voz"
    raise ValueError("envie um documento, foto, vídeo, áudio, animação ou voz")


async def _send_category_detail(bot: Bot, chat_id: int, db: Database, category_id: int) -> None:
    item = await db.get_category(category_id)
    if item is None:
        return
    rows = [
        [button("Abrir categoria", callback_data=f"admin:category:{category_id}", style="primary")],
        [button("Painel", callback_data="admin:home")],
    ]
    await bot.send_message(
        chat_id,
        f"Categoria atual: <b>{escape(str(item['name']))}</b>",
        reply_markup=_markup(rows),
    )


async def _send_product_detail(bot: Bot, chat_id: int, db: Database, product_id: int) -> None:
    item = await db.get_product(product_id)
    if item is None:
        return
    await bot.send_message(
        chat_id,
        f"Produto atual: <b>{escape(str(item['name']))}</b>",
        reply_markup=_markup(
            [
                [
                    button(
                        "Abrir produto",
                        callback_data=f"admin:product:{product_id}",
                        style="primary",
                    )
                ],
                [button("Painel", callback_data="admin:home")],
            ]
        ),
    )


async def _send_stock_detail(bot: Bot, chat_id: int, db: Database, product_id: int) -> None:
    item = await db.get_product(product_id)
    if item is None:
        return
    await bot.send_message(
        chat_id,
        f"Estoque atual: <b>{escape(_stock_summary(item))}</b>",
        reply_markup=_markup(
            [
                [
                    button(
                        "Abrir estoque",
                        callback_data=f"admin:stock:{product_id}",
                        style="primary",
                    )
                ],
                [button("Painel", callback_data="admin:home")],
            ]
        ),
    )


async def _send_contents(bot: Bot, chat_id: int, db: Database, product_id: int) -> None:
    count = len(await db.list_delivery_items(product_id))
    await bot.send_message(
        chat_id,
        f"Conteúdos cadastrados: <b>{count}</b>",
        reply_markup=_markup(
            [
                [
                    button(
                        "Abrir conteúdos",
                        callback_data=f"admin:contents:{product_id}",
                        style="primary",
                    )
                ],
                [button("Painel", callback_data="admin:home")],
            ]
        ),
    )
