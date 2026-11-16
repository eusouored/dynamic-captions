"""Torção: o contorno das letras ondula como desenho feito à mão (efeito contínuo).

As letras deixam de ser texto e viram formas vetoriais do ASS, tiradas do contorno da própria
fonte. Um campo suave desloca as curvas; alternar entre ``VARIANTS`` versões, uma a cada
``wobble_ms``, dá o "tremido" de animação tradicional.
"""

from __future__ import annotations

import math
import re
from functools import lru_cache

from .typography import resolve_font

VARIANTS = 8
AMPLITUDE = .014   # força do efeito, em frações do tamanho da fonte (controle "Intensidade")
# Receita do "desenhado à mão" do After Effects: um deslocamento turbulento largo (a letra
# inteira entorta), outro miúdo (bordas rugosas) e uma troca brusca de desenho a cada passo —
# cada variação é um sorteio novo, sem relação com a anterior.
LOW_WAVE, LOW_GAIN = .6, 1.0     # ondas largas: comprimento (em tamanhos de fonte) e peso
HIGH_WAVE, HIGH_GAIN = .055, 1.5  # rugosidade das bordas (miúda, como no "Bordes rugosos" do AE)
STEP = .022                       # distância entre pontos do contorno (bordas irregulares pedem pontos próximos)


def _hash(ix: int, iy: int, seed: int) -> float:
    n = (ix * 374761393 + iy * 668265263 + seed * 2147483647) & 0xFFFFFFFF
    n = ((n ^ (n >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((n ^ (n >> 16)) & 0xFFFF) / 32767.5 - 1


def _noise(x: float, y: float, seed: int) -> float:
    """Ruído suave entre -1 e 1 (valores sorteados numa grade, interpolados)."""
    ix, iy = math.floor(x), math.floor(y)
    fx, fy = x - ix, y - iy
    fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    a, b = _hash(ix, iy, seed), _hash(ix + 1, iy, seed)
    c, d = _hash(ix, iy + 1, seed), _hash(ix + 1, iy + 1, seed)
    return (a + (b - a) * fx) * (1 - fy) + (c + (d - c) * fx) * fy


def _lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def _split(p0, c1, c2, p3, n):
    """Divide a cúbica em n partes (de Casteljau), para curvas longas também ondularem."""
    out = []
    for i in range(n):
        t = 1 / (n - i)
        a, b, c = _lerp(p0, c1, t), _lerp(c1, c2, t), _lerp(c2, p3, t)
        d, e = _lerp(a, b, t), _lerp(b, c, t)
        f = _lerp(d, e, t)
        out.append((a, d, f))
        p0, c1, c2 = f, e, c
    return out


@lru_cache(maxsize=2048)
def outline(text: str, family: str, bold: bool, italic: bool, tracking: float = 0.0):
    """Contornos do texto em unidades de "linha" (1.0 = \\fs do ASS), origem na base esquerda.

    ``tracking``: espaço extra entre letras, na mesma unidade (\\fsp / \\fs).
    Devolve (contornos, largura, subida) ou None se a fonte não puder ser lida.
    """
    match = resolve_font(family, bold, italic)
    if not match:
        return None
    try:
        from fontTools.pens.recordingPen import DecomposingRecordingPen
        from fontTools.ttLib import TTCollection, TTFont

        f = TTCollection(match[1]).fonts[match[2]] if match[1].lower().endswith(".ttc") else TTFont(match[1])
        glyphs, cmap, hmtx, os2 = f.getGlyphSet(), f.getBestCmap(), f["hmtx"], f["OS/2"]
        total = os2.usWinAscent + os2.usWinDescent
        if not total:
            return None
        k = 1 / total
        contours, x = [], 0.0
        for ci, ch in enumerate(text):
            if ci:
                x += tracking
            name = cmap.get(ord(ch))
            if name is None:
                return None  # sem o glifo, o libass trocaria de fonte: melhor manter como texto
            pen = DecomposingRecordingPen(glyphs)
            glyphs[name].draw(pen)
            cur = start = None
            segs: list = []

            def point(p, x=x):
                return (p[0] * k + x, -p[1] * k)

            def cubic(c1, c2, e):
                nonlocal cur
                length = math.dist(cur, c1) + math.dist(c1, c2) + math.dist(c2, e)
                segs.extend(_split(cur, c1, c2, e, max(1, round(length / STEP))))
                cur = e

            def close():
                nonlocal segs
                if segs:
                    if math.dist(cur, start) > 1e-4:
                        cubic(_lerp(cur, start, 1 / 3), _lerp(cur, start, 2 / 3), start)
                    contours.append((start, tuple(segs)))
                segs = []

            for op, args in pen.value:
                if op == "moveTo":
                    close()
                    cur = start = point(args[0])
                elif op == "lineTo":
                    e = point(args[0])
                    cubic(_lerp(cur, e, 1 / 3), _lerp(cur, e, 2 / 3), e)
                elif op == "qCurveTo":
                    pts = [point(p) for p in args if p is not None]
                    seq = []
                    for i, p in enumerate(pts[:-1]):
                        seq.append(p)
                        if i < len(pts) - 2:
                            seq.append(_lerp(p, pts[i + 1], .5))
                    seq.append(pts[-1])
                    for i in range(0, len(seq) - 1, 2):
                        c, e = seq[i], seq[i + 1]
                        cubic(_lerp(cur, c, 2 / 3), _lerp(e, c, 2 / 3), e)
                elif op == "curveTo":
                    c1, c2, e = (point(p) for p in args)
                    cubic(c1, c2, e)
                else:  # closePath / endPath
                    close()
            close()
            x += hmtx[name][0] * k
        return tuple(contours), x, os2.usWinAscent * k
    except Exception:  # noqa: BLE001 - fonte ilegível: a palavra continua como texto comum
        return None


@lru_cache(maxsize=4096)
def drawing(text: str, family: str, bold: bool, italic: bool, size: float, spacing: float,
            variant: int, seed_x: float = 0, seed_y: float = 0, amplitude: float = AMPLITUDE) -> str | None:
    """Forma ASS (\\p1) da palavra, centrada em (0,0), na variação pedida; None = usar texto."""
    # Espaçamento entre letras entra no avanço de cada glifo (antes a torção era desligada).
    data = outline(text, family, bold, italic, round(spacing / size, 4) if spacing and size else 0.0)
    if data is None or not data[0]:
        return None
    contours, width, ascent = data
    v = variant % VARIANTS
    low, high = size * amplitude * LOW_GAIN, size * amplitude * HIGH_GAIN
    lw, hw = size * LOW_WAVE, size * HIGH_WAVE
    ox, oy = -width * size / 2, (ascent - .5) * size

    def warp(p):
        x, y = p[0] * size + ox, p[1] * size + oy
        gx, gy = x + seed_x, y + seed_y
        dx = low * _noise(gx / lw, gy / lw, 11 + v * 4) + high * _noise(gx / hw, gy / hw, 12 + v * 4)
        dy = low * _noise(gx / lw, gy / lw, 13 + v * 4) + high * _noise(gx / hw, gy / hw, 14 + v * 4)
        return f"{x + dx:.1f} {y + dy:.1f}"

    # Linhas retas entre pontos próximos: é o que dá a borda "rasgada" do traço à mão.
    parts = []
    for start, segs in contours:
        parts.append("m " + warp(start) + " l")
        parts.extend(warp(seg[2]) for seg in segs)
    return " ".join(parts)


def _args(text: str, i: int) -> tuple[str, int]:
    """Conteúdo de um parêntese que começa em text[i] == '(' e o índice depois do ')' correspondente."""
    depth, j = 0, i
    while j < len(text):
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
            if depth == 0:
                return text[i + 1:j], j + 1
        j += 1
    return text[i + 1:], len(text)


def shift_tags(tags: str, offset_ms: int) -> str:
    """Reescreve \\t e \\move para um evento que começa ``offset_ms`` depois do original."""
    if offset_ms <= 0:
        return tags
    out, i = [], 0
    while i < len(tags):
        if tags.startswith("\\t(", i):
            inner, end = _args(tags, i + 2)
            m = re.match(r"(-?\d+),(-?\d+),(.*)$", inner, re.S)
            if m:
                a, b, rest = int(m.group(1)) - offset_ms, int(m.group(2)) - offset_ms, shift_tags(m.group(3), offset_ms)
                out.append(rest if b <= 0 else f"\\t({a},{b},{rest})")  # já terminou: vale o estado final
            else:
                out.append(tags[i:end])
            i = end
        elif tags.startswith("\\move(", i):
            inner, end = _args(tags, i + 5)
            v = [float(n) for n in inner.split(",")]
            if len(v) == 6:
                x1, y1, x2, y2, a, b = v
                a, b = a - offset_ms, b - offset_ms
                if b <= 0:
                    out.append(f"\\pos({x2:g},{y2:g})")
                elif a < 0:
                    p = -a / (b - a)
                    out.append(f"\\move({x1 + (x2 - x1) * p:g},{y1 + (y2 - y1) * p:g},{x2:g},{y2:g},0,{b:g})")
                else:
                    out.append(f"\\move({x1:g},{y1:g},{x2:g},{y2:g},{a:g},{b:g})")
            else:
                out.append(tags[i:end])
            i = end
        else:
            out.append(tags[i])
            i += 1
    return "".join(out)


def slices(start_us: int, end_us: int, step_us: int):
    """Fatias [a, b) de um evento na grade global da torção, com o número da variação."""
    step_us = max(step_us, 40_000)
    t = start_us
    while t < end_us:
        n = t // step_us
        nxt = min((n + 1) * step_us, end_us)
        yield t, nxt, n % VARIANTS
        t = nxt
