"""Normalização de texto em português para COMPARAR hipóteses (nunca para exibir).

Sem isso, diferenças de caixa, pontuação e grafia de números viram divergências falsas
na fila de revisão.
"""

from __future__ import annotations

import re
import unicodedata

_UNITS = {
    "0": "zero", "1": "um", "2": "dois", "3": "três", "4": "quatro", "5": "cinco",
    "6": "seis", "7": "sete", "8": "oito", "9": "nove", "10": "dez", "11": "onze",
    "12": "doze", "13": "treze", "14": "quatorze", "15": "quinze", "16": "dezesseis",
    "17": "dezessete", "18": "dezoito", "19": "dezenove", "20": "vinte",
}
# Variantes que significam a mesma palavra na comparação.
_EQUIV = {"catorze": "quatorze", "uma": "um", "duas": "dois", "ok": "okay"}
_TOKEN = re.compile(r"[\w]+(?:[-'][\w]+)*", re.UNICODE)


def normalize_token(token: str) -> str:
    t = unicodedata.normalize("NFC", token).lower()
    t = "".join(_TOKEN.findall(t))
    t = _UNITS.get(t, t)
    return _EQUIV.get(t, t)


def normalize_text(text: str) -> list[str]:
    """Separa e normaliza um texto inteiro em tokens comparáveis."""
    tokens = []
    for raw in unicodedata.normalize("NFC", text).lower().split():
        for part in _TOKEN.findall(raw):
            tokens.append(normalize_token(part))
    return [t for t in tokens if t]
