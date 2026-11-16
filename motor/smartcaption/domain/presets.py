"""Presets de leitura: como as palavras viram blocos de legenda."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ReadingPreset(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    max_chars_per_line: int = Field(42, ge=8)
    max_lines: int = Field(2, ge=1, le=3)
    max_words: int | None = None  # limite de palavras por bloco (estilo redes sociais)
    max_cps: float = 17.0  # caracteres por segundo
    min_duration_us: int = 833_334  # 5/6 s
    max_duration_us: int = 7_000_000
    pause_split_us: int = 700_000  # pausa que sempre fecha o bloco
    min_gap_us: int = 0  # folga mínima entre blocos ao estender a duração
    chain_gap_us: int = 500_000  # pausas menores que isto são fechadas (evita a legenda piscar)
    split_on_speaker_change: bool = True
    prefer_punctuation: bool = True


# Guia Netflix para português: 42 caracteres por linha, 17 caracteres/s, mínimo de 5/6 s.
LEGENDA_TRADICIONAL = ReadingPreset(
    id="legenda_tradicional",
    name="Legenda tradicional",
    max_chars_per_line=42,
    max_lines=2,
    max_cps=17.0,
    min_gap_us=83_000,  # ~2 quadros a 24 fps
)

# Redes sociais: uma linha curta, poucas palavras, destaque karaokê.
REDES_SOCIAIS = ReadingPreset(
    id="redes_sociais",
    name="Redes sociais",
    max_chars_per_line=22,
    max_lines=1,
    max_words=4,
    max_cps=25.0,
    min_duration_us=300_000,
    max_duration_us=3_000_000,
    pause_split_us=400_000,
    chain_gap_us=400_000,
)

PRESETS: dict[str, ReadingPreset] = {p.id: p for p in (LEGENDA_TRADICIONAL, REDES_SOCIAIS)}


def get_preset(preset_id: str) -> ReadingPreset:
    try:
        return PRESETS[preset_id]
    except KeyError as exc:
        raise KeyError(f"preset desconhecido: {preset_id}") from exc
