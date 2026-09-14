import pytest

from bot.config import Config, ConfigurationError


def test_minimal_config(monkeypatch, tmp_path):
    monkeypatch.setenv("BOT_TOKEN", "123:test")
    monkeypatch.setenv("ADMIN_IDS", "10, 20")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "store.db"))
    monkeypatch.delenv("MERCADO_PAGO_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("MERCADO_PAGO_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("MERCADO_PAGO_PAYER_EMAIL", raising=False)
    config = Config.from_env()

    assert config.admin_ids == frozenset({10, 20})
    assert not config.mercado_pago_ready


def test_admin_is_required(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123:test")
    monkeypatch.setenv("ADMIN_IDS", "")
    with pytest.raises(ConfigurationError):
        Config.from_env()
