from __future__ import annotations

import logging

log = logging.getLogger(__name__)

import win32clipboard
import win32con

from .clipboard_util import open_clipboard
from .models import ClipboardItem, MAX_IMAGE_BYTES, MAX_RICH_RAW_BYTES
from .text_util import html_fragment_preview as _extract_html_fragment_preview
from .text_util import rtf_to_plain_text as _rtf_preview


HTML_FORMAT_NAME = "HTML Format"
RTF_FORMAT_NAME = "Rich Text Format"


class ClipboardPayloadError(ValueError):
    """Raised before changing the clipboard when an item cannot be written."""


def _validate_item(item: ClipboardItem) -> None:
    """Validate payload sizes and required blobs before EmptyClipboard()."""
    if item.item_type not in {"text", "files", "image", "html", "rtf"}:
        raise ClipboardPayloadError(f"unsupported item_type: {item.item_type}")

    if item.image_bytes is not None and len(item.image_bytes) > MAX_IMAGE_BYTES:
        raise ClipboardPayloadError(
            f"辅助图片负载超过上限（{len(item.image_bytes)} bytes，限制 {MAX_IMAGE_BYTES} bytes）"
        )

    if item.item_type == "image":
        if not item.raw_bytes:
            raise ClipboardPayloadError("图片内容不可用，无法写入剪贴板")
        if len(item.raw_bytes) > MAX_IMAGE_BYTES:
            raise ClipboardPayloadError(
                f"图片负载超过上限（{len(item.raw_bytes)} bytes，限制 {MAX_IMAGE_BYTES} bytes）"
            )
    elif item.item_type in {"html", "rtf"} and item.raw_bytes is not None:
        if len(item.raw_bytes) > MAX_RICH_RAW_BYTES:
            raise ClipboardPayloadError(
                f"富文本负载超过上限（{len(item.raw_bytes)} bytes，限制 {MAX_RICH_RAW_BYTES} bytes）"
            )


def _set_image_if_any(item: ClipboardItem) -> None:
    if not item.image_bytes:
        return
    win32clipboard.SetClipboardData(win32con.CF_DIB, item.image_bytes)


def set_clipboard_item(item: ClipboardItem, hwnd: int | None = None) -> None:
    # Validate before opening and, especially, before EmptyClipboard(). A
    # failed lazy blob load must not erase the user's current clipboard.
    _validate_item(item)

    plain_text: str | None = None
    if item.item_type == "html":
        plain_text = item.text or ""
        if item.raw_bytes:
            plain_text = _extract_html_fragment_preview(item.raw_bytes, max_len=12000) or plain_text
    elif item.item_type == "rtf":
        plain_text = item.text or ""
        if item.raw_bytes:
            plain_text = _rtf_preview(item.raw_bytes, max_len=12000) or plain_text
    rich_format: int | None = None
    if item.item_type == "html":
        rich_format = win32clipboard.RegisterClipboardFormat(HTML_FORMAT_NAME)
    elif item.item_type == "rtf":
        rich_format = win32clipboard.RegisterClipboardFormat(RTF_FORMAT_NAME)
    if item.item_type in {"html", "rtf"} and not rich_format:
        raise ClipboardPayloadError("无法注册剪贴板富文本格式")

    with open_clipboard(hwnd):
        win32clipboard.EmptyClipboard()

        if item.item_type == "text":
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, item.text or "")
            _set_image_if_any(item)
            return

        if item.item_type == "files":
            win32clipboard.SetClipboardData(win32con.CF_HDROP, list(item.file_paths or ()))
            return

        if item.item_type == "image":
            raw_bytes = item.raw_bytes
            if raw_bytes is None:  # guarded by _validate_item; keep API defensive
                raise ClipboardPayloadError("图片内容不可用，无法写入剪贴板")
            win32clipboard.SetClipboardData(win32con.CF_DIB, raw_bytes)
            return

        if item.item_type == "html":
            # Put a plain-text fallback first so a later format failure still
            # leaves the clipboard usable.
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, plain_text or "")
            if item.raw_bytes:
                assert rich_format is not None
                win32clipboard.SetClipboardData(rich_format, item.raw_bytes)
            _set_image_if_any(item)
            return

        if item.item_type == "rtf":
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, plain_text or "")
            if item.raw_bytes:
                assert rich_format is not None
                win32clipboard.SetClipboardData(rich_format, item.raw_bytes)
            _set_image_if_any(item)
            return
