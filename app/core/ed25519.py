"""Подпись Ed25519 на чистом питоне, по RFC 8032.

Нужна ровно для одного: проверить ключ лицензии без интернета. Ключ
подписывает сервер закрытым ключом, программа сверяет подпись открытым.

Почему своё, а не библиотека. `cryptography` или `PyNaCl` тянут в
установщик несколько мегабайт двоичных модулей ради одной проверки
при запуске, а каждый новый двоичный модуль у нас это ещё и повод для
очередной ложной тревоги антивируса. Проверка подписи здесь занимает
несколько миллисекунд, а сама арифметика дословно следует эталонной
реализации из RFC 8032, раздел 6. Правильность держат проверки
`лицензия_test.py`: эталонные векторы из RFC и сверка с Go, на котором
написан сервер, выдающий ключи.

Защиты от подсмотра по времени здесь нет и не нужно: закрытого ключа
в программе нет, проверяется только открытое.
"""

from __future__ import annotations

import hashlib

_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _sha512_int(data: bytes) -> int:
    return int.from_bytes(hashlib.sha512(data).digest(), "little")


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _I % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_GY = 4 * pow(5, _P - 2, _P) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)
_ZERO = (0, 1, 1, 0)


def _add(a, b):
    # Сложение в расширенных координатах, формулы из RFC 8032, 5.1.4.
    k1 = (a[1] - a[0]) * (b[1] - b[0]) % _P
    k2 = (a[1] + a[0]) * (b[1] + b[0]) % _P
    k3 = 2 * a[3] * b[3] * _D % _P
    k4 = 2 * a[2] * b[2] % _P
    e, f, g, h = k2 - k1, k4 - k3, k4 + k3, k2 + k1
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(s: int, point):
    q = _ZERO
    while s > 0:
        if s & 1:
            q = _add(q, point)
        point = _add(point, point)
        s >>= 1
    return q


def _equal(a, b) -> bool:
    if (a[0] * b[2] - b[0] * a[2]) % _P != 0:
        return False
    return (a[1] * b[2] - b[1] * a[2]) % _P == 0


def _compress(point) -> bytes:
    zinv = pow(point[2], _P - 2, _P)
    x = point[0] * zinv % _P
    y = point[1] * zinv % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(data: bytes):
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _expand(seed: bytes) -> tuple[int, bytes]:
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(seed: bytes) -> bytes:
    """Открытый ключ по 32 байтам закрытого."""
    if len(seed) != 32:
        raise ValueError("закрытый ключ Ed25519 — ровно 32 байта")
    a, _ = _expand(seed)
    return _compress(_mul(a, _G))


def sign(seed: bytes, message: bytes) -> bytes:
    """Подписать сообщение. В программе не нужно: для выдачи ключей и проверок."""
    if len(seed) != 32:
        raise ValueError("закрытый ключ Ed25519 — ровно 32 байта")
    a, prefix = _expand(seed)
    pub = _compress(_mul(a, _G))
    r = _sha512_int(prefix + message) % _L
    big_r = _compress(_mul(r, _G))
    h = _sha512_int(big_r + pub + message) % _L
    s = (r + h * a) % _L
    return big_r + int.to_bytes(s, 32, "little")


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    """Сходится ли подпись. Любой мусор на входе означает «нет», а не падение."""
    if len(public) != 32 or len(signature) != 64:
        return False
    point_a = _decompress(public)
    if point_a is None:
        return False
    r_bytes = signature[:32]
    point_r = _decompress(r_bytes)
    if point_r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    # Без этой проверки подпись можно размножить: s и s + L дают одно и то же.
    if s >= _L:
        return False
    h = _sha512_int(r_bytes + public + message) % _L
    return _equal(_mul(s, _G), _add(point_r, _mul(h, point_a)))
