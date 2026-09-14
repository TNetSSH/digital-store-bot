from __future__ import annotations

import asyncio
import logging
import sys

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    ErrorEvent,
)

from bot.config import Config, ConfigurationError
from bot.database import Database
from bot.handlers import admin_router, payments_router, store_router
from bot.services.delivery import DeliveryService
from bot.services.mercadopago import MercadoPagoClient
from bot.services.payments import PaymentProcessor
from bot.web import start_web_server

logger = logging.getLogger(__name__)


async def _configure_commands(bot: Bot, config: Config) -> None:
    await bot.set_my_commands(
        [BotCommand(command="start", description="Abrir a loja")],
        scope=BotCommandScopeAllPrivateChats(),
    )
    for admin_id in config.admin_ids:
        try:
            await bot.set_my_commands(
                [
                    BotCommand(command="start", description="Abrir a loja"),
                    BotCommand(command="admin", description="Painel administrativo"),
                ],
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        except Exception:
            logger.info(
                "admin %s has not started the bot yet; command scope will be retried on restart",
                admin_id,
            )


async def _on_error(event: ErrorEvent) -> bool:
    exception = event.exception
    logger.error(
        "unhandled update error",
        exc_info=(type(exception), exception, exception.__traceback__),
    )
    return True


async def main() -> None:
    config = Config.from_env()
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        stream=sys.stdout,
    )

    db = Database(config.database_path)
    await db.open(
        stars_enabled=config.stars_default_enabled,
        pix_enabled=config.pix_default_enabled,
    )
    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
            link_preview_is_disabled=True,
        ),
    )
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_routers(admin_router, payments_router, store_router)
    dispatcher.errors.register(_on_error)

    web_runner = None
    async with aiohttp.ClientSession() as session:
        mercado_pago = MercadoPagoClient(config, session)
        delivery = DeliveryService(bot, db, config)
        processor = PaymentProcessor(
            bot=bot,
            db=db,
            config=config,
            mercado_pago=mercado_pago,
            delivery=delivery,
        )
        try:
            web_runner = await start_web_server(config, processor)
            await bot.delete_webhook(drop_pending_updates=False)
            await _configure_commands(bot, config)
            me = await bot.get_me()
            logger.info("Digital Store Bot started as @%s", me.username)
            await dispatcher.start_polling(
                bot,
                allowed_updates=dispatcher.resolve_used_update_types(),
                config=config,
                db=db,
                mercado_pago=mercado_pago,
                delivery=delivery,
                processor=processor,
            )
        finally:
            if web_runner is not None:
                await web_runner.cleanup()
            await db.close()
            await bot.session.close()


def run() -> None:
    try:
        asyncio.run(main())
    except (ConfigurationError, KeyboardInterrupt) as exc:
        if isinstance(exc, ConfigurationError):
            print(f"Erro de configuração: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
