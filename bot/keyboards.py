from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup

from bot.database import Record
from bot.utils.buttons import button


def main_keyboard(settings: dict[str, str], *, is_admin: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            button(
                settings["products_text"],
                callback_data="store:categories",
                style=settings["products_style"],
                emoji_id=settings["products_emoji_id"],
            )
        ],
        [
            button(
                settings["purchases_text"],
                callback_data="store:purchases",
                style=settings["purchases_style"],
                emoji_id=settings["purchases_emoji_id"],
            )
        ],
    ]
    if settings.get("support_url"):
        rows.append(
            [
                button(
                    settings["support_text"],
                    url=settings["support_url"],
                    style=settings["support_style"],
                    emoji_id=settings["support_emoji_id"],
                )
            ]
        )
    if is_admin:
        rows.append([button("Painel administrativo", callback_data="admin:home", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def categories_keyboard(categories: list[Record], settings: dict[str, str]) -> InlineKeyboardMarkup:
    rows = [
        [
            button(
                category["button_text"],
                callback_data=f"store:category:{category['id']}",
                style=category["button_style"],
                emoji_id=category["button_emoji_id"],
            )
        ]
        for category in categories
    ]
    rows.append([back_button(settings, "store:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def products_keyboard(
    products: list[Record], category_id: int, settings: dict[str, str]
) -> InlineKeyboardMarkup:
    rows = [
        [
            button(
                product["button_text"],
                callback_data=f"store:product:{product['id']}",
                style=product["button_style"],
                emoji_id=product["button_emoji_id"],
            )
        ]
        for product in products
    ]
    rows.append([back_button(settings, "store:categories")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def product_keyboard(
    product: Record,
    settings: dict[str, str],
    *,
    stars_enabled: bool,
    pix_enabled: bool,
) -> InlineKeyboardMarkup:
    rows = []
    if stars_enabled and product["allow_stars"] and product["price_stars"] > 0:
        rows.append(
            [
                button(
                    settings["stars_text"],
                    callback_data=f"pay:stars:{product['id']}",
                    style=settings["stars_style"],
                    emoji_id=settings["stars_emoji_id"],
                )
            ]
        )
    if pix_enabled and product["allow_pix"] and product["price_brl_cents"] > 0:
        rows.append(
            [
                button(
                    settings["pix_text"],
                    callback_data=f"pay:pix:{product['id']}",
                    style=settings["pix_style"],
                    emoji_id=settings["pix_emoji_id"],
                )
            ]
        )
    rows.append([back_button(settings, f"store:category:{product['category_id']}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_button(settings: dict[str, str], callback_data: str):
    return button(
        settings["back_text"],
        callback_data=callback_data,
        style=settings["back_style"],
        emoji_id=settings["back_emoji_id"],
    )
