"""Leitura do WAV de trabalho (mono, 16 kHz) e cortes por região.

Todos os adaptadores leem o mesmo arquivo preparado pela etapa de áudio, com ``soundfile``,
em vez de cada biblioteca decodificar por conta própria (torchaudio/torchcodec exigem as
bibliotecas compartilhadas do FFmpeg no Windows; o WAV já está no formato certo).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from ..base import TimeRange

SAMPLE_RATE = 16_000


def us_to_sample(us: int) -> int:
    return us * SAMPLE_RATE // 1_000_000


def sample_to_us(n: int) -> int:
    return n * 1_000_000 // SAMPLE_RATE


@lru_cache(maxsize=2)
def _load(path: str, mtime_ns: int, size: int):
    import soundfile as sf  # type: ignore

    samples, sr = sf.read(path, dtype="float32", always_2d=False)
    if sr != SAMPLE_RATE:
        raise ValueError(f"esperado WAV de {SAMPLE_RATE} Hz, recebido {sr} Hz: {path}")
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    samples.setflags(write=False)  # compartilhado entre adaptadores pelo cache
    return samples


def load_wav(path: Path):
    """Amostras float32 mono a 16 kHz (cache por caminho, data e tamanho)."""
    st = Path(path).stat()
    return _load(str(path), st.st_mtime_ns, st.st_size)


def cut(samples, region: TimeRange):
    return samples[us_to_sample(region.start_us) : us_to_sample(region.end_us)]


def quietest_point_us(samples, lo_us: int, hi_us: int, frame_us: int = 100_000) -> int:
    """Início do quadro de menor energia entre ``lo_us`` e ``hi_us`` (ponto de corte seguro)."""
    import numpy as np

    frame = max(us_to_sample(frame_us), 1)
    a, b = us_to_sample(lo_us), min(us_to_sample(hi_us), len(samples))
    if b - a < frame:
        return hi_us
    window = np.asarray(samples[a:b], dtype=np.float32)
    n = (len(window) // frame) * frame
    energy = (window[:n].reshape(-1, frame) ** 2).mean(axis=1)
    best = int(np.argmin(energy))
    return sample_to_us(a + best * frame + frame // 2)


def plan_segments(
    samples,
    regions: list[TimeRange],
    *,
    max_len_us: int,
    max_gap_us: int = 1_500_000,
    search_us: int = 15_000_000,
) -> list[TimeRange]:
    """Junta regiões de voz próximas em trechos de até ``max_len_us`` para o modelo.

    Pausas maiores que ``max_gap_us`` começam outro trecho (silêncio longo dentro do áudio
    enviado ao modelo favorece texto inventado). Uma região sozinha maior que o limite é
    cortada no ponto mais silencioso dos últimos ``search_us`` antes do limite.
    """
    pieces: list[TimeRange] = []
    for r in sorted(regions, key=lambda r: r.start_us):
        start = r.start_us
        while r.end_us - start > max_len_us:
            hi = start + max_len_us
            cut_at = quietest_point_us(samples, max(start + 1, hi - search_us), hi)
            cut_at = min(max(cut_at, start + 1), hi)
            pieces.append(TimeRange(start, cut_at))
            start = cut_at
        pieces.append(TimeRange(start, r.end_us))

    segments: list[TimeRange] = []
    for p in pieces:
        if segments:
            last = segments[-1]
            if p.start_us - last.end_us <= max_gap_us and p.end_us - last.start_us <= max_len_us:
                segments[-1] = TimeRange(last.start_us, p.end_us)
                continue
        segments.append(p)
    return segments
