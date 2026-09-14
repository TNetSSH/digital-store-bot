from bot.utils.text import (
    admin_html,
    normalize_legacy_emoji,
    render_user_template,
    shorten,
    strip_html,
)


def test_template_escapes_user_values():
    rendered = render_user_template(
        "Olá, {NAME} ({USERNAME}) #{ID}",
        name="<Tiago>",
        username="t&net",
        user_id=123,
    )
    assert rendered == "Olá, &lt;Tiago&gt; (@t&amp;net) #123"


def test_shorten_respects_limit():
    shortened = shorten("um texto bem longo", 10)
    assert len(shortened) <= 10
    assert shortened.endswith("…")
    assert shorten("curto", 10) == "curto"


def test_legacy_premium_emoji_markup():
    source = "<emoji id='5345952995991363418'>👋</> <b>Olá!</b>"
    assert normalize_legacy_emoji(source) == (
        '<tg-emoji emoji-id="5345952995991363418">👋</tg-emoji> <b>Olá!</b>'
    )


def test_admin_html_keeps_telegram_entities_when_no_raw_markup():
    assert admin_html("Olá", "<b>Olá</b>") == "<b>Olá</b>"


def test_strip_html_keeps_premium_emoji_fallback():
    value = "<emoji id='123'>👋</> <b>Curso completo</b>"
    assert strip_html(value) == "👋 Curso completo"
