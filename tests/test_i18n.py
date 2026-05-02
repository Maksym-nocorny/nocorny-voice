from i18n import DEFAULT_LANG, TRANSLATIONS, get_text


def test_known_language_returns_localized_string():
    assert get_text("uk", "downloading") == "Завантаження медіа..."


def test_unknown_language_falls_back_to_english():
    assert get_text("xx", "downloading") == TRANSLATIONS["en"]["downloading"]


def test_none_language_falls_back():
    assert get_text(None, "welcome") == TRANSLATIONS["en"]["welcome"]


def test_format_arguments():
    out = get_text("en", "media_too_long", 30)
    assert "30" in out
    assert "minutes" in out


def test_format_in_other_language():
    out = get_text("uk", "media_too_long", 30)
    assert "30" in out
    assert "хвилин" in out


def test_html_label_format():
    out = get_text("en", "transcription_label", "hello")
    assert out == "<b>Transcription:</b>\n\nhello"


def test_no_stats_keys_remaining():
    """Analytics keys were removed; verify they don't exist anywhere."""
    for lang_dict in TRANSLATIONS.values():
        stats_keys = [k for k in lang_dict if k.startswith("stats_")]
        assert stats_keys == [], f"unexpected stats keys: {stats_keys}"


def test_all_languages_have_required_keys():
    required = set(TRANSLATIONS[DEFAULT_LANG].keys())
    for lang, d in TRANSLATIONS.items():
        missing = required - set(d.keys())
        assert not missing, f"{lang} missing keys: {missing}"
