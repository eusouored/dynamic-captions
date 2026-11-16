"""IDs estáveis no formato ULID (26 caracteres, ordenáveis por tempo de criação)."""

from __future__ import annotations

import os
import threading
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32
_lock = threading.Lock()
_last_ms = -1
_last_rand = 0


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def new_id() -> str:
    """Gera um ULID monotônico: IDs criados no mesmo milissegundo continuam ordenados."""
    global _last_ms, _last_rand
    with _lock:
        now_ms = int(time.time() * 1000)
        if now_ms == _last_ms:
            _last_rand = (_last_rand + 1) & ((1 << 80) - 1)
        else:
            _last_ms = now_ms
            _last_rand = int.from_bytes(os.urandom(10), "big")
        return _encode(now_ms, 10) + _encode(_last_rand, 16)
