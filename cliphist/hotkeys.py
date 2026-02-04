from __future__ import annotations

from dataclasses import dataclass

import win32con


@dataclass(frozen=True, slots=True)
class HotkeySpec:
    display: str
    modifiers: int
    vk: int


_KEY_ALIASES: dict[str, str] = {
    "CONTROL": "CTRL",
    "LCTRL": "CTRL",
    "RCTRL": "CTRL",
    "L_CONTROL": "CTRL",
    "R_CONTROL": "CTRL",
    "ALTGR": "ALT",
    "OPTION": "ALT",
    "RETURN": "ENTER",
    "ESCAPE": "ESC",
    "PGUP": "PAGEUP",
    "PGDN": "PAGEDOWN",
    "PGDOWN": "PAGEDOWN",
    "DEL": "DELETE",
    "INS": "INSERT",
    "PRIOR": "PAGEUP",
    "NEXT": "PAGEDOWN",
}


_VK_MAP: dict[str, int] = {
    "BACKSPACE": win32con.VK_BACK,
    "TAB": win32con.VK_TAB,
    "ENTER": win32con.VK_RETURN,
    "ESC": win32con.VK_ESCAPE,
    "SPACE": win32con.VK_SPACE,
    "LEFT": win32con.VK_LEFT,
    "RIGHT": win32con.VK_RIGHT,
    "UP": win32con.VK_UP,
    "DOWN": win32con.VK_DOWN,
    "HOME": win32con.VK_HOME,
    "END": win32con.VK_END,
    "PAGEUP": win32con.VK_PRIOR,
    "PAGEDOWN": win32con.VK_NEXT,
    "INSERT": win32con.VK_INSERT,
    "DELETE": win32con.VK_DELETE,
}

for i in range(1, 25):
    _VK_MAP[f"F{i}"] = getattr(win32con, f"VK_F{i}")


def parse_hotkey_sequence(seq: str) -> HotkeySpec | None:
    if not seq:
        return None

    seq = seq.strip()
    for sep in (",", "，", "、"):
        if sep in seq:
            seq = seq.split(sep, 1)[0].strip()
            break
    seq = seq.replace("＋", "+").replace("﹢", "+")
    parts = [p.strip() for p in seq.replace(" ", "").split("+") if p.strip()]
    if not parts:
        return None

    modifiers = 0
    key_part: str | None = None

    for raw in parts:
        token = raw.upper()
        token = _KEY_ALIASES.get(token, token)

        if token in ("CTRL",):
            modifiers |= win32con.MOD_CONTROL
            continue
        if token in ("SHIFT",):
            modifiers |= win32con.MOD_SHIFT
            continue
        if token in ("ALT",):
            modifiers |= win32con.MOD_ALT
            continue
        if token in ("WIN", "META", "CMD", "COMMAND"):
            modifiers |= win32con.MOD_WIN
            continue

        if key_part is None:
            key_part = token
        else:
            return None

    if key_part is None:
        return None

    vk = _vk_from_key(key_part)
    if vk is None:
        return None

    display = format_hotkey_display(modifiers, vk)
    return HotkeySpec(display=display, modifiers=modifiers, vk=vk)


def _vk_from_key(key: str) -> int | None:
    if key in _VK_MAP:
        return _VK_MAP[key]

    if len(key) == 1:
        ch = key.upper()
        if "A" <= ch <= "Z":
            return ord(ch)
        if "0" <= ch <= "9":
            return ord(ch)
    return None


def format_hotkey_display(modifiers: int, vk: int) -> str:
    parts: list[str] = []
    if modifiers & win32con.MOD_CONTROL:
        parts.append("Ctrl")
    if modifiers & win32con.MOD_SHIFT:
        parts.append("Shift")
    if modifiers & win32con.MOD_ALT:
        parts.append("Alt")
    if modifiers & win32con.MOD_WIN:
        parts.append("Win")

    key = _key_name_from_vk(vk)
    parts.append(key)
    return "+".join(parts)


def _key_name_from_vk(vk: int) -> str:
    if ord("A") <= vk <= ord("Z"):
        return chr(vk)
    if ord("0") <= vk <= ord("9"):
        return chr(vk)
    for k, v in _VK_MAP.items():
        if v == vk:
            if k == "VK_RETURN":
                return "Enter"
            return k.title().replace("Pageup", "PageUp").replace("Pagedown", "PageDown")
    return hex(vk)
