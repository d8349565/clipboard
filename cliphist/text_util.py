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
    """Convert raw RTF bytes to a rough plain-text preview."""
    try:
        s = raw.decode("latin-1", errors="ignore")
    except Exception:
        return "(RTF)"
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = _RE_RTF_CTRL.sub(" ", s)
    s = _RE_WS.sub(" ", s).strip()
    return s[:max_len] or "(RTF)"
