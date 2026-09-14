from __future__ import annotations

import base64
import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from bot.config import Config
from bot.database import (
    Database,
    OutOfStockError,
    product_has_fulfillment,
    product_has_stock,
)
from bot.services.mercadopago import (
    MercadoPagoClient,
    MercadoPagoError,
    MercadoPagoNotConfigured,
    pix_transaction_data,
)
from bot.services.payments import PaymentProcessor, PaymentValidationError, StockPaymentError
from bot.utils.buttons import button
from bot.utils.money import format_brl
from bot.utils.text import shorten, strip_html

logger = logging.getLogger(__name__)
router = Router(name="payments")


async def _sellable_product(db: Database, product_id: int):
    product = await db.get_product(product_id)
    if (
        product is None
        or not product["is_visible"]
        or not product_has_stock(product)
        or not product_has_fulfillment(product)
    ):
        return None
    category = await db.get_category(int(product["category_id"]))
    if category is None or not category["is_visible"]:
        return None
    return product


@router.callback_query(F.data.startswith("pay:stars:"))
async def pay_stars(callback: CallbackQuery, bot: Bot, db: Database) -> None:
    product_id = int(callback.data.rsplit(":", 1)[1])
    product = await _sellable_product(db, product_id)
    settings = await db.get_settings()
    if (
        product is None
        or settings.get("stars_enabled") != "1"
        or not product["allow_stars"]
        or int(product["price_stars"]) <= 0
    ):
        await callback.answer("Pagamento por Stars indisponível.", show_alert=True)
        return
    await db.upsert_user(
        callback.from_user.id, callback.from_user.first_name, callback.from_user.username
    )
    try:
        order = await db.create_order(
            telegram_user_id=callback.from_user.id,
            product=product,
            payment_method="stars",
            amount=int(product["price_stars"]),
            currency="XTR",
            reservation_minutes=30,
        )
    except OutOfStockError:
        await callback.answer("Produto esgotado.", show_alert=True)
        return
    pay_button = button(
        settings["stars_text"],
        pay=True,
        style=settings["stars_style"],
        emoji_id=settings["stars_emoji_id"],
    )
    await callback.answer()
    try:
        await bot.send_invoice(
            chat_id=callback.from_user.id,
            title=shorten(strip_html(str(product["name"])), 32),
            description=shorten(strip_html(str(product["description"] or product["name"])), 255),
            payload=f"dsb:{order['public_id']}",
            provider_token="",
            currency="XTR",
            prices=[
                LabeledPrice(
                    label=shorten(strip_html(str(product["name"])), 32),
                    amount=order["amount"],
                )
            ],
            start_parameter=f"product_{product_id}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[pay_button]]),
        )
    except Exception:
        await db.update_order_status(int(order["id"]), "error", "telegram_invoice_send_failed")
        raise


@router.callback_query(F.data.startswith("pay:pix:"))
async def pay_pix(
    callback: CallbackQuery,
    bot: Bot,
    db: Database,
    config: Config,
    mercado_pago: MercadoPagoClient,
) -> None:
    product_id = int(callback.data.rsplit(":", 1)[1])
    product = await _sellable_product(db, product_id)
    settings = await db.get_settings()
    if (
        product is None
        or settings.get("pix_enabled") != "1"
        or not product["allow_pix"]
        or int(product["price_brl_cents"]) <= 0
    ):
        await callback.answer("Pagamento por PIX indisponível.", show_alert=True)
        return
    if not config.mercado_pago_ready:
        await callback.answer("PIX aguardando configuração do administrador.", show_alert=True)
        return
    await db.upsert_user(
        callback.from_user.id, callback.from_user.first_name, callback.from_user.username
    )
    try:
        order = await db.create_order(
            telegram_user_id=callback.from_user.id,
            product=product,
            payment_method="pix",
            amount=int(product["price_brl_cents"]),
            currency="BRL",
            reservation_minutes=config.pix_expiration_minutes,
        )
    except OutOfStockError:
        await callback.answer("Produto esgotado.", show_alert=True)
        return
    await callback.answer("Gerando PIX…")
    try:
        payment = await mercado_pago.create_pix_payment(
            order=order,
            product_description=str(product["name"]),
            payer_first_name=callback.from_user.first_name,
        )
        transaction = pix_transaction_data(payment)
    except (MercadoPagoError, MercadoPagoNotConfigured) as exc:
        logger.exception("could not create PIX for order %s", order["public_id"])
        await db.update_order_status(int(order["id"]), "error", str(exc))
        await bot.send_message(
            callback.from_user.id,
            "Não foi possível gerar o PIX agora. Tente novamente em instantes.",
        )
        return

    payment_id = str(payment["id"])
    await db.set_external_payment(int(order["id"]), payment_id)
    qr_code = str(transaction["qr_code"])
    rows = [
        [button("Copiar código PIX", copy_text=qr_code, style="success")],
        [
            button(
                "Verificar pagamento",
                callback_data=f"pay:check:{order['public_id']}",
                style="primary",
            )
        ],
    ]
    ticket_url = transaction.get("ticket_url")
    if ticket_url:
        rows.insert(1, [button("Abrir página do PIX", url=str(ticket_url))])
    caption = (
        f"<b>PIX — {escape(str(product['name']))}</b>\n\n"
        f"Valor: <b>{format_brl(int(order['amount']))}</b>\n"
        f"Expira em {config.pix_expiration_minutes} minutos.\n\n"
        "Use o botão para copiar o código. A entrega será automática após a aprovação."
    )
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    qr_base64 = str(transaction.get("qr_code_base64") or "")
    sent: Message
    if qr_base64:
        if "," in qr_base64:
            qr_base64 = qr_base64.split(",", 1)[1]
        try:
            image = base64.b64decode(qr_base64, validate=True)
            sent = await bot.send_photo(
                callback.from_user.id,
                BufferedInputFile(image, filename="pix.png"),
                caption=caption,
                reply_markup=markup,
            )
        except (ValueError, base64.binascii.Error):
            sent = await bot.send_message(callback.from_user.id, caption, reply_markup=markup)
    else:
        sent = await bot.send_message(callback.from_user.id, caption, reply_markup=markup)
    await db.set_payment_message(int(order["id"]), sent.chat.id, sent.message_id)


@router.callback_query(F.data.startswith("pay:check:"))
async def check_pix(callback: CallbackQuery, db: Database, processor: PaymentProcessor) -> None:
    public_id = callback.data.removeprefix("pay:check:")
    order = await db.get_order_by_public_id(public_id)
    if (
        order is None
        or int(order["telegram_user_id"]) != callback.from_user.id
        or order["payment_method"] != "pix"
    ):
        await callback.answer("Pedido não encontrado.", show_alert=True)
        return
    if not order.get("external_payment_id"):
        await callback.answer("Pagamento ainda não registrado.", show_alert=True)
        return
    try:
        status = await processor.process_pix(str(order["external_payment_id"]))
    except (MercadoPagoError, PaymentValidationError):
        logger.exception("manual PIX check failed for order %s", public_id)
        await callback.answer("Não foi possível consultar agora.", show_alert=True)
        return
    messages = {
        "approved": "Pagamento aprovado e conteúdo liberado.",
        "pending": "Pagamento ainda pendente.",
        "rejected": "Pagamento rejeitado.",
        "cancelled": "Pagamento cancelado.",
        "refunded": "Pagamento devolvido.",
        "charged_back": "Pagamento contestado.",
    }
    await callback.answer(messages.get(status, f"Status: {status}"), show_alert=True)


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery, processor: PaymentProcessor) -> None:
    valid, error = await processor.validate_pre_checkout(query)
    await query.answer(ok=valid, error_message=error or None)


@router.message(F.successful_payment)
async def successful_stars(
    message: Message, processor: PaymentProcessor, config: Config, bot: Bot
) -> None:
    try:
        await processor.process_stars(message, message.successful_payment)
    except StockPaymentError:
        logger.exception("Stars payment arrived without a valid stock reservation")
    except PaymentValidationError:
        logger.exception("invalid Stars payment received")
        await message.answer(
            "⚠️ O pagamento foi recebido, mas a entrega precisa de revisão. "
            "O administrador já foi avisado."
        )
        for admin_id in config.admin_ids:
            await bot.send_message(
                admin_id,
                "⚠️ Um pagamento Stars não passou pela validação. Consulte os logs.",
            )
