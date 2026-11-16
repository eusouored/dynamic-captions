"""Instante da prévia exata em que a legenda já está totalmente visível."""

from __future__ import annotations

from pathlib import Path

from ..domain.models import CaptionBlock
from ..domain.timecode import FrameRate
from .ffmpeg import escape_filter_path, translate_filter
from .style import StyleSpec


def preview_cmd(ass_path: Path, png: Path, *, t_us: int, frame_width: int,
                frame_height: int, output_width: int, ffmpeg: str,
                background: str | None = None, offset: tuple[float, float] = (0.0, 0.0)) -> list[str]:
    """Desenha só um quadro no instante pedido, mesmo no fim de sequências longas.

    A libass recebe o PTS absoluto em microssegundos. Não é necessário gerar todos
    os quadros anteriores para chegar ao instante da legenda.
    """
    if background and Path(background).is_file():
        src = ["-i", background]
        pre = f"scale={frame_width}:{frame_height},"
    else:
        src = ["-f", "lavfi", "-i", f"color=c=0x2f363d:s={frame_width}x{frame_height}:r=1:d=1"]
        pre = ""
    sec = t_us / 1_000_000
    move = translate_filter(frame_width, frame_height, *offset)
    if move:
        # Legenda deslocada: desenha numa camada transparente, desloca e sobrepõe ao quadro.
        layer = f"color=c=black@0.0:s={frame_width}x{frame_height}:r=1:d=1,format=rgba"
        graph = (f"[0:v]{pre}format=rgba[bg];"
                 f"[1:v]settb=AVTB,setpts=PTS+{sec:.6f}/TB,ass={escape_filter_path(ass_path)}:alpha=1,"
                 f"setpts=PTS-STARTPTS,{move}[fg];"
                 f"[bg]setpts=PTS-STARTPTS[b0];[b0][fg]overlay=0:0:format=auto,scale={output_width}:-2")
        return [ffmpeg, "-v", "error", "-y", "-nostdin", *src, "-f", "lavfi", "-i", layer,
                "-filter_complex", graph, "-frames:v", "1", str(png)]
    vf = (f"{pre}settb=AVTB,setpts=PTS+{sec:.6f}/TB,"
          f"ass={escape_filter_path(ass_path)},scale={output_width}:-2,setpts=PTS-STARTPTS")
    return [ffmpeg, "-v", "error", "-y", "-nostdin", *src, "-vf", vf,
            "-frames:v", "1", str(png)]


def visible_time_us(blocks: list[CaptionBlock], t_us: int, spec: StyleSpec, rate: FrameRate) -> int:
    """Leva ``t_us`` para depois da animação de entrada (e antes da de saída) do bloco que o contém.

    No primeiro quadro de uma palavra que abre o bloco, um fade/pop/slide ainda está no começo
    e a libass desenha o texto transparente. Fora de qualquer bloco, devolve ``t_us``.
    """
    block = next((b for b in blocks if b.start_us <= t_us < b.end_us), None)
    if block is None or spec.animation_in == "none":
        return t_us
    frame = rate.frame_to_us(1)
    anim = spec.animation_ms * 1000
    lo = block.start_us + anim + frame
    hi = block.end_us - (anim if spec.animation_in == "fade" else 0) - frame
    if hi <= lo:  # bloco curto demais para as duas animações: o meio do bloco
        return (block.start_us + block.end_us) // 2
    return min(max(t_us, lo), hi)
