import pytest

from bot.utils.money import (
    InvalidMoney,
    external_amount_to_cents,
    format_brl,
    parse_brl_to_cents,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("19,90", 1990),
        ("R$ 1.234,56", 123456),
        ("10", 1000),
        ("0", 0),
        ("10.50", 1050),
    ],
)
def test_parse_brl(raw, expected):
    assert parse_brl_to_cents(raw) == expected


def test_invalid_negative_money():
    with pytest.raises(InvalidMoney):
        parse_brl_to_cents("-1")


def test_external_amount_is_compared_in_cents():
    assert external_amount_to_cents("29.9") == 2990


def test_format_brl():
    assert format_brl(123456) == "R$ 1.234,56"
