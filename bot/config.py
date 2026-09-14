from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigurationError(RuntimeError):
    """Raised when a required environment setting is absent or malformed."""


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "sim", "on"}


def _admin_ids(value: str) -> frozenset[int]:
    try:
        ids = frozenset(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise ConfigurationError("ADMIN_IDS deve conter apenas IDs numericos") from exc
    if not ids:
        raise ConfigurationError("Informe ao menos um ID em ADMIN_IDS")
    return ids


@dataclass(frozen=True, slots=True)
class Config:
    bot_token: str
    admin_ids: frozenset[int]
    database_path: Path
    log_level: str
    web_host: str
    web_port: int
    public_base_url: str
    stars_default_enabled: bool
    pix_default_enabled: bool
    mercado_pago_access_token: str
    mercado_pago_webhook_secret: str
    mercado_pago_payer_email: str
    pix_expiration_minutes: int

    @classmethod
    def from_env(cls) -> Config:
        load_dotenv()
        bot_token = os.getenv("BOT_TOKEN", "").strip()
        if not bot_token:
            raise ConfigurationError("BOT_TOKEN nao foi configurado")

        try:
            web_port = int(os.getenv("WEB_PORT", "8081"))
            expiration = int(os.getenv("PIX_EXPIRATION_MINUTES", "30"))
        except ValueError as exc:
            raise ConfigurationError(
                "WEB_PORT e PIX_EXPIRATION_MINUTES devem ser numeros inteiros"
            ) from exc
        if not 1 <= web_port <= 65535:
            raise ConfigurationError("WEB_PORT deve estar entre 1 e 65535")
        if not 5 <= expiration <= 1440:
            raise ConfigurationError("PIX_EXPIRATION_MINUTES deve estar entre 5 e 1440")

        return cls(
            bot_token=bot_token,
            admin_ids=_admin_ids(os.getenv("ADMIN_IDS", "")),
            database_path=Path(os.getenv("DATABASE_PATH", "./data/store.db")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            web_host=os.getenv("WEB_HOST", "127.0.0.1"),
            web_port=web_port,
            public_base_url=os.getenv("PUBLIC_BASE_URL", "").rstrip("/"),
            stars_default_enabled=_as_bool(os.getenv("STARS_ENABLED"), True),
            pix_default_enabled=_as_bool(os.getenv("PIX_ENABLED"), False),
            mercado_pago_access_token=os.getenv("MERCADO_PAGO_ACCESS_TOKEN", "").strip(),
            mercado_pago_webhook_secret=os.getenv("MERCADO_PAGO_WEBHOOK_SECRET", "").strip(),
            mercado_pago_payer_email=os.getenv("MERCADO_PAGO_PAYER_EMAIL", "").strip(),
            pix_expiration_minutes=expiration,
        )

    @property
    def mercado_pago_ready(self) -> bool:
        return all(
            (
                self.public_base_url,
                self.mercado_pago_access_token,
                self.mercado_pago_webhook_secret,
                self.mercado_pago_payer_email,
            )
        )

    @property
    def mercado_pago_missing(self) -> tuple[str, ...]:
        values = {
            "PUBLIC_BASE_URL": self.public_base_url,
            "MERCADO_PAGO_ACCESS_TOKEN": self.mercado_pago_access_token,
            "MERCADO_PAGO_WEBHOOK_SECRET": self.mercado_pago_webhook_secret,
            "MERCADO_PAGO_PAYER_EMAIL": self.mercado_pago_payer_email,
        }
        return tuple(key for key, value in values.items() if not value)
