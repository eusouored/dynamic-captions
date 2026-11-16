"""Atribuição de falante a cada palavra.

Duas fontes, em ordem de preferência:
1. Energia por faixa de microfone (uma faixa por pessoa) — mais precisa que qualquer modelo.
2. Turnos do diarizador (pyannote Community-1 em modo exclusivo).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from ..domain.models import DEFAULT_SPEAKER_COLORS, Flag, Speaker, SpeakerSource, Word
from ..engines.base import SpeakerTurn


def _overlap(a0: int, a1: int, b0: int, b1: int) -> int:
    return max(0, min(a1, b1) - max(a0, b0))


def speakers_from_turns(turns: Sequence[SpeakerTurn]) -> tuple[list[Speaker], dict[str, str]]:
    """Cria falantes na ordem em que aparecem ("Falante 1", "Falante 2"...)."""
    mapping: dict[str, str] = {}
    speakers: list[Speaker] = []
    for t in sorted(turns, key=lambda t: t.start_us):
        if t.speaker not in mapping:
            n = len(mapping) + 1
            sid = f"spk_{n}"
            mapping[t.speaker] = sid
            speakers.append(
                Speaker(id=sid, label=f"Falante {n}", color=DEFAULT_SPEAKER_COLORS[(n - 1) % len(DEFAULT_SPEAKER_COLORS)])
            )
    return speakers, mapping


def assign_from_turns(
    words: Sequence[Word],
    turns: Sequence[SpeakerTurn],
    mapping: Mapping[str, str],
    *,
    max_gap_us: int = 1_000_000,
    overlap_ratio: float = 0.3,
) -> list[Word]:
    """Falante com maior sobreposição; sem sobreposição, o turno mais próximo até ``max_gap_us``.

    Se os turnos se sobrepõem (saída não exclusiva) e o segundo colocado cobre ao menos
    ``overlap_ratio`` da palavra, a palavra recebe a marca de fala sobreposta.
    """
    ordered = sorted(turns, key=lambda t: t.start_us)
    out: list[Word] = []
    for w in words:
        scores: dict[str, int] = {}
        for t in ordered:
            if t.start_us > w.end_us:
                break
            # Alinhadores podem dar duração zero a artigos/interjeições. Esse ponto
            # continua dentro de um turno de voz; não é uma palavra sem falante.
            ov = _overlap(w.start_us, max(w.end_us, w.start_us + 1), t.start_us, t.end_us)
            if ov:
                scores[t.speaker] = scores.get(t.speaker, 0) + ov
        flags = set(w.flags)
        if scores:
            ranked = sorted(scores.items(), key=lambda kv: -kv[1])
            label = ranked[0][0]
            if len(ranked) > 1 and ranked[1][1] >= overlap_ratio * max(w.duration_us, 1):
                flags.add(Flag.OVERLAP)
        else:
            near = min(
                ordered,
                key=lambda t: min(abs(t.start_us - w.mid_us), abs(t.end_us - w.mid_us)),
                default=None,
            )
            if near is None or min(abs(near.start_us - w.mid_us), abs(near.end_us - w.mid_us)) > max_gap_us:
                out.append(w)
                continue
            label = near.speaker
        out.append(w.model_copy(update={"speaker_id": mapping[label], "flags": frozenset(flags)}))
    return out


def assign_from_tracks(
    words: Sequence[Word],
    envelopes: Mapping[str, Sequence[float]],
    *,
    hop_us: int = 10_000,
    margin_db: float = 6.0,
) -> tuple[list[Word], list[Speaker]]:
    """Atribui pela faixa com mais energia (RMS por janela de ``hop_us``).

    ``envelopes`` mapeia o nome da faixa (ex.: "A1") para valores RMS lineares.
    Se a faixa vencedora não supera a segunda por ``margin_db``, marca fala sobreposta
    (ou vazamento) para revisão.
    """
    names = list(envelopes)
    speakers = [
        Speaker(
            id=f"spk_{i + 1}",
            label=f"Falante {i + 1}",
            display_name=None,
            color=DEFAULT_SPEAKER_COLORS[i % len(DEFAULT_SPEAKER_COLORS)],
            source=SpeakerSource.TRACK,
        )
        for i in range(len(names))
    ]
    track_to_id = {name: sp.id for name, sp in zip(names, speakers)}
    out: list[Word] = []
    for w in words:
        a, b = w.start_us // hop_us, max(w.end_us // hop_us, w.start_us // hop_us + 1)
        energy = {}
        for name in names:
            env = envelopes[name]
            seg = env[a:b] or [0.0]
            energy[name] = math.sqrt(sum(x * x for x in seg) / len(seg))
        ranked = sorted(energy.items(), key=lambda kv: -kv[1])
        best, best_e = ranked[0]
        flags = set(w.flags)
        if len(ranked) > 1:
            second_e = ranked[1][1]
            if best_e <= 0 or 20 * math.log10(best_e / max(second_e, 1e-12)) < margin_db:
                flags.add(Flag.OVERLAP)
        out.append(w.model_copy(update={"speaker_id": track_to_id[best], "flags": frozenset(flags)}))
    return out, speakers
