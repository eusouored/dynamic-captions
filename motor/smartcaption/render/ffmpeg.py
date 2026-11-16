"""Comandos FFmpeg: extração de áudio e renderização com canal alfa.

As funções montam os comandos; quem executa é a etapa de áudio ou o worker de renderização.
O executável vem de ``find_ffmpeg()`` (variável SMARTCAPTION_FFMPEG, PATH ou imageio-ffmpeg).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePath

from ..domain.models import CaptionBlock
from ..domain.timecode import FrameRate


def _escape_option_value(value: str) -> str:
    """Nível 1 do FFmpeg: valor de opção dentro da descrição de um filtro."""
    return "".join("\\" + ch if ch in "\\':" else ch for ch in value)


def _escape_filtergraph(value: str) -> str:
    """Nível 2 do FFmpeg: a descrição do filtro dentro do filtergraph."""
    return "".join("\\" + ch if ch in "\\'[],;" else ch for ch in value)


def escape_filter_path(path: str | PurePath) -> str:
    r"""Caminho como argumento de filtro em ``-vf``, sem shell no meio.

    Aplica os dois níveis de escape do FFmpeg, então apóstrofo, dois-pontos (letra de unidade
    no Windows), vírgula, colchetes e ponto e vírgula chegam intactos à libass.
    Exemplo: ``C:\Vídeos\pasta d'água\a.ass`` vira ``C\\:/Vídeos/pasta d\\\'água/a.ass``.
    """
    p = str(path).replace("\\", "/")  # o FFmpeg aceita barras normais no Windows
    return _escape_filtergraph(_escape_option_value(p))


def no_window() -> dict:
    """Argumentos de subprocess que impedem a janela preta de console no Windows.

    O motor roda com pythonw (sem console); sem CREATE_NO_WINDOW cada FFmpeg ou nvidia-smi
    abre um console vazio na frente do editor. Em outros sistemas não muda nada.
    """
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}


def find_ffmpeg(required: bool = True) -> str | None:
    """Caminho do FFmpeg: SMARTCAPTION_FFMPEG, depois o PATH, depois o pacote imageio-ffmpeg."""
    env = os.environ.get("SMARTCAPTION_FFMPEG")
    if env:
        return env
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    if required:
        raise FileNotFoundError(
            "FFmpeg não encontrado. Instale (winget install Gyan.FFmpeg), ou pip install imageio-ffmpeg, "
            "ou defina SMARTCAPTION_FFMPEG com o caminho do executável."
        )
    return None


def extract_audio_cmd(
    src: str | Path, dst: str | Path, *, audio_stream: int | None = None, ffmpeg: str = "ffmpeg"
) -> list[str]:
    """WAV mono 16 kHz para VAD, ASR e diarização. ``audio_stream`` escolhe uma faixa específica."""
    cmd = [ffmpeg, "-y", "-hide_banner", "-nostdin", "-i", str(src)]
    if audio_stream is not None:
        cmd += ["-map", f"0:a:{audio_stream}"]
    return cmd + ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst)]


def translate_filter(width: int, height: int, dx_pct: float, dy_pct: float) -> str:
    """Desloca a camada transparente (legenda arrastada na prévia); vazio quando não há deslocamento."""
    dx, dy = round(width * dx_pct / 100), round(height * dy_pct / 100)
    dx = max(-(width - 2), min(width - 2, dx))
    dy = max(-(height - 2), min(height - 2, dy))
    if not dx and not dy:
        return ""
    cw, ch = width - abs(dx), height - abs(dy)
    cx, cy = (0 if dx > 0 else -dx), (0 if dy > 0 else -dy)
    px, py = (dx if dx > 0 else 0), (dy if dy > 0 else 0)
    return f"crop={cw}:{ch}:{cx}:{cy},pad={width}:{height}:{px}:{py}:color=black@0.0"


def frame_count(duration_us: int, rate: FrameRate) -> int:
    """Quadros que a fonte ``color`` gera para ``duration_us`` (n com n/fps < duração)."""
    num = max(duration_us, 1) * rate.num
    den = rate.den * 1_000_000
    return max(1, -(-num // den))


def alpha_render_cmd(
    ass_path: str | Path,
    out_path: str | Path,
    *,
    width: int,
    height: int,
    rate: FrameRate,
    duration_us: int,
    fonts_dir: str | Path | None = None,
    ffmpeg: str = "ffmpeg",
    offset: tuple[float, float] = (0.0, 0.0),
    start_frame: int = 0,
    frames: int | None = None,
    codec: str = "prores",
) -> list[str]:
    """Desenha o ASS sobre fundo transparente e grava ProRes 4444 com alfa (lido pelo Premiere).

    ``start_frame``/``frames`` renderizam só um segmento do bloco (renderização em paralelo):
    o PTS avança ``start_frame`` quadros antes da libass e volta a zero depois, então os
    segmentos concatenados são idênticos, quadro a quadro, ao bloco renderizado de uma vez.
    """
    seconds = max(duration_us, 1) / 1_000_000
    if frames is not None:
        seconds = (frames + 1) * rate.den / rate.num
    source = f"color=c=black@0.0:s={width}x{height}:r={rate.num}/{rate.den}:d={seconds:.6f},format=rgba"
    vf = f"ass={escape_filter_path(ass_path)}:alpha=1"
    if fonts_dir is not None:
        vf += f":fontsdir={escape_filter_path(fonts_dir)}"
    if start_frame:
        vf = f"setpts=PTS+{int(start_frame)},{vf},setpts=PTS-STARTPTS"
    move = translate_filter(width, height, *offset)
    if move:
        vf += "," + move
    out = ["-frames:v", str(int(frames))] if frames is not None else []
    return [
        ffmpeg, "-y", "-hide_banner", "-nostdin",
        "-f", "lavfi", "-i", source,
        "-vf", vf, *out,
        *CODECS[codec],
        str(out_path),
    ]


# Todos com alfa, em .mov. ProRes 4444 é o padrão validado no Premiere; PNG/Animation codificam
# 15-30x mais rápido (o ProRes é o gargalo ao trocar de estilo) e são decodificados pelo Premiere.
CODECS = {
    "prores": ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", "-vendor", "apl0"],
    "png": ["-c:v", "png", "-compression_level", "1", "-pix_fmt", "rgba"],
    "qtrle": ["-c:v", "qtrle", "-pix_fmt", "argb"],
}


DEFAULT_CODEC = "qtrle"  # 0.2.0: ~18x mais rápido que ProRes 4444 em 4K, menor e com alfa direto no Premiere 26.5


def render_codec() -> str:
    """Formato dos clipes de legenda. SMARTCAPTION_CODEC=prores volta ao ProRes 4444."""
    c = os.environ.get("SMARTCAPTION_CODEC", DEFAULT_CODEC).lower()
    return c if c in CODECS else DEFAULT_CODEC


def concat_cmd(list_path: str | Path, out_path: str | Path, ffmpeg: str = "ffmpeg") -> list[str]:
    """Junta segmentos (cada um começa num quadro-chave) sem recodificar."""
    return [ffmpeg, "-y", "-hide_banner", "-nostdin", "-f", "concat", "-safe", "0", "-i", str(list_path),
            "-c", "copy", str(out_path)]


@dataclass(frozen=True)
class RenderChunk:
    index: int
    start_us: int
    end_us: int
    block_ids: tuple[str, ...]


def plan_chunks(
    blocks: Sequence[CaptionBlock],
    *,
    target_us: int = 45_000_000,
    min_split_gap_us: int = 300_000,
    hard_max_us: int = 60_000_000,
) -> list[RenderChunk]:
    """Agrupa blocos em trechos de ~``target_us``, cortando em pausas.

    Trechos curtos permitem renderizar de novo só o que mudou e trocar um clipe na timeline.
    """
    chunks: list[RenderChunk] = []
    cur: list[CaptionBlock] = []

    def close() -> None:
        if cur:
            chunks.append(RenderChunk(len(chunks), cur[0].start_us, cur[-1].end_us, tuple(b.id for b in cur)))
            cur.clear()

    for b in blocks:
        if cur:
            span = b.end_us - cur[0].start_us
            gap = b.start_us - cur[-1].end_us
            if (span > target_us and gap >= min_split_gap_us) or span > hard_max_us:
                close()
        cur.append(b)
    close()
    return chunks
