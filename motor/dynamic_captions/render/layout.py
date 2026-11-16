"""Ajusta o limite de caracteres por linha ao quadro e à fonte do estilo.

O preset de leitura define um teto (ex.: 42 caracteres), mas num vídeo vertical com fonte
grande cabem bem menos. Esta estimativa usa a largura média de caractere por tipo de texto;
a versão final medirá com as métricas reais da fonte (fontTools) antes de renderizar.
"""

from __future__ import annotations

from ..domain.presets import ReadingPreset
from .style import StyleSpec

USABLE_WIDTH = 0.90  # margens laterais de 5% (ver render/ass.py)


def _avg_char_em(style: StyleSpec) -> float:
    if style.uppercase:
        return 0.68 if style.bold else 0.62
    return 0.56 if style.bold else 0.50


def fit_chars_per_line(style: StyleSpec, width: int, height: int) -> int:
    overshoot = 1.25 if style.animation_in == "zoom" else 1.12 if style.animation_in == "bounce" else 1
    font_px = height * style.font_size_pct / 100 * overshoot
    return max(8, int(width * USABLE_WIDTH / (font_px * _avg_char_em(style))))


def preset_for_frame(preset: ReadingPreset, style: StyleSpec, width: int, height: int) -> ReadingPreset:
    fit = fit_chars_per_line(style, width, height)
    if fit >= preset.max_chars_per_line:
        return preset
    return preset.model_copy(update={"max_chars_per_line": fit})
