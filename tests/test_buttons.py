import pytest

from bot.utils.buttons import button, normalize_style


def test_style_validation():
    assert normalize_style("primary") == "primary"
    assert normalize_style("default") is None
    assert normalize_style("pink") is None


def test_button_requires_exactly_one_action():
    with pytest.raises(ValueError):
        button("Inválido", callback_data="a", url="https://example.com")


def test_premium_emoji_and_style_are_serialized():
    result = button(
        "Comprar",
        callback_data="buy:1",
        style="success",
        emoji_id="5345952995991363418",
    )
    assert result.style == "success"
    assert result.icon_custom_emoji_id == "5345952995991363418"
