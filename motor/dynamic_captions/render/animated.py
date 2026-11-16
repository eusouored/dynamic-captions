"""Legenda animada: blocos de ProRes 4444 com alfa, renderizados de forma incremental.

Cada bloco de ~45 s vira um arquivo cujo nome é o hash do seu conteúdo (ASS + quadro + taxa).
Depois de uma correção, só os blocos cujo conteúdo mudou são renderizados de novo, e o painel
troca na timeline só os clipes cujo nome mudou.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

from ..cancel import Cancelled
from ..domain.models import CaptionBlock, SequenceInfo, Transcript
from ..domain.timecode import FrameRate
from .ass import compile_ass
from .ffmpeg import alpha_render_cmd, concat_cmd, find_ffmpeg, frame_count, no_window, plan_chunks, render_codec
from .style import StyleLayers


@dataclass(frozen=True)
class RenderedClip:
    index: int
    key: str
    path: str
    start_us: int  # relativo ao início do trecho transcrito
    end_us: int
    reused: bool
    text: str = ""  # texto da legenda (vira o nome do clipe no projeto do Premiere)



def concat_quote(path: str) -> str:
    """Caminho entre aspas para a lista do concat do FFmpeg ('\\'' fecha, escapa e reabre a aspa)."""
    return "'" + path.replace("'", "'\\''") + "'"

def _workers() -> int:
    env = os.environ.get("DYNAMIC_CAPTIONS_RENDER_WORKERS")
    if env and env.isdigit():
        return max(1, int(env))
    return max(2, min(8, (os.cpu_count() or 4) // 2))


# Segmentos menores que isso não compensam um FFmpeg a mais.
MIN_SEGMENT_FRAMES = 48


def render_animated(
    blocks: list[CaptionBlock],
    transcript: Transcript,
    layers: StyleLayers,
    seq: SequenceInfo,
    out_dir: Path,
    *,
    progress: Callable[[str, float], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    ffmpeg: str | None = None,
    runner=subprocess.run,
    workers: int | None = None,
    codec: str | None = None,
    restore: Callable[[Path, str], bool] | None = None,
) -> list[RenderedClip]:
    """Renderiza os blocos que mudaram. Blocos e pedaços de blocos rodam em paralelo.

    Cada bloco ainda vira um único arquivo (mesmo nome por conteúdo): os pedaços são
    concatenados sem recodificar, então a troca de estilo custa ~1/N do tempo anterior.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rate = FrameRate(seq.fps_num, seq.fps_den)
    exe = ffmpeg or find_ffmpeg()
    n_workers = workers or _workers()
    codec = codec or render_codec()
    base = layers.base
    offset = (getattr(base, "offset_x_pct", 0.0), getattr(base, "offset_y_pct", 0.0))
    chunks = plan_chunks(blocks)
    clips: list[RenderedClip] = []
    todo: list[tuple[int, str, Path, int, int, Path]] = []
    for n, c in enumerate(chunks):
        if should_cancel and should_cancel():
            raise Cancelled("render")
        start = rate.frame_to_us(rate.us_to_frame(c.start_us))  # clipe começa num quadro inteiro
        end = max(c.end_us, start + rate.frame_to_us(1))
        sub = [b for b in blocks if b.id in set(c.block_ids)]
        ass = compile_ass(sub, transcript, layers, width=seq.width, height=seq.height, offset_us=start)
        move = f"|{offset[0]:g},{offset[1]:g}" if any(offset) else ""
        move += "" if codec == "prores" else f"|{codec}"  # trocar o formato gera arquivos novos
        key = hashlib.sha1(f"{ass}|{seq.width}x{seq.height}|{rate.num}/{rate.den}|{end - start}{move}".encode()).hexdigest()[:12]
        mov = out_dir / f"DC_{key}.mov"
        reused = (mov.exists() and mov.stat().st_size > 0) or bool(restore and restore(out_dir, mov.name))
        if not reused:
            ass_path = out_dir / f"DC_{key}.ass"
            ass_path.write_text(ass, encoding="utf-8")
            todo.append((n, key, mov, start, end, ass_path))
        text = " ".join(line.text for b in sub for line in b.lines)
        clips.append(RenderedClip(n, key, str(mov), start, end, reused, text))

    # Tarefas: (bloco, pedaço, quadro inicial, quadros) — pedaços só quando há folga de núcleos.
    tasks = []
    for n, key, mov, start, end, ass_path in todo:
        total = frame_count(end - start, rate)
        parts = max(1, min(n_workers, total // MIN_SEGMENT_FRAMES)) if len(todo) < n_workers else 1
        if parts == 1:
            tasks.append((n, key, mov, ass_path, end - start, 0, None, 0))
        else:
            step = -(-total // parts)
            for i, f0 in enumerate(range(0, total, step)):
                tasks.append((n, key, mov, ass_path, end - start, f0, min(step, total - f0), i))
    done = 0
    lock = threading.Lock()

    def run(task):
        nonlocal done
        n, key, mov, ass_path, dur, f0, frames, i = task
        if should_cancel and should_cancel():
            raise Cancelled("render")
        target = out_dir / (f"DC_{key}.parcial.mov" if frames is None else f"DC_{key}.p{i:02d}.mov")
        cmd = alpha_render_cmd(ass_path, target, width=seq.width, height=seq.height, rate=rate, duration_us=dur,
                               ffmpeg=exe, offset=offset, start_frame=f0, frames=frames, codec=codec)
        proc = runner(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **no_window())
        if proc.returncode != 0:
            target.unlink(missing_ok=True)
            tail = "\n".join((proc.stderr or "").strip().splitlines()[-3:])
            raise RuntimeError(f"falha ao renderizar o bloco {n + 1}: {tail}")
        with lock:
            done += 1
            if progress:
                progress("render", done / max(len(tasks), 1))
        return task, target

    results = []
    if tasks:
        pool = ThreadPoolExecutor(max_workers=min(n_workers, len(tasks)))
        futures = [pool.submit(run, t) for t in tasks]
        try:
            for f in futures:
                results.append(f.result())
        except BaseException:
            for f in futures:
                f.cancel()
            pool.shutdown(wait=True)
            for t in tasks:  # nada parcial fica com nome de bloco pronto
                n, key = t[0], t[1]
                for leftover in out_dir.glob(f"DC_{key}.p*.mov"):
                    leftover.unlink(missing_ok=True)
                (out_dir / f"DC_{key}.parcial.mov").unlink(missing_ok=True)
            raise
        pool.shutdown(wait=True)
    if should_cancel and should_cancel():  # cancelado durante o FFmpeg: descarta os parciais
        for _task, target in results:
            target.unlink(missing_ok=True)
        raise Cancelled("render")

    by_block: dict[str, list] = {}
    for task, target in results:
        by_block.setdefault(task[1], []).append((task[7], task[2], target))
    for key, parts in by_block.items():
        parts.sort()
        mov = parts[0][1]
        if len(parts) == 1:
            os.replace(parts[0][2], mov)
            continue
        listing = out_dir / f"DC_{key}.lista.txt"
        listing.write_text("".join(f"file {concat_quote(p[2].as_posix())}\n" for p in parts), encoding="utf-8")
        tmp = out_dir / f"DC_{key}.parcial.mov"
        proc = runner(concat_cmd(listing, tmp, exe), capture_output=True, text=True, encoding="utf-8", errors="replace", **no_window())
        for p in parts:
            p[2].unlink(missing_ok=True)
        listing.unlink(missing_ok=True)
        if proc.returncode != 0:
            tmp.unlink(missing_ok=True)
            raise RuntimeError("falha ao juntar os pedaços do bloco: " + "\n".join((proc.stderr or "").strip().splitlines()[-3:]))
        os.replace(tmp, mov)
    if progress and not tasks:
        progress("render", 1.0)
    return clips


def clips_as_dicts(clips: list[RenderedClip], offset_us: int) -> list[dict]:
    """Para o painel: tempos absolutos na sequência (início do trecho + posição no trecho)."""
    return [{**asdict(c), "seq_start_us": offset_us + c.start_us, "seq_end_us": offset_us + c.end_us} for c in clips]
