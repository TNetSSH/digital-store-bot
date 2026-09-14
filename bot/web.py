from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

from bot import __version__
from bot.config import Config
from bot.services.mercadopago import MercadoPagoError
from bot.services.payments import PaymentProcessor, PaymentValidationError
from bot.utils.signature import validate_mercado_pago_signature

logger = logging.getLogger(__name__)


def _payment_id(request: web.Request, body: dict[str, Any]) -> str:
    query_id = request.query.get("data.id", "")
    data = body.get("data") or {}
    body_id = data.get("id", "") if isinstance(data, dict) else ""
    return str(query_id or body_id)


def create_web_app(config: Config, processor: PaymentProcessor) -> web.Application:
    app = web.Application(client_max_size=1024 * 1024)

    async def health(_: web.Request) -> web.Response:
        return web.json_response(
            {"ok": True, "service": "digital-store-bot", "version": __version__}
        )

    async def mercado_pago_webhook(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        payment_id = _payment_id(request, body)
        valid = validate_mercado_pago_signature(
            secret=config.mercado_pago_webhook_secret,
            data_id=payment_id,
            request_id=request.headers.get("x-request-id", ""),
            signature_header=request.headers.get("x-signature", ""),
        )
        if not valid:
            logger.warning("rejected Mercado Pago webhook with invalid signature")
            return web.json_response({"ok": False, "error": "invalid signature"}, status=401)
        try:
            status = await processor.process_pix(payment_id)
        except PaymentValidationError:
            logger.exception("Mercado Pago webhook did not match a valid order")
            return web.json_response({"ok": True, "ignored": True})
        except MercadoPagoError:
            logger.exception("temporary error while processing Mercado Pago webhook")
            return web.json_response({"ok": False, "error": "temporary error"}, status=503)
        return web.json_response({"ok": True, "status": status})

    app.router.add_get("/health", health)
    app.router.add_post("/webhooks/mercadopago", mercado_pago_webhook)
    return app


async def start_web_server(config: Config, processor: PaymentProcessor) -> web.AppRunner:
    app = create_web_app(config, processor)
    runner = web.AppRunner(app, access_log=logger)
    await runner.setup()
    site = web.TCPSite(runner, config.web_host, config.web_port)
    await site.start()
    logger.info("web server listening on %s:%s", config.web_host, config.web_port)
    return runner
