"""Etapa de áudio: qualquer mídia → WAV de trabalho (mono, 16 kHz, PCM 16 bits).

Um WAV que já está nesse formato é usado como está. O resto é convertido pelo FFmpeg para
uma pasta de cache, com nome derivado do caminho, tamanho e data do arquivo (reprocessar a
mesma mídia não converte de novo).
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import wave
from collections.abc import Callable
from pathlib import Path

from ..render.ffmpeg import extract_audio_cmd, find_ffmpeg, no_window


def is_work_wav(path: Path) -> bool:
    try:
        with wave.open(str(path), "rb") as w:
            return (
                w.getnchannels() == 1 and w.getframerate() == 16_000 and w.getsampwidth() == 2
                and w.getcomptype() == "NONE"
            )
    except (wave.Error, EOFError, OSError):
        return False


def prepare_audio(
    src: Path,
    cache_dir: Path,
    *,
    audio_stream: int | None = None,
    ffmpeg: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> Path:
    src = Path(src)
    if not src.exists():
        raise FileNotFoundError(f"mídia não encontrada: {src}")
    if audio_stream is None and is_work_wav(src):
        return src
    st = src.stat()
    key = hashlib.sha1(f"{src.resolve()}|{st.st_size}|{st.st_mtime_ns}|{audio_stream}".encode()).hexdigest()[:16]
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    dst = cache_dir / f"{key}.wav"
    if is_work_wav(dst):
        return dst
    tmp = cache_dir / f"{key}.parcial.wav"
    cmd = extract_audio_cmd(src, tmp, audio_stream=audio_stream, ffmpeg=ffmpeg or find_ffmpeg())
    proc = runner(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **no_window())
    if proc.returncode != 0 or not is_work_wav(tmp):
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-4:])
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"o FFmpeg não conseguiu extrair o áudio de {src.name}: {tail}")
    os.replace(tmp, dst)
    return dst
