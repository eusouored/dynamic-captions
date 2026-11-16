"""Informações da máquina que o painel não consegue obter sozinho.

- Presets de exportação de áudio WAV do Premiere/Media Encoder (``.epr``), usados para o
  Premiere exportar só o áudio mixado da sequência editada.
- Fontes instaladas (nome de família), as mesmas que a libass encontra ao renderizar.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

FONT_EXT = {".ttf", ".otf", ".ttc"}


def _adobe_roots() -> list[Path]:
    roots: list[Path] = []
    for env in ("PROGRAMFILES", "PROGRAMW6432", "PROGRAMFILES(X86)"):
        base = os.environ.get(env)
        if base:
            roots.append(Path(base) / "Adobe")
    if sys.platform == "darwin":
        roots.append(Path("/Applications"))
    docs = Path.home() / "Documents" / "Adobe"
    roots.append(docs)
    extra = os.environ.get("SMARTCAPTION_ADOBE_DIR")
    if extra:
        roots.insert(0, Path(extra))
    return [r for r in dict.fromkeys(roots) if r.exists()]


def _is_wav_preset(p: Path) -> bool:
    n = p.name.lower()
    return p.suffix.lower() == ".epr" and ("wav" in n or "waveform" in n)


def find_audio_presets(limit: int = 20) -> list[str]:
    """Presets WAV (cache por processo: varrer a pasta da Adobe leva segundos)."""
    return list(_find_audio_presets_cached(limit, os.environ.get("SMARTCAPTION_AUDIO_PRESET", "")))


@lru_cache(maxsize=4)
def _find_audio_presets_cached(limit: int, _env_key: str) -> tuple[str, ...]:
    return tuple(_scan_audio_presets(limit))


def _scan_audio_presets(limit: int = 20) -> list[str]:
    """Presets WAV encontrados, os de 48 kHz/16 bits primeiro. SMARTCAPTION_AUDIO_PRESET tem prioridade."""
    found: list[Path] = []
    env = os.environ.get("SMARTCAPTION_AUDIO_PRESET")
    if env and Path(env).exists():
        found.append(Path(env))
    for root in _adobe_roots():
        try:
            for p in root.rglob("*.epr"):
                if _is_wav_preset(p):
                    found.append(p)
                    if len(found) > 400:
                        break
        except OSError:
            continue

    def score(p: Path) -> tuple:
        n = p.name.lower()
        return (str(p) != env, "48" not in n, "16" not in n, len(n))

    unique = list(dict.fromkeys(found))
    return [str(p) for p in sorted(unique, key=score)[:limit]]


def _font_dirs() -> list[Path]:
    dirs: list[Path] = []
    if sys.platform == "win32":
        dirs.append(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    elif sys.platform == "darwin":
        dirs += [Path("/System/Library/Fonts"), Path("/Library/Fonts"), Path.home() / "Library" / "Fonts"]
    else:
        dirs += [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".fonts", Path.home() / ".local/share/fonts"]
    return [d for d in dirs if d.exists()]


def _family_names(path: Path) -> list[str]:
    try:
        from fontTools.ttLib import TTCollection, TTFont  # type: ignore
    except ImportError:
        return [path.stem.replace("-", " ").replace("_", " ")]
    try:
        fonts = TTCollection(str(path), lazy=True).fonts if path.suffix.lower() == ".ttc" else [TTFont(str(path), lazy=True)]
    except Exception:
        return []
    names = []
    for f in fonts:
        try:
            name = f["name"]
            fam = name.getDebugName(16) or name.getDebugName(1)  # família tipográfica, senão a legada
            if fam:
                names.append(fam)
        except Exception:
            continue
    return names


@lru_cache(maxsize=1)
def list_fonts() -> tuple[str, ...]:
    families: set[str] = set()
    for d in _font_dirs():
        try:
            files = [p for p in d.rglob("*") if p.suffix.lower() in FONT_EXT]
        except OSError:
            continue
        for p in files:
            families.update(n for n in _family_names(p) if n and not n.startswith("."))
    return tuple(sorted(families, key=str.lower))
