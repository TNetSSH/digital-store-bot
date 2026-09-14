from types import SimpleNamespace

import pytest

from bot.services.mercadopago import MercadoPagoClient


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
async def test_pix_expiration_uses_timezone_aware_utc_datetime():
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
    assert body["date_of_expiration"].endswith("+00:00")
