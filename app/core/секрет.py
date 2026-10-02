"""Секреты в настройках: шифруем средствами Windows (DPAPI).

Токен бота Telegram — это ключ от бота: кто его прочитал, тот читает
переписку с ботом и пишет от его имени. Открытым текстом в settings.json
его держать нельзя: этот файл копируют при переносе, прикладывают к
жалобам, синхронизируют облаком.

DPAPI шифрует ключом учётной записи Windows: расшифровать может только
тот же человек на том же компьютере. Ни пароля, ни своей криптографии.
Вне Windows шифровать нечем: храним как есть и честно помечаем приставкой.
"""
from __future__ import annotations

import base64
import sys

ПРИСТАВКА_DPAPI = "dpapi:"
ПРИСТАВКА_ОТКРЫТО = "plain:"


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _CRYPTPROTECT_UI_FORBIDDEN = 0x01

    def _в_blob(данные: bytes) -> tuple[_BLOB, ctypes.Array]:
        буфер = ctypes.create_string_buffer(данные, len(данные))
        return _BLOB(len(данные), ctypes.cast(буфер, ctypes.POINTER(ctypes.c_char))), буфер

    def _из_blob(blob: _BLOB) -> bytes:
        try:
            return ctypes.string_at(blob.pbData, blob.cbData)
        finally:
            _kernel32.LocalFree(blob.pbData)

    def _зашифровать(данные: bytes) -> bytes:
        вход, _держим = _в_blob(данные)
        выход = _BLOB()
        if not _crypt32.CryptProtectData(ctypes.byref(вход), None, None, None, None,
                                          _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(выход)):
            raise OSError(ctypes.get_last_error(), "CryptProtectData")
        return _из_blob(выход)

    def _расшифровать(данные: bytes) -> bytes:
        вход, _держим = _в_blob(данные)
        выход = _BLOB()
        if not _crypt32.CryptUnprotectData(ctypes.byref(вход), None, None, None, None,
                                            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(выход)):
            raise OSError(ctypes.get_last_error(), "CryptUnprotectData")
        return _из_blob(выход)


def спрятать(текст: str) -> str:
    """Секрет для записи в настройки. Пустой — пустая строка."""
    if not текст:
        return ""
    данные = текст.encode("utf-8")
    if sys.platform == "win32":
        return ПРИСТАВКА_DPAPI + base64.b64encode(_зашифровать(данные)).decode("ascii")
    return ПРИСТАВКА_ОТКРЫТО + base64.b64encode(данные).decode("ascii")


def достать(запись: str) -> str:
    """Секрет из настроек. Не расшифровался (чужой компьютер, порча) — пусто."""
    if not запись:
        return ""
    try:
        if запись.startswith(ПРИСТАВКА_DPAPI) and sys.platform == "win32":
            return _расшифровать(base64.b64decode(запись[len(ПРИСТАВКА_DPAPI):])).decode("utf-8")
        if запись.startswith(ПРИСТАВКА_ОТКРЫТО):
            return base64.b64decode(запись[len(ПРИСТАВКА_ОТКРЫТО):]).decode("utf-8")
    except (OSError, ValueError, UnicodeDecodeError):
        return ""
    return ""
