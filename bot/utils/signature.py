from __future__ import annotations

import hashlib
import hmac


def parse_x_signature(header: str) -> dict[str, str]:
    parts: dict[str, str] = {}
    for component in header.split(","):
        key, separator, value = component.strip().partition("=")
        if separator and key and value:
            parts[key] = value
    return parts


def mercado_pago_manifest(data_id: str, request_id: str, timestamp: str) -> str:
    return f"id:{data_id.lower()};request-id:{request_id};ts:{timestamp};"


def validate_mercado_pago_signature(
    *, secret: str, data_id: str, request_id: str, signature_header: str
) -> bool:
    if not all((secret, data_id, request_id, signature_header)):
        return False
    signature = parse_x_signature(signature_header)
    timestamp = signature.get("ts", "")
    received = signature.get("v1", "")
    if not timestamp or not received:
        return False
    manifest = mercado_pago_manifest(data_id, request_id, timestamp)
    expected = hmac.new(secret.encode(), manifest.encode(), digestmod=hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)
