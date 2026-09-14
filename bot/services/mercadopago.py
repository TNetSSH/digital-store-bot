from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

from bot.config import Config
from bot.database import Record
from bot.utils.money import cents_to_api_amount

MERCADO_PAGO_BRAZIL_TIMEZONE = timezone(timedelta(hours=-3))


class MercadoPagoError(RuntimeError):
    pass


class MercadoPagoNotConfigured(MercadoPagoError):
    pass


def format_mercado_pago_datetime(value: datetime) -> str:
    """Format a datetime exactly as required by Mercado Pago's Payments API."""
    if value.tzinfo is None:
        raise ValueError("datetime must include timezone information")
    return (
        value.astimezone(MERCADO_PAGO_BRAZIL_TIMEZONE)
        .replace(microsecond=0)
        .isoformat(timespec="milliseconds")
    )


class MercadoPagoClient:
    API_BASE = "https://api.mercadopago.com"

    def __init__(self, config: Config, session: aiohttp.ClientSession) -> None:
        self.config = config
        self.session = session

    def ensure_ready(self) -> None:
        if not self.config.mercado_pago_ready:
            missing = ", ".join(self.config.mercado_pago_missing)
            raise MercadoPagoNotConfigured(f"configuracao PIX incompleta: {missing}")

    async def create_pix_payment(
        self,
        *,
        order: Record,
        product_description: str,
        payer_first_name: str,
    ) -> Record:
        self.ensure_ready()
        expiration = datetime.now(timezone.utc) + timedelta(
            minutes=self.config.pix_expiration_minutes
        )
        body = {
            "transaction_amount": cents_to_api_amount(int(order["amount"])),
            "description": product_description[:255],
            "payment_method_id": "pix",
            "external_reference": order["public_id"],
            "notification_url": f"{self.config.public_base_url}/webhooks/mercadopago",
            "date_of_expiration": format_mercado_pago_datetime(expiration),
            "payer": {
                "email": self.config.mercado_pago_payer_email,
                "first_name": payer_first_name[:100] or "Cliente",
            },
            "metadata": {
                "order_id": order["public_id"],
                "telegram_user_id": str(order["telegram_user_id"]),
            },
        }
        return await self._request(
            "POST",
            "/v1/payments",
            json=body,
            idempotency_key=str(order["public_id"]),
        )

    async def get_payment(self, payment_id: str) -> Record:
        self.ensure_ready()
        if not payment_id or not payment_id.replace("-", "").isalnum():
            raise MercadoPagoError("identificador de pagamento invalido")
        return await self._request("GET", f"/v1/payments/{payment_id}")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Record:
        headers = {
            "Authorization": f"Bearer {self.config.mercado_pago_access_token}",
            "Content-Type": "application/json",
        }
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        try:
            async with self.session.request(
                method,
                f"{self.API_BASE}{path}",
                json=json,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                data = await response.json(content_type=None)
                if response.status >= 400:
                    message = (
                        data.get("message", "erro desconhecido") if isinstance(data, dict) else data
                    )
                    raise MercadoPagoError(
                        f"Mercado Pago respondeu HTTP {response.status}: {message}"
                    )
                if not isinstance(data, dict):
                    raise MercadoPagoError("resposta inesperada do Mercado Pago")
                return data
        except TimeoutError as exc:
            raise MercadoPagoError("tempo limite excedido ao consultar o Mercado Pago") from exc
        except aiohttp.ClientError as exc:
            raise MercadoPagoError("falha de comunicacao com o Mercado Pago") from exc


def pix_transaction_data(payment: Record) -> Record:
    point = payment.get("point_of_interaction") or {}
    transaction = point.get("transaction_data") or {}
    if not isinstance(transaction, dict):
        raise MercadoPagoError("pagamento criado sem dados do PIX")
    if not transaction.get("qr_code"):
        raise MercadoPagoError("pagamento criado sem codigo PIX")
    return transaction
