import hashlib
import hmac

from bot.utils.signature import (
    mercado_pago_manifest,
    parse_x_signature,
    validate_mercado_pago_signature,
)


def test_parse_signature_ignores_malformed_parts():
    assert parse_x_signature("ts=123,broken,v1=abc") == {"ts": "123", "v1": "abc"}


def test_valid_mercado_pago_signature():
    secret = "super-secret"
    data_id = "ABC123"
    request_id = "request-9"
    timestamp = "1720000000"
    digest = hmac.new(
        secret.encode(),
        mercado_pago_manifest(data_id, request_id, timestamp).encode(),
        hashlib.sha256,
    ).hexdigest()
    assert validate_mercado_pago_signature(
        secret=secret,
        data_id=data_id,
        request_id=request_id,
        signature_header=f"ts={timestamp},v1={digest}",
    )


def test_invalid_mercado_pago_signature():
    assert not validate_mercado_pago_signature(
        secret="secret",
        data_id="10",
        request_id="req",
        signature_header="ts=1,v1=invalid",
    )
