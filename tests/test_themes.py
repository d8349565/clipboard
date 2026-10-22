from cliphist.themes import THEME_NAMES, normalize_theme, theme_colors


def _luminance(color: str) -> float:
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return sum(channel * weight for channel, weight in zip(linear, (0.2126, 0.7152, 0.0722)))


def test_theme_presets_keep_text_readable_and_colors_independent():
    for mode in ("light", "blue", "dark"):
        colors = theme_colors(mode)
        for foreground, background in (
            ("text", "surface"), ("muted", "surface"),
            ("text", "selection"), ("muted", "selection"),
            ("accent_text", "accent"),
        ):
            light, dark = sorted((_luminance(colors[foreground]), _luminance(colors[background])), reverse=True)
            assert (light + 0.05) / (dark + 0.05) >= 4.5, (mode, foreground, background)
        colors["surface"] = "#000000"
        assert theme_colors(mode)["surface"] != "#000000"


def test_unknown_or_old_theme_preferences_fall_back_to_system():
    for mode in THEME_NAMES:
        assert normalize_theme(mode) == mode
    for invalid in (None, "", "green", 3, ["dark"]):
        assert normalize_theme(invalid) == "system"
