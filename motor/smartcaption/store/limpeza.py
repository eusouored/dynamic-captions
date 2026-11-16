"""Limpeza de arquivos do Dynamic Captions sem deixar mídia offline no Premiere.

- Prévias (``previews/``) e quadros de fundo (``cache/quadro_*.jpg``) nunca entram no Premiere:
  saem sozinhos quando ficam velhos.
- Clipes renderizados (``renders/<projeto>/SC_<código>.mov``) só saem depois que o painel
  confirma que nenhuma sequência do projeto aberto os usa. Primeiro vão para
  ``descartados/<projeto>/`` e ficam lá ``KEEP_DAYS`` dias (um Auto-Save antigo do Premiere
  ainda pode apontar para eles); um render que volta a precisar do mesmo arquivo o recupera.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

KEEP_DAYS = 7
CACHE_MAX_AGE_S = 24 * 3600
_COMPANIONS = (".mov", ".ass")


def trash_dir(workspace: Path) -> Path:
    return Path(workspace) / "descartados"


def _size(paths) -> int:
    total = 0
    for p in paths:
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return total


def _files(folder: Path) -> list[Path]:
    return [p for p in folder.rglob("*") if p.is_file()] if folder.is_dir() else []


def _render_file(workspace: Path, raw: str) -> Path | None:
    """Só aceita SC_*.mov dentro de renders/<projeto>/ (nada fora da pasta do motor)."""
    try:
        p = Path(raw).resolve()
        root = (Path(workspace) / "renders").resolve()
        rel = p.relative_to(root)
    except (OSError, ValueError):
        return None
    if len(rel.parts) != 2 or not p.name.startswith("SC_") or p.suffix.lower() != ".mov":
        return None
    return p


def discard(workspace: Path, paths: list[str]) -> list[str]:
    """Move clipes que saíram da timeline para descartados/ (recuperáveis por KEEP_DAYS dias)."""
    moved = []
    now = time.time()
    for raw in paths:
        mov = _render_file(workspace, raw)
        if mov is None or not mov.exists():
            continue
        dest = trash_dir(workspace) / mov.parent.name
        dest.mkdir(parents=True, exist_ok=True)
        for ext in _COMPANIONS:
            src = mov.with_suffix(ext)
            if src.exists():
                target = dest / src.name
                try:
                    os.replace(src, target)
                    os.utime(target, (now, now))  # o prazo conta a partir do descarte
                except OSError:
                    continue  # em uso (o Premiere ainda segura o arquivo): fica onde está
        if not mov.exists():
            moved.append(str(mov))
    return moved


def restore(out_dir: Path, name: str, workspace: Path) -> bool:
    """Se um render precisa de um clipe descartado (mesmo conteúdo), traz de volta em vez de renderizar."""
    src = trash_dir(workspace) / Path(out_dir).name / name
    if not src.is_file() or src.stat().st_size == 0:
        return False
    try:
        os.replace(src, Path(out_dir) / name)
        ass = src.with_suffix(".ass")
        if ass.exists():
            os.replace(ass, Path(out_dir) / ass.name)
    except OSError:
        return False
    return True


def prune(workspace: Path, *, now: float | None = None) -> dict[str, int]:
    """Limpeza automática: descartados com mais de KEEP_DAYS dias, prévias e quadros velhos."""
    now = time.time() if now is None else now
    removed = {"descartados": 0, "previas": 0, "quadros": 0}
    for p in _files(trash_dir(workspace)):
        if now - p.stat().st_mtime > KEEP_DAYS * 86400:
            p.unlink(missing_ok=True)
            removed["descartados"] += 1
    for p in _files(Path(workspace) / "previews"):
        if now - p.stat().st_mtime > CACHE_MAX_AGE_S:
            p.unlink(missing_ok=True)
            removed["previas"] += 1
    cache = Path(workspace).parent / "cache"
    for p in cache.glob("quadro_*.jpg") if cache.is_dir() else []:
        if now - p.stat().st_mtime > CACHE_MAX_AGE_S:
            p.unlink(missing_ok=True)
            removed["quadros"] += 1
    return removed


def in_use(projects) -> set[str]:
    """Clipes que algum projeto diz ter colocado na timeline (manifesto salvo)."""
    used: set[str] = set()
    for pf in projects:
        applied = pf.meta().get("applied") or {}
        for c in applied.get("clips") or []:
            if c.get("path"):
                try:
                    used.add(str(Path(c["path"]).resolve()).lower())
                except OSError:
                    pass
    return used


def report(workspace: Path, projects) -> dict:
    """Quanto cada tipo de arquivo ocupa; ``unused`` são renders fora de todo manifesto."""
    used = in_use(projects)
    renders = sorted((Path(workspace) / "renders").glob("*/SC_*.mov"))
    unused = [p for p in renders if str(p.resolve()).lower() not in used]
    cache = Path(workspace).parent / "cache"
    quadros = list(cache.glob("quadro_*.jpg")) if cache.is_dir() else []
    return {
        "renders_bytes": _size(renders) + _size(p.with_suffix(".ass") for p in renders),
        "unused": {"count": len(unused), "bytes": _size(unused), "paths": [str(p) for p in unused]},
        "discarded_bytes": _size(_files(trash_dir(workspace))),
        "cache_bytes": _size(_files(Path(workspace) / "previews")) + _size(quadros),
    }


def free_space(workspace: Path, projects, paths: list[str]) -> dict:
    """Apaga de vez: os renders indicados (se ainda fora de todo manifesto), descartados e caches."""
    used = in_use(projects)
    freed = 0
    deleted = 0
    for raw in paths:
        mov = _render_file(workspace, raw)
        if mov is None or str(mov).lower() in used:
            continue
        for ext in _COMPANIONS:
            f = mov.with_suffix(ext)
            if f.exists():
                try:
                    size = f.stat().st_size
                    f.unlink()
                    freed += size
                    deleted += ext == ".mov"
                except OSError:
                    pass  # em uso: fica para a próxima
    # .ass sem o .mov (render cancelado no meio): não servem para nada.
    for ass in (Path(workspace) / "renders").glob("*/SC_*.ass"):
        if not ass.with_suffix(".mov").exists():
            try:
                size = ass.stat().st_size
                ass.unlink()
                freed += size
            except OSError:
                pass
    for folder in (trash_dir(workspace), Path(workspace) / "previews"):
        freed += _size(_files(folder))
        shutil.rmtree(folder, ignore_errors=True)
    cache = Path(workspace).parent / "cache"
    for p in cache.glob("quadro_*.jpg") if cache.is_dir() else []:
        try:
            size = p.stat().st_size
            p.unlink()
            freed += size
        except OSError:
            pass
    return {"freed_bytes": freed, "deleted_renders": deleted}
