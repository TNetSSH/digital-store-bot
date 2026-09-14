from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from html import escape

from aiogram import Bot
from aiogram.types import Message, PreCheckoutQuery, SuccessfulPayment

from bot.config import Config
from bot.database import Database, Record, StockReservationError
from bot.services.delivery import DeliveryService
from bot.services.mercadopago import MercadoPagoClient
from bot.utils.money import InvalidMoney, external_amount_to_cents

logger = logging.getLogger(__name__)


class PaymentValidationError(RuntimeError):
    pass


class StockPaymentError(PaymentValidationError):
    pass


class PaymentProcessor:
    PIX_STATUS = {
        "pending": "pending",
        "in_process": "pending",
        "authorized": "pending",
        "approved": "approved",
        "rejected": "rejected",
        "cancelled": "cancelled",
        "refunded": "refunded",
        "charged_back": "charged_back",
    }

    def __init__(
        self,
        *,
        bot: Bot,
        db: Database,
        config: Config,
        mercado_pago: MercadoPagoClient,
        delivery: DeliveryService,
    ) -> None:
        self.bot = bot
        self.db = db
        self.config = config
        self.mercado_pago = mercado_pago
        self.delivery = delivery
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def validate_pre_checkout(self, query: PreCheckoutQuery) -> tuple[bool, str]:
        public_id = self._stars_public_id(query.invoice_payload)
        if not public_id:
            return False, "Pedido inválido. Volte ao produto e tente novamente."
        order = await self.db.get_order_by_public_id(public_id)
        if order is None:
            return False, "Pedido não encontrado. Volte ao produto e tente novamente."
        if order["payment_method"] != "stars" or order["status"] != "pending":
            return False, "Este pedido não está mais disponível para pagamento."
        if int(order["telegram_user_id"]) != query.from_user.id:
            return False, "Este pedido pertence a outro usuário."
        if query.currency != "XTR" or query.total_amount != int(order["amount"]):
            return False, "O valor do pedido mudou. Gere uma nova cobrança."
        if not await self.db.ensure_order_reservation(int(order["id"])):
            return False, "A reserva expirou ou o produto esgotou. Gere uma nova cobrança."
        return True, ""

    async def process_stars(self, message: Message, payment: SuccessfulPayment) -> bool:
        public_id = self._stars_public_id(payment.invoice_payload)
        if not public_id or message.from_user is None:
            raise PaymentValidationError("payload Stars invalido")
        async with self._locks[public_id]:
            order = await self.db.get_order_by_public_id(public_id)
            if order is None:
                raise PaymentValidationError("pedido Stars nao encontrado")
            self._validate_common(
                order,
                telegram_user_id=message.from_user.id,
                currency=payment.currency,
                amount=payment.total_amount,
                method="stars",
            )
            already_approved = order["status"] == "approved"
            existing_charge = order.get("telegram_charge_id")
            if existing_charge and existing_charge != payment.telegram_payment_charge_id:
                raise PaymentValidationError("pedido possui outro identificador Stars")
            if not already_approved:
                try:
                    await self.db.approve_stars_order(
                        int(order["id"]),
                        telegram_charge_id=payment.telegram_payment_charge_id,
                        provider_charge_id=payment.provider_payment_charge_id,
                    )
                except StockReservationError as exc:
                    await self._notify_stock_problem(order)
                    raise StockPaymentError(str(exc)) from exc
                await self.bot.send_message(
                    message.chat.id,
                    f"✅ Pagamento aprovado: <b>{escape(str(order['product_name']))}</b>.",
                )
                await self._notify_low_stock(order)
            current = await self.db.get_order_by_id(int(order["id"]))
            if current:
                await self.delivery.deliver_order(current)
            return not already_approved

    async def process_pix(self, payment_id: str) -> str:
        payment = await self.mercado_pago.get_payment(payment_id)
        public_id = str(payment.get("external_reference") or "")
        if not public_id:
            by_external = await self.db.get_order_by_external_payment(str(payment.get("id", "")))
            if by_external:
                public_id = str(by_external["public_id"])
        if not public_id:
            raise PaymentValidationError("pagamento PIX sem referencia de pedido")

        async with self._locks[public_id]:
            order = await self.db.get_order_by_public_id(public_id)
            if order is None:
                raise PaymentValidationError("pedido PIX nao encontrado")
            try:
                amount = external_amount_to_cents(payment.get("transaction_amount"))
            except InvalidMoney as exc:
                raise PaymentValidationError("valor PIX invalido") from exc
            self._validate_common(
                order,
                telegram_user_id=int(order["telegram_user_id"]),
                currency=str(payment.get("currency_id") or ""),
                amount=amount,
                method="pix",
            )

            external_id = str(payment.get("id") or payment_id)
            existing_external = str(order.get("external_payment_id") or "")
            if existing_external and existing_external != external_id:
                raise PaymentValidationError("pedido possui outro pagamento PIX")
            if not existing_external:
                await self.db.set_external_payment(int(order["id"]), external_id)

            provider_status = str(payment.get("status") or "pending")
            local_status = self.PIX_STATUS.get(provider_status, "pending")
            detail = str(payment.get("status_detail") or "")
            was_approved = order["status"] == "approved"
            if local_status == "approved":
                try:
                    await self.db.approve_pix_order(int(order["id"]), external_id)
                except StockReservationError as exc:
                    await self._notify_stock_problem(order)
                    raise StockPaymentError(str(exc)) from exc
                if not was_approved:
                    await self._remove_pix_qr(order)
                    await self.bot.send_message(
                        int(order["telegram_user_id"]),
                        f"✅ PIX aprovado: <b>{escape(str(order['product_name']))}</b>.",
                    )
                    await self._notify_low_stock(order)
                current = await self.db.get_order_by_id(int(order["id"]))
                if current:
                    await self.delivery.deliver_order(current)
            elif not was_approved or local_status in {"refunded", "charged_back"}:
                await self.db.update_order_status(int(order["id"]), local_status, detail)
                if local_status in {"refunded", "charged_back"} and was_approved:
                    await self._notify_reversal(order, local_status)
            return local_status

    async def _remove_pix_qr(self, order: Record) -> None:
        chat_id = order.get("payment_message_chat_id")
        message_id = order.get("payment_message_id")
        if chat_id and message_id:
            try:
                await self.bot.delete_message(int(chat_id), int(message_id))
            except Exception:  # pragma: no cover - Telegram may already have deleted it
                logger.info("could not delete PIX message for order %s", order["public_id"])

    async def _notify_reversal(self, order: Record, status: str) -> None:
        text = (
            "⚠️ <b>Pagamento PIX revertido</b>\n\n"
            f"Status: {status}\nPedido: <code>{order['public_id']}</code>\n"
            f"Produto: {escape(str(order['product_name']))}"
        )
        for admin_id in self.config.admin_ids:
            try:
                await self.bot.send_message(admin_id, text)
            except Exception:  # pragma: no cover
                logger.exception("failed to notify admin about reversed PIX")

    async def _notify_low_stock(self, order: Record) -> None:
        try:
            available = await self.db.claim_low_stock_alert(int(order["product_id"]))
        except Exception:  # pragma: no cover - checkout must continue if alerting fails
            logger.exception("failed to calculate low stock alert")
            return
        if available is None:
            return
        text = (
            "⚠️ <b>Estoque baixo</b>\n\n"
            f"Produto: {escape(str(order['product_name']))}\n"
            f"Disponível: <b>{available}</b>"
        )
        for admin_id in self.config.admin_ids:
            try:
                await self.bot.send_message(admin_id, text)
            except Exception:  # pragma: no cover - depends on Telegram availability
                logger.exception("failed to notify admin about low stock")

    async def _notify_stock_problem(self, order: Record) -> None:
        await self.bot.send_message(
            int(order["telegram_user_id"]),
            "⚠️ O pagamento foi confirmado, mas a reserva de estoque precisa de revisão. "
            "O administrador já foi avisado.",
        )
        text = (
            "🚨 <b>Pagamento aprovado sem reserva de estoque</b>\n\n"
            f"Pedido: <code>{order['public_id']}</code>\n"
            f"Produto: {escape(str(order['product_name']))}"
        )
        for admin_id in self.config.admin_ids:
            try:
                await self.bot.send_message(admin_id, text)
            except Exception:  # pragma: no cover
                logger.exception("failed to notify admin about stock reservation")

    @staticmethod
    def _stars_public_id(payload: str) -> str | None:
        prefix = "dsb:"
        if not payload.startswith(prefix):
            return None
        public_id = payload[len(prefix) :]
        return public_id if len(public_id) == 36 else None

    @staticmethod
    def _validate_common(
        order: Record,
        *,
        telegram_user_id: int,
        currency: str,
        amount: int,
        method: str,
    ) -> None:
        if int(order["telegram_user_id"]) != telegram_user_id:
            raise PaymentValidationError("usuario do pagamento nao confere")
        if order["payment_method"] != method:
            raise PaymentValidationError("metodo do pagamento nao confere")
        if order["currency"] != currency:
            raise PaymentValidationError("moeda do pagamento nao confere")
        if int(order["amount"]) != amount:
            raise PaymentValidationError("valor do pagamento nao confere")
        if order["status"] in {
            "cancelled",
            "rejected",
            "refunded",
            "charged_back",
            "expired",
            "error",
        }:
            raise PaymentValidationError("pedido nao pode mais ser aprovado")
