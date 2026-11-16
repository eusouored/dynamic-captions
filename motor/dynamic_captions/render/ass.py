"""Compilação para ASS (Advanced SubStation Alpha), renderizado pela libass.

O mesmo arquivo alimenta a prévia no painel (JASSUB, libass em WebAssembly) e a exportação
(filtro ``ass`` do FFmpeg com canal alfa). Por isso o que o editor vê é o que vai para a timeline.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
import re

from ..domain.models import CaptionBlock, Transcript, Word
from ..domain.timecode import format_ass_time
from .style import StyleLayers, StyleSpec

_ALIGN = {"bottom": 2, "middle": 5, "top": 8}


def ass_color(hex_rgb: str, opacity: float = 1.0) -> str:
    """'#RRGGBB' + opacidade → '&HAABBGGRR' (no ASS, alfa 00 = opaco)."""
    r, g, b = hex_rgb[1:3], hex_rgb[3:5], hex_rgb[5:7]
    alpha = round((1.0 - opacity) * 255)
    return f"&H{alpha:02X}{b}{g}{r}".upper()


_LINE_BREAKS = str.maketrans({"\r": " ", "\n": " ", "\u2028": " ", "\u2029": " "})


def _escape(text: str) -> str:
    # Quebra de linha no texto encerraria o evento Dialogue e criaria uma linha solta no ASS.
    return text.translate(_LINE_BREAKS).replace("\\", "/").replace("{", "(").replace("}", ")")


def _style_name(sid: str, taken: set[str]) -> str:
    """Nome de estilo ASS seguro (vírgula ou quebra de linha num id quebrariam as linhas Style/Dialogue)."""
    base = "Spk_" + (re.sub(r"[^A-Za-z0-9_-]", "_", sid) if sid else "none")
    name, n = base, 2
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    taken.add(name)
    return name


def _style_line(name: str, s: StyleSpec, width: int, height: int) -> str:
    scale = height / 1080
    size = round(height * s.font_size_pct / 100)
    karaoke = s.karaoke in ("color", "fill")
    primary = ass_color(s.highlight_color if karaoke else s.text_color)
    secondary = ass_color(s.text_color)
    if s.box:
        border_style, outline_col, outline = 3, ass_color(s.box_color, s.box_opacity), s.outline_px * scale
    else:
        border_style, outline_col, outline = 1, ass_color(s.outline_color), s.outline_px * scale
    back = ass_color("#000000", 0.5)
    # Margem 0 encostaria contorno/sombra na borda: guarda o mínimo para não cortar.
    edge = math.ceil((s.outline_px + s.shadow_px + s.glow_px) * scale) + 2
    margin_v = 0 if s.position == "middle" else max(round(height * s.margin_pct / 100), edge)
    margin_lr = round(width * 0.05)
    fields = [
        name, s.font_family, str(size), primary, secondary, outline_col, back,
        "-1" if s.bold else "0", "-1" if s.italic else "0", "0", "0", "100", "100",
        f"{s.spacing_px * scale:g}", "0",
        str(border_style), f"{outline:.1f}", f"{s.shadow_px * scale:.1f}",
        str(_ALIGN[s.position]), str(margin_lr), str(margin_lr), str(margin_v), "1",
    ]
    return "Style: " + ",".join(fields)


def _event_fade(s: StyleSpec, index: int, count: int, dur_us: int) -> str:
    """Surgir num bloco dividido em vários eventos (destaque por palavra, Revelar): entra no primeiro
    e sai no último. Antes cada evento tinha o próprio fade e a frase piscava a cada palavra.
    Em "Palavra" cada palavra é um evento: fade curto, para chegar à opacidade cheia."""
    ms = s.animation_ms
    if s.text_mode == "word":
        m = min(ms, dur_us // 4000)
        return f"\\fad({m},{m})" if m > 0 else ""
    fin = min(ms, dur_us // 2000) if index == 0 else 0
    fout = min(ms, dur_us // 2000) if index == count - 1 else 0
    return f"\\fad({fin},{fout})" if fin or fout else ""


def _anim_tags(s: StyleSpec, width: int, height: int) -> str:
    ms = s.animation_ms
    if s.animation_in == "fade":
        return f"\\fad({ms},{ms})"
    if s.animation_in == "pop":
        return f"\\fscx85\\fscy85\\t(0,{ms},\\fscx100\\fscy100)"
    if s.animation_in == "bounce":
        a, b = round(ms * .55), round(ms * .8)
        return (f"\\fscx70\\fscy70\\t(0,{a},\\fscx112\\fscy112)"
                f"\\t({a},{b},\\fscx96\\fscy96)\\t({b},{ms},\\fscx100\\fscy100)")
    if s.animation_in == "zoom":
        return f"\\fscx125\\fscy125\\t(0,{ms},\\fscx100\\fscy100)"
    if s.animation_in == "blur":
        return f"\\blur{height / 1080 * 12:g}\\t(0,{ms},\\blur0)"
    if s.animation_in == "rotate":
        return f"\\frz-8\\fscx92\\fscy92\\t(0,{ms},\\frz0\\fscx100\\fscy100)"
    if s.animation_in == "slide":
        x = width // 2
        if s.position == "middle":
            y = height // 2
        elif s.position == "top":
            y = round(height * s.margin_pct / 100)
        else:
            y = height - round(height * s.margin_pct / 100)
        offset = round(height * 0.03)
        return f"\\move({x},{y + offset},{x},{y},0,{ms})"
    return ""


def _block_text(block: CaptionBlock, words: Mapping[str, Word], s: StyleSpec, offset_us: int) -> str:

    def show(t: str) -> str:
        return _escape(t.upper() if s.uppercase else t)

    if s.karaoke == "none":
        return "\\N".join(show(line.text) for line in block.lines)
    tag = "kf" if s.karaoke == "fill" else "k"
    ordered = [words[wid] for line in block.lines for wid in line.word_ids if wid in words]
    if not ordered:
        return "\\N".join(show(line.text) for line in block.lines)
    # Fronteiras em centésimos acumulados para não haver desvio por arredondamento.
    starts_cs = [round((w.start_us - offset_us) / 10_000) for w in ordered]
    block_start_cs = round((block.start_us - offset_us) / 10_000)
    block_end_cs = round((block.end_us - offset_us) / 10_000)
    starts_cs[0] = block_start_cs
    bounds = starts_cs + [block_end_cs]
    parts: list[str] = []
    k = 0
    for li, line in enumerate(block.lines):
        if li:
            parts.append("\\N")
        tokens = []
        for wid in line.word_ids:
            if wid not in words:
                continue
            dur = max(bounds[k + 1] - bounds[k], 0)
            tokens.append(f"{{\\{tag}{dur}}}{show(words[wid].text)}")
            k += 1
        parts.append(" ".join(tokens))
    return "".join(parts)


def _timed_text(block: CaptionBlock, words: Mapping[str, Word], s: StyleSpec,
                start_us: int, end_us: int, active_start: int | None) -> str:
    """Mantém o espaço das palavras ocultas, para a frase não saltar de posição."""
    lines = []
    for line in block.lines:
        tokens = []
        for wid in line.word_ids:
            w = words.get(wid)
            if w is None:
                continue
            if s.text_mode == "word" and w.start_us != active_start:
                continue
            text = _escape(w.text.upper() if s.uppercase else w.text)
            color = s.highlight_color if s.karaoke == "active" and w.start_us == active_start else s.text_color
            prefix = f"\\1c{ass_color(color)}&"
            hidden = s.text_mode == "progressive" and w.start_us >= end_us
            if hidden:
                tokens.append("{" + prefix + "\\alpha&HFF&}" + text)
            elif s.animation_in == "typewriter":
                duration = min(max(w.duration_us, 10_000), s.animation_ms * 1000)
                chars = []
                for i, char in enumerate(text):
                    reveal = w.start_us + round(i * duration / max(len(text), 1))
                    if reveal <= start_us:
                        tag = "\\alpha&H00&"
                    else:
                        ms = round((reveal - start_us) / 1000)
                        tag = f"\\alpha&HFF&\\t({ms},{ms + 1},\\alpha&H00&)"
                    chars.append("{" + prefix + tag + "}" + char)
                tokens.append("".join(chars))
            else:
                tokens.append("{" + prefix + "\\alpha&H00&}" + text)
        if tokens:
            lines.append(" ".join(tokens))
    return "\\N".join(lines)


def _events(block: CaptionBlock, words: Mapping[str, Word], s: StyleSpec, offset_us: int):
    dynamic = s.text_mode != "phrase" or s.karaoke == "active"
    if not dynamic:
        if s.animation_in == "typewriter":
            text = _timed_text(block, words, s, block.start_us, block.end_us, None)
        else:
            text = _block_text(block, words, s, offset_us)
        yield block.start_us, block.end_us, text
        return
    ordered = [words[wid] for line in block.lines for wid in line.word_ids if wid in words]
    # Mesma regra do caminho rico: cada palavra é "a da vez" do seu início até a próxima, sem
    # buracos nas pausas (antes o destaque sumia entre uma palavra e outra).
    from .rich_ass import active_windows
    win = active_windows(block, ordered)
    bounds = sorted({block.start_us, block.end_us} | {
        t for a, b in win.values() for t in (a, b) if block.start_us < t < block.end_us
    })
    previous = None
    for start, end in zip(bounds, bounds[1:]):
        mid = (start + end) // 2
        active = [w for w in ordered if win[w.id][0] <= mid < win[w.id][1]]
        active_start = max((w.start_us for w in active), default=None)
        if s.text_mode == "word" and active_start is None:
            continue
        text = _timed_text(block, words, s, start, end, active_start)
        # Sem animação por letra, estados iguais podem compartilhar o mesmo evento.
        if previous and previous[2] == text and previous[1] == start and s.animation_in != "typewriter":
            previous = (previous[0], end, text)
        else:
            if previous:
                yield previous
            previous = (start, end, text)
    if previous:
        yield previous


def _fit_phrase_size(text: str, spec: StyleSpec, width: int, height: int) -> float | None:
    """Tamanho (\\fs) que faz a linha mais larga caber em 90% do quadro; None se já cabe.

    O ASS usa WrapStyle 2 (as quebras vêm do agrupamento), então uma linha longa em vídeo
    vertical ou com fonte grande passaria da borda. Mede com a fonte real (mesma régua da prévia).
    """
    from .typography import ass_ratio, text_width

    lines = [ln.strip() for ln in re.sub(r"\{[^}]*\}", "", text).split("\\N")]
    lines = [ln for ln in lines if ln]
    if not lines:
        return None
    scale = height / 1080
    size = round(height * spec.font_size_pct / 100)
    ratio = ass_ratio(spec.font_family, spec.bold, spec.italic)
    peak = 1.25 if spec.animation_in == "zoom" else 1.12 if spec.animation_in == "bounce" else 1
    widest = max(text_width(ln, spec.font_family, size * ratio, spec.bold, spec.italic, spec.spacing_px * scale) for ln in lines)
    room = (width * 0.90 - 2 * (spec.outline_px + spec.glow_px) * scale) / peak * 0.97
    if widest <= room or widest <= 0:
        return None
    return max(1.0, math.floor(size * room / widest))


def compile_ass(
    blocks: Sequence[CaptionBlock],
    transcript: Transcript,
    layers: StyleLayers,
    *,
    width: int,
    height: int,
    offset_us: int = 0,
    title: str = "Dynamic Captions",
) -> str:
    """Gera o documento ASS. ``offset_us`` desloca os tempos (renderização em blocos)."""
    speaker_ids = sorted({b.speaker_id or "" for b in blocks})
    styles: dict[str, tuple[str, StyleSpec]] = {}
    taken: set[str] = set()
    for sid in speaker_ids:
        sp = transcript.speaker(sid) if sid else None
        styles[sid] = (_style_name(sid, taken), layers.resolve(sid or None, sp.color if sp else None))
    out = [
        "[Script Info]",
        f"Title: {title}",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: None",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
    ]
    relevant_ids={wid for block in blocks for wid in block.word_ids}
    rich = bool(relevant_ids.intersection(layers.words)) or any(spec.marker != "none" or spec.active_scale != 1 or spec.animation_in not in ("none", "fade") or spec.wobble or spec.arch_pct > 0 for _,spec in styles.values())
    out += [_style_line(name, spec, width, height) for name, spec in styles.values()]
    if rich:
        out += [_style_line(name+"_rich", spec.model_copy(update={"box":False}), width, height) for name,spec in styles.values()]
    out += ["", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    needs_words = any(spec.karaoke != "none" or spec.text_mode != "phrase" or
                      spec.animation_in == "typewriter" for _, spec in styles.values())
    words = {w.id: w for w in transcript.words} if needs_words or rich else {}
    for b in blocks:
        name, spec = styles[b.speaker_id or ""]
        if rich:
            from .typography import needs_layout
            from .rich_ass import events
            if needs_layout(b,spec,layers):
                out.extend(events(b,words,spec,layers,name+"_rich",width,height,offset_us))
                continue
        evs = [(max(a - offset_us, 0), max(z - offset_us, 0), text) for a, z, text in _events(b, words, spec, offset_us)]
        evs = [e for e in evs if e[1] > e[0] and e[2]]
        for i, (start, end, text) in enumerate(evs):
            anim = _event_fade(spec, i, len(evs), end - start) if spec.animation_in == "fade" else _anim_tags(spec, width, height)
            if spec.text_mode == "word":
                # Uma palavra não pode ser quebrada como uma frase. Reserva espaço
                # para os glifos mais largos, contorno, caixa e excesso da animação.
                plain = re.sub(r"\{[^}]*\}", "", text).replace("\\N", " ").strip()
                count = max(len(plain), 1)
                scale = height / 1080
                peak = 1.25 if spec.animation_in == "zoom" else 1.12 if spec.animation_in == "bounce" else 1
                room = width * .86 / peak - 2 * spec.outline_px * scale - max(0,count-1) * spec.spacing_px * scale
                font_size = min(round(height * spec.font_size_pct / 100), max(1., room / (count * 1.05)))
                anim += f"\\fs{font_size:g}"
            else:
                fitted = _fit_phrase_size(text, spec, width, height)
                if fitted:
                    anim += f"\\fs{fitted:g}"
            prefix = f"{{{anim}}}" if anim else ""
            if spec.glow_px:
                scale = height / 1080
                glow = (f"\\blur{spec.glow_px * scale:g}\\bord{(spec.outline_px + spec.glow_px) * scale:g}"
                        f"\\3c{ass_color(spec.outline_color)}&\\3a&H70&")
                out.append(f"Dialogue: 0,{format_ass_time(start)},{format_ass_time(end)},{name},,0,0,0,,{{{anim}{glow}}}{text}")
            layer = 1 if spec.glow_px else 0
            out.append(f"Dialogue: {layer},{format_ass_time(start)},{format_ass_time(end)},{name},,0,0,0,,{prefix}{text}")
    return "\n".join(out) + "\n"
