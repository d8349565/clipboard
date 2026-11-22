"""Shared text-processing utilities for HTML / RTF clipboard content."""
from __future__ import annotations

import html as _html
import re

_RE_HTML_SCRIPT_STYLE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL
)
_RE_HTML_TAG = re.compile(r"<[^>]+>")
_RE_RTF_CTRL = re.compile(r"\\[a-zA-Z]+\d* ?|[{}]")
_RE_WS = re.compile(r"\s+")
_RTF_DESTINATIONS = {
    "annotation",
    "author",
    "colortbl",
    "comment",
    "creatim",
    "datastore",
    "filetbl",
    "fonttbl",
    "footer",
    "footerf",
    "footerl",
    "footerr",
    "generator",
    "header",
    "headerf",
    "headerl",
    "headerr",
    "info",
    "listoverridetable",
    "listtable",
    "nonshppict",
    "objdata",
    "object",
    "pict",
    "printim",
    "revtim",
    "rsidtbl",
    "shppict",
    "stylesheet",
    "themedata",
    "xmlnstbl",
}
_RTF_DESTINATIONS = {w.lower() for w in _RTF_DESTINATIONS}


def extract_html_fragment(raw: bytes) -> str:
    """Extract the HTML fragment from a CF_HTML (Windows 'HTML Format') blob.

    Returns the decoded fragment string, or ``""`` on failure.
    """
    try:
        header = raw[:4096].decode("ascii", errors="ignore")
        start_key = "StartFragment:"
        end_key = "EndFragment:"
        start_i = header.find(start_key)
        end_i = header.find(end_key)
        if start_i != -1 and end_i != -1:
            start_line = header[start_i : header.find("\n", start_i)].strip()
            end_line = header[end_i : header.find("\n", end_i)].strip()
            start = int(start_line.split(":", 1)[1].strip())
            end = int(end_line.split(":", 1)[1].strip())
            if 0 <= start < end <= len(raw):
                return raw[start:end].decode("utf-8", errors="ignore")
    except Exception:
        pass
    return ""


def html_to_plain_text(s: str, max_len: int = 400) -> str:
    """Convert an HTML string to plain text, stripping tags & scripts."""
    if not s:
        return ""
    s = _RE_HTML_SCRIPT_STYLE.sub(" ", s)
    s = _RE_HTML_TAG.sub(" ", s)
    # Handle truncated HTML like "<ul style=..." where no closing ">" exists.
    lt = s.rfind("<")
    gt = s.rfind(">")
    if lt > gt:
        s = s[:lt]
    s = _html.unescape(s)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = _RE_WS.sub(" ", s).strip()
    return s[:max_len]


def html_fragment_preview(raw: bytes, max_len: int = 400, *, empty: str = "(HTML)") -> str:
    """Return a plain-text preview extracted from a CF_HTML blob."""
    fragment = extract_html_fragment(raw)
    if fragment:
        return html_to_plain_text(fragment, max_len=max_len)
    try:
        s = raw.decode("utf-8", errors="ignore")
    except Exception:
        return empty
    plain = html_to_plain_text(s, max_len=max_len)
    return plain or empty


_RE_RTF_SPECIAL = re.compile(r"[{}\\]")


def _skip_rtf_group_body(s: str, i: int, n: int) -> int:
    """Fast-forward inside a skipped RTF destination until a '{' or '}' token.

    This avoids Python-level per-character iteration over megabytes of hex
    image data inside ``{\\pict ...}`` or ``{\\objdata ...}``.
    """
    depth = 0
    while i < n:
        match = _RE_RTF_SPECIAL.search(s, i)
        if match is None:
            return n
        pos = match.start()
        ch = s[pos]
        if ch == "\\":
            # Escaped brace or backslash does not affect group nesting.
            i = pos + 2
            continue
        if ch == "{":
            depth += 1
            i = pos + 1
            continue
        # ch == "}"
        if depth == 0:
            return pos
        depth -= 1
        i = pos + 1
    return n


def rtf_to_plain_text(raw: bytes, max_len: int = 400, *, empty: str = "(RTF)") -> str:
    """Convert raw RTF bytes to plain text, skipping formatting destinations."""
    try:
        s = raw.decode("latin-1", errors="ignore")
    except Exception:
        return empty
    out: list[str] = []
    visible_chars = 0

    def append_char(char: str) -> None:
        nonlocal visible_chars
        if not char:
            return
        if char.isspace():
            if not out:
                return
        else:
            visible_chars += 1
        out.append(char)

    stack: list[tuple[bool, int]] = []
    skip_group = False
    uc_skip = 1
    pending_ignorable = False
    skip_after_unicode = 0

    i = 0
    n = len(s)
    while i < n:
        # Visible characters survive whitespace cleanup, so this is a safe early exit.
        if max_len > 0 and visible_chars >= max_len:
            break
        if skip_group:
            i = _skip_rtf_group_body(s, i, n)
            if i >= n:
                break
            if stack:
                skip_group, uc_skip = stack.pop()
            else:
                skip_group = False
            pending_ignorable = False
            i += 1
            continue

        ch = s[i]

        if skip_after_unicode > 0:
            if ch in "\r\n":
                i += 1
                continue
            if ch == "\\" and i + 1 < n:
                nxt = s[i + 1]
                if nxt == "'" and i + 3 < n:
                    i += 4
                    skip_after_unicode -= 1
                    continue
                if nxt in "{}\\":
                    i += 2
                    skip_after_unicode -= 1
                    continue
            i += 1
            skip_after_unicode -= 1
            continue

        if ch in "\r\n":
            i += 1
            continue

        if ch == "{":
            stack.append((skip_group, uc_skip))
            pending_ignorable = False
            i += 1
            continue

        if ch == "}":
            if stack:
                skip_group, uc_skip = stack.pop()
            pending_ignorable = False
            i += 1
            continue

        if ch != "\\":
            pending_ignorable = False
            append_char(ch)
            i += 1
            continue

        i += 1
        if i >= n:
            break
        ctrl = s[i]

        if ctrl in "{}\\":
            append_char(ctrl)
            i += 1
            continue

        if ctrl == "*":
            pending_ignorable = True
            i += 1
            continue

        if ctrl == "'":
            if i + 2 < n:
                hex_code = s[i + 1 : i + 3]
                try:
                    b = int(hex_code, 16)
                    append_char(bytes((b,)).decode("cp1252", errors="ignore"))
                    i += 3
                    continue
                except Exception:
                    pass
            i += 1
            continue

        if ctrl.isalpha():
            start = i
            while i < n and s[i].isalpha():
                i += 1
            word = s[start:i].lower()

            sign = 1
            if i < n and s[i] in "+-":
                if s[i] == "-":
                    sign = -1
                i += 1
            num_start = i
            while i < n and s[i].isdigit():
                i += 1
            num: int | None = None
            if i > num_start:
                num = sign * int(s[num_start:i])

            if i < n and s[i] == " ":
                i += 1

            if pending_ignorable or word in _RTF_DESTINATIONS:
                skip_group = True
                pending_ignorable = False
                continue
            pending_ignorable = False

            if word in ("par", "line"):
                append_char("\n")
                continue
            if word == "tab":
                append_char("\t")
                continue
            if word == "uc" and num is not None:
                uc_skip = max(0, num)
                continue
            if word == "u" and num is not None:
                code = num if num >= 0 else num + 65536
                try:
                    append_char(chr(code))
                except Exception:
                    pass
                skip_after_unicode = max(0, uc_skip)
                continue
            continue

        pending_ignorable = False
        if ctrl == "~":
            append_char(" ")
        elif ctrl in ("-", "_"):
            append_char("-")
        else:
            append_char(ctrl)
        i += 1

    text = "".join(out).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    if not text:
        return empty
    return text[:max_len]
