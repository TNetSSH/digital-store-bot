from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


class InvalidMoney(ValueError):
    pass


def parse_brl_to_cents(value: str) -> int:
    normalized = value.strip().lower().replace("r$", "").replace(" ", "")
    if not normalized:
        raise InvalidMoney("valor vazio")
    if "," in normalized:
        normalized = normalized.replace(".", "").replace(",", ".")
    try:
        amount = Decimal(normalized).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise InvalidMoney("valor invalido") from exc
    if amount < 0:
        raise InvalidMoney("o valor nao pode ser negativo")
    return int(amount * 100)


def external_amount_to_cents(value: object) -> int:
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise InvalidMoney("valor externo invalido") from exc
    return int(amount * 100)


def format_brl(cents: int) -> str:
    if cents < 0:
        raise InvalidMoney("o valor nao pode ser negativo")
    whole, fraction = divmod(cents, 100)
    return f"R$ {whole:,}".replace(",", ".") + f",{fraction:02d}"


def cents_to_api_amount(cents: int) -> float:
    if cents < 0:
        raise InvalidMoney("o valor nao pode ser negativo")
    return float(Decimal(cents) / Decimal(100))
