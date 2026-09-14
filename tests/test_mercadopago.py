from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from bot.services.mercadopago import MercadoPagoClient, format_mercado_pago_datetime


class FakeResponse:
    status = 201

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def json(self, *, content_type=None):
        return {"id": 123}


class FakeSession:
    def __init__(self):
        self.request_data = None

    def request(self, method, url, **kwargs):
        self.request_data = (method, url, kwargs)
        return FakeResponse()


@pytest.mark.asyncio
async def test_pix_expiration_uses_mercado_pago_format():
    config = SimpleNamespace(
        mercado_pago_ready=True,
        mercado_pago_missing=(),
        mercado_pago_access_token="token",
        mercado_pago_payer_email="loja@example.com",
        public_base_url="https://bot.example.com",
        pix_expiration_minutes=30,
    )
    session = FakeSession()
    client = MercadoPagoClient(config, session)

    await client.create_pix_payment(
        order={"amount": 1990, "public_id": "pedido-1", "telegram_user_id": 42},
        product_description="Produto",
        payer_first_name="Cliente",
    )

    body = session.request_data[2]["json"]
    expiration = body["date_of_expiration"]
    assert expiration.endswith(".000-03:00")
    assert datetime.fromisoformat(expiration).tzinfo is not None


def test_mercado_pago_datetime_converts_utc_and_includes_milliseconds():
    value = datetime(2026, 9, 14, 22, 13, 8, tzinfo=timezone.utc)

    assert format_mercado_pago_datetime(value) == "2026-09-14T19:13:08.000-03:00"


def test_mercado_pago_datetime_rejects_naive_values():
    with pytest.raises(ValueError, match="timezone"):
        format_mercado_pago_datetime(datetime(2026, 9, 14, 22, 13, 8))
