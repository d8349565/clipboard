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


def html_fragment_preview(raw: bytes, max_len: int = 400) -> str:
    """Return a plain-text preview extracted from a CF_HTML blob."""
    fragment = extract_html_fragment(raw)
    if fragment:
        return html_to_plain_text(fragment, max_len=max_len)
    try:
        s = raw.decode("utf-8", errors="ignore")
    except Exception:
        return "(HTML)"
    plain = html_to_plain_text(s, max_len=max_len)
    return plain or "(HTML)"


def rtf_to_plain_text(raw: bytes, max_len: int = 400) -> str:
    """Convert raw RTF bytes to plain text, skipping formatting destinations."""
    try:
        s = raw.decode("latin-1", errors="ignore")
    except Exception:
        return "(RTF)"
    out: list[str] = []
    stack: list[tuple[bool, int]] = []
    skip_group = False
    uc_skip = 1
    pending_ignorable = False
    skip_after_unicode = 0

    i = 0
    n = len(s)
    while i < n:
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
            if not skip_group:
                out.append(ch)
            i += 1
            continue

        i += 1
        if i >= n:
            break
        ctrl = s[i]

        if ctrl in "{}\\":
            if not skip_group:
                out.append(ctrl)
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
                    if not skip_group:
                        out.append(bytes((b,)).decode("cp1252", errors="ignore"))
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
                if not skip_group:
                    out.append("\n")
                continue
            if word == "tab":
                if not skip_group:
                    out.append("\t")
                continue
            if word == "uc" and num is not None:
                uc_skip = max(0, num)
                continue
            if word == "u" and num is not None and not skip_group:
                code = num if num >= 0 else num + 65536
                try:
                    out.append(chr(code))
                except Exception:
                    pass
                skip_after_unicode = max(0, uc_skip)
                continue
            continue

        pending_ignorable = False
        if not skip_group:
            if ctrl == "~":
                out.append(" ")
            elif ctrl in ("-", "_"):
                out.append("-")
            else:
                out.append(ctrl)
        i += 1

    text = "".join(out).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    if not text:
        return "(RTF)"
    return text[:max_len]
