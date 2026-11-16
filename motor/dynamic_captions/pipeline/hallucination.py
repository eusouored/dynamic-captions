"""Sinais de alucinação do ASR. Nada é apagado aqui: as palavras vão para a fila de revisão."""

from __future__ import annotations

from collections.abc import Sequence

from ..domain.models import Flag, Word
from ..engines.base import TimeRange
from .normalize import normalize_token

# Frases que modelos treinados em vídeos da web costumam gerar em silêncio ou música.
# Só são marcadas quando também caem fora da voz detectada ou têm confiança baixa.
CREDIT_PHRASES = (
    "legendas pela comunidade amara.org",
    "obrigado por assistir",
    "inscreva-se no canal",
    "legendado por",
)


def _mark(w: Word) -> Word:
    return w.model_copy(update={"flags": frozenset(set(w.flags) | {Flag.HALLUCINATION_SUSPECT})})


def flag_outside_speech(words: Sequence[Word], regions: Sequence[TimeRange], pad_us: int = 200_000) -> list[Word]:
    # Sem regiões, nenhuma palavra está dentro de fala detectada.
    return [w if any(r.contains(w.mid_us, pad_us) for r in regions) else _mark(w) for w in words]


def flag_repetition_loops(words: Sequence[Word], max_ngram: int = 4, min_repeats: int = 3) -> list[Word]:
    """Marca n-gramas repetidos em sequência (ex.: "sim sim sim sim" ou frases em laço)."""
    tokens = [normalize_token(w.text) for w in words]
    marked: set[int] = set()
    n_words = len(tokens)
    for n in range(1, max_ngram + 1):
        i = 0
        while i + n <= n_words:
            gram = tokens[i : i + n]
            if not all(gram):
                i += 1
                continue
            reps = 1
            while i + (reps + 1) * n <= n_words and tokens[i + reps * n : i + (reps + 1) * n] == gram:
                reps += 1
            needed = min_repeats + (1 if n == 1 else 0)  # repetir uma palavra 3x é comum na fala
            if reps >= needed:
                marked.update(range(i + n, i + reps * n))  # mantém a primeira ocorrência limpa
                i += reps * n
            else:
                i += 1
    return [_mark(w) if k in marked else w for k, w in enumerate(words)]


def flag_credit_phrases(
    words: Sequence[Word], regions: Sequence[TimeRange], *, low_confidence: float = 0.5
) -> list[Word]:
    out = list(words)
    toks = [normalize_token(w.text) for w in out]
    for phrase in CREDIT_PHRASES:
        target = [normalize_token(t) for t in phrase.split()]  # mesma tokenização das palavras
        n = len(target)
        for i in range(len(toks) - n + 1):
            if toks[i : i + n] != target:
                continue
            span = out[i : i + n]
            outside = not any(r.contains(w.mid_us, 200_000) for w in span for r in regions)
            weak = any(w.confidence is not None and w.confidence < low_confidence for w in span)
            if outside or weak:
                for k in range(i, i + n):
                    out[k] = _mark(out[k])
    return out


def apply_all(words: Sequence[Word], regions: Sequence[TimeRange]) -> list[Word]:
    out = flag_outside_speech(words, regions)
    out = flag_repetition_loops(out)
    return flag_credit_phrases(out, regions)
