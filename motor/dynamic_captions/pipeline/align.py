"""Alinhamento de sequências de tokens (Levenshtein com reconstrução do caminho)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

EditOp = Literal["equal", "sub", "ins", "del"]


@dataclass(frozen=True)
class Edit:
    op: EditOp
    i: int | None  # índice em ``ref`` (None para inserção)
    j: int | None  # índice em ``hyp`` (None para deleção)


def align_tokens(ref: Sequence[str], hyp: Sequence[str]) -> list[Edit]:
    """Caminho de edição mínimo de ``ref`` para ``hyp``.

    Custo O(n·m); use em janelas (dezenas a centenas de palavras), nunca no arquivo inteiro.
    """
    n, m = len(ref), len(hyp)
    dist = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dist[i][0] = i
    for j in range(1, m + 1):
        dist[0][j] = j
    for i in range(1, n + 1):
        ri = ref[i - 1]
        row, prev = dist[i], dist[i - 1]
        for j in range(1, m + 1):
            cost = 0 if ri == hyp[j - 1] else 1
            row[j] = min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + cost)
    edits: list[Edit] = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and dist[i][j] == dist[i - 1][j - 1] + (0 if ref[i - 1] == hyp[j - 1] else 1):
            edits.append(Edit("equal" if ref[i - 1] == hyp[j - 1] else "sub", i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i > 0 and dist[i][j] == dist[i - 1][j] + 1:
            edits.append(Edit("del", i - 1, None))
            i -= 1
        else:
            edits.append(Edit("ins", None, j - 1))
            j -= 1
    edits.reverse()
    return edits


def word_error_rate(ref: Sequence[str], hyp: Sequence[str]) -> float:
    if not ref:
        return 0.0 if not hyp else 1.0
    errors = sum(1 for e in align_tokens(ref, hyp) if e.op != "equal")
    return errors / len(ref)
