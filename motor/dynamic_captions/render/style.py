"""Especificação de estilo, independente do renderizador.

Níveis (cada um sobrescreve o anterior): preset global → falante → bloco → palavra.
Esta versão implementa global, falante e sobrescritas por palavra.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
LEGACY_STYLE_KEYS = ("composition", "emphasis_scale", "secondary_scale", "line_gap_pct")
LINE_GAP_PCT = 1.0  # espaço entre as linhas, em % da altura do quadro


class StyleSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    font_family: str = Field("Arial", min_length=1, max_length=160)
    font_size_pct: float = Field(5.5, gt=0, le=30)  # % da altura do quadro
    bold: bool = True
    italic: bool = False
    spacing_px: float = Field(0, ge=0, le=20)
    glow_px: float = Field(0, ge=0, le=30)
    uppercase: bool = False
    text_color: str = "#FFFFFF"
    highlight_color: str = "#FFD400"
    outline_color: str = "#000000"
    outline_px: float = Field(3.0, ge=0, le=60)  # referência 1080p, escalado pela altura; teto evita render de minutos
    shadow_px: float = Field(0.0, ge=0, le=60)
    box: bool = False
    box_color: str = "#000000"
    box_opacity: float = Field(0.6, ge=0, le=1)
    position: Literal["bottom", "middle", "top"] = "bottom"
    margin_pct: float = Field(8.0, ge=0, le=45)  # margem vertical, % da altura
    # Deslocamento livre da camada de legenda (arrastar na prévia), em % do quadro.
    offset_x_pct: float = Field(0.0, ge=-50, le=50)
    offset_y_pct: float = Field(0.0, ge=-50, le=50)
    text_mode: Literal["phrase", "word", "progressive"] = "phrase"
    karaoke: Literal["none", "fill", "color", "active"] = "none"
    animation_in: Literal["none", "fade", "pop", "slide", "bounce", "zoom", "blur", "rotate", "typewriter",
                          "blur_rise", "curve_up", "custom",
                          # 0.2.11: pacote de aberturas por palavra/letra com desfoque suave
                          "letter_fade", "fade_down", "slide_left", "slide_right", "tracking", "colors",
                          "from_right", "random",
                          # 0.2.12: medida quadro a quadro do CapCut (vídeo de referência)
                          "blur_ascend"] = "none"
    animation_ms: int = Field(300, ge=0, le=2000)
    speaker_color_target: Literal["none", "text", "highlight"] = "highlight"
    max_lines: int = Field(2, ge=1, le=2)  # linhas por legenda; o que não couber vira o próximo trecho
    marker: Literal["none", "active"] = "none"
    marker_color: str = "#D9FF55"
    marker_opacity: float = Field(.95, ge=0, le=1)
    marker_padding_px: float = Field(6, ge=0, le=30)
    marker_radius_px: float = Field(5, ge=0, le=40)
    active_scale: float = Field(1, ge=1, le=1.35)
    # Torção: contorno das letras ondulando como desenho animado; wobble_ms = tempo de cada "quadro".
    wobble: bool = False
    wobble_ms: int = Field(125, ge=60, le=600)  # 8 desenhos por segundo, como o "desenhado à mão" do After Effects
    wobble_pct: float = Field(1.4, ge=.3, le=4)  # quanto o contorno se desloca, em % do tamanho da fonte
    # Arco: a linha curvada para cima (∩), letras inclinadas nas pontas e maiores no meio; em % da largura da linha.
    arch_pct: float = Field(0, ge=0, le=6)  # acima de ~6% as letras do meio se atropelam
    # Entrada personalizada (animation_in="custom"): de onde cada item parte até o estado final.
    entry_unit: Literal["phrase", "word", "letter"] = "word"      # o que entra de cada vez
    entry_stagger_pct: float = Field(45, ge=0, le=100)            # atraso entre itens, em % da duração
    entry_rise_pct: float = Field(3.5, ge=-15, le=15)             # começa abaixo (+) ou acima (-), em % da altura
    entry_blur_px: float = Field(0, ge=0, le=40)                  # desfoque inicial (referência 1080p)
    entry_scale: float = Field(1, ge=.1, le=2.5)                  # tamanho inicial (1 = sem mudança)
    entry_rotate_deg: float = Field(0, ge=-90, le=90)             # giro inicial
    entry_fade: bool = True                                       # surge da transparência
    entry_shift_pct: float = Field(0, ge=-15, le=15)              # começa à direita (+) ou à esquerda (-), em % da altura
    entry_order: Literal["forward", "reverse", "random"] = "forward"  # ordem em que os itens entram
    entry_tint: bool = False                                      # entra na cor de destaque e passa à cor do texto

    @model_validator(mode="before")
    @classmethod
    def _drop_legacy(cls, data):
        # 0.2.12: a hierarquia (2/3 níveis, palavra principal maior) saiu. Projetos e modelos
        # exportados antes ainda trazem esses campos: são ignorados e a legenda fica em fluxo.
        if isinstance(data, dict) and any(k in data for k in LEGACY_STYLE_KEYS):
            data = {k: v for k, v in data.items() if k not in LEGACY_STYLE_KEYS}
        if isinstance(data, dict) and isinstance(data.get("arch_pct"), (int, float)) and data["arch_pct"] > 6:
            data = {**data, "arch_pct": 6}  # o 1º pacote 0.2.13 ia até 10%: abre no máximo atual
        return data

    @model_validator(mode="after")
    def _temporal_modes(self):
        if self.karaoke in ("fill", "color") and (self.text_mode != "phrase" or self.animation_in == "typewriter"):
            raise ValueError("karaokê acumulado/preenchido exige frase inteira e entrada diferente de digitação")
        if self.marker != "none" or self.active_scale != 1:
            if self.text_mode == "word" or self.karaoke in ("fill", "color") or self.animation_in == "typewriter":
                raise ValueError("marcador e pulso exigem frase/progressivo, sem karaokê acumulado ou digitação")
        return self

    @field_validator("text_color", "highlight_color", "outline_color", "box_color", "marker_color")
    @classmethod
    def _hex(cls, v: str) -> str:
        if not _HEX.match(v):
            raise ValueError(f"cor inválida: {v}")
        return v.upper()

    @field_validator("font_family")
    @classmethod
    def _font_name(cls,v):
        if re.search(r"[{},\\\r\n;\"'<>]",v) or not v.strip():
            raise ValueError("nome da fonte inválido")
        return v.strip()



# Aberturas: de onde cada item (frase, palavra ou letra) parte até o estado final.
#   rise  = começa abaixo (+) ou acima (-), em % da altura      scale = tamanho inicial
#   ease  = "out" (desacelera) ou "back" (passa do ponto e assenta; ``over`` = quanto passa)
_ENTRIES = {
    "pop":    {"unit": "phrase", "scale": .72, "ease": "back", "over": 2.2},
    "slide":  {"unit": "phrase", "rise": 4.0},
    "bounce": {"unit": "phrase", "rise": 6.0, "ease": "back", "over": 3.4},
    "zoom":   {"unit": "phrase", "scale": 1.7},
    "blur":   {"unit": "phrase", "blur": 12.0},                 # só projetos antigos
    "rotate": {"unit": "phrase", "rotate": -8.0, "scale": .92},  # só projetos antigos
    "blur_rise": {"unit": "word", "stagger": .45, "rise": 3.5, "blur": 14.0},                 # Fade para cima
    "fade_down": {"unit": "word", "stagger": .45, "rise": -3.5, "blur": 14.0},                # Fade para baixo
    "slide_left": {"unit": "word", "stagger": .5, "shift": -5.0, "blur": 12.0},               # sai de trás da palavra anterior (vem da esquerda)
    "slide_right": {"unit": "word", "stagger": .5, "shift": 5.0, "blur": 12.0},               # vem da direita
    "from_right": {"unit": "word", "stagger": .5, "rise": 3.5, "blur": 10.0, "order": "reverse"},  # última palavra primeiro
    "letter_fade": {"unit": "letter", "stagger": .16, "rise": 1.2, "blur": 8.0},              # letra a letra
    "tracking": {"unit": "letter", "stagger": .16, "shift": 3.0, "blur": 8.0},                # letras chegam da direita
    "colors": {"unit": "letter", "stagger": .18, "blur": 5.0, "tint": True},                  # letra entra colorida
    "random": {"unit": "letter", "stagger": .1, "blur": 8.0, "order": "random"},              # letras em ordem sorteada
    "typewriter": {"unit": "letter", "instant": True, "fade": False},
    # Desfoque ascendente (CapCut, medido quadro a quadro): palavra por palavra a cada ~0,13 s, cada uma
    # parte 11% da altura do quadro abaixo, desfocada e transparente, e sobe desacelerando. A palavra se
    # move inteira (a forma dela não muda); durante a entrada a frase forma uma "escada" que se alinha.
    "blur_ascend": {"unit": "word", "stagger": .27, "rise": 11.0, "blur": 10.0},
}
MIN_ENTRY_MS = 100  # piso da abertura: 0,1 s (3 quadros a 30 fps)


def cascade(spec: "StyleSpec") -> dict | None:
    """Parâmetros da abertura; None para "sem" e "surgir" (fade simples)."""
    base = {"unit": "phrase", "stagger": 0.0, "rise": 0.0, "blur": 0.0, "scale": 1.0, "rotate": 0.0,
            "fade": True, "ease": "out", "over": 0.0, "instant": False, "shift": 0.0, "order": "forward", "tint": False}
    if spec.animation_in == "blur_ascend" and spec.position == "bottom":
        # No rodapé, partir 11% abaixo jogaria a palavra para fora do quadro: parte de dentro dele.
        gap = spec.margin_pct - spec.offset_y_pct
        return {**base, **_ENTRIES["blur_ascend"], "rise": max(2.5, min(11.0, gap - 1.0))}
    if spec.animation_in in _ENTRIES:
        return {**base, **_ENTRIES[spec.animation_in]}
    if spec.animation_in == "curve_up":    # Curva para cima (CapCut, medido quadro a quadro)
        # Letra por letra; cada uma nasce do zero e chega ao tamanho cheio em ~0,1 s crescendo em volta do
        # próprio centro (o "rise" negativo compensa a âncora na base), sem fade nem desfoque. Uma nova letra
        # a cada 2/3 da duração (0,067 s com 0,1 s).
        return {**base, "unit": "letter", "stagger": .67, "rise": -.25 * spec.font_size_pct, "scale": 0.0,
                "fade": False, "ease": "quad"}
    if spec.animation_in == "custom":
        return {**base, "unit": spec.entry_unit, "stagger": spec.entry_stagger_pct / 100, "rise": spec.entry_rise_pct,
                "blur": spec.entry_blur_px, "scale": spec.entry_scale, "rotate": spec.entry_rotate_deg,
                "fade": spec.entry_fade, "shift": spec.entry_shift_pct, "order": spec.entry_order, "tint": spec.entry_tint}
    return None


def entry_ms(spec: "StyleSpec") -> int:
    """Duração efetiva da abertura (com o piso que garante que ela apareça)."""
    if spec.animation_in in ("none", "fade"):
        return spec.animation_ms
    return max(spec.animation_ms, MIN_ENTRY_MS) if spec.animation_ms else 0


class WordStyle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    font_scale: float = Field(1, ge=.5, le=2.5)
    font_family: str | None = Field(None, min_length=1, max_length=160)
    bold: bool | None = None
    italic: bool | None = None
    text_color: str | None = None
    break_before: bool = False

    @model_validator(mode="before")
    @classmethod
    def _drop_legacy(cls, data):
        if isinstance(data, dict) and "emphasis" in data:  # "palavra principal" da hierarquia (até 0.2.11)
            data = {k: v for k, v in data.items() if k != "emphasis"}
        return data

    @field_validator("text_color")
    @classmethod
    def _color(cls, v):
        if v is not None and not _HEX.fullmatch(v):
            raise ValueError("cor da palavra inválida")
        return v.upper() if v else None

    @field_validator("font_family")
    @classmethod
    def _font_name(cls,v):
        return StyleSpec._font_name(v) if v is not None else None


def voice_target(spec: StyleSpec) -> str:
    """Onde a cor da voz entra: "text", "highlight" ou "none".

    Com destaque ligado, "Cor por voz: Texto" pinta o destaque (a palavra falada) e o resto fica na
    cor do texto. Antes a frase inteira virava a cor da voz, igual à do destaque: destaque invisível."""
    target = spec.speaker_color_target
    if target == "text" and spec.karaoke != "none":
        return "highlight"
    return target


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def color_distance(a: str, b: str) -> float:
    return sum((x - y) ** 2 for x, y in zip(_rgb(a), _rgb(b))) ** .5


NEAR = 90  # abaixo disso duas cores se confundem na legenda


def contrast_for(color: str) -> str:
    """Cor que se destaca de ``color``: amarelo sobre texto quase branco, branco nos demais."""
    return "#FFD400" if color_distance(color, "#FFFFFF") < NEAR else "#FFFFFF"


def _lum(hex_color: str) -> float:
    """Luminância relativa (WCAG)."""
    def ch(v):
        v /= 255
        return v / 12.92 if v <= .03928 else ((v + .055) / 1.055) ** 2.4
    r, g, b = _rgb(hex_color)
    return .2126 * ch(r) + .7152 * ch(g) + .0722 * ch(b)


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + .05) / (lb + .05)


def readable_on(color: str, background: str) -> str:
    """A própria cor se ela se lê sobre o fundo (contraste ≥ 3); senão, quase preto ou branco, o que ler melhor."""
    if contrast(color, background) >= 3:
        return color
    return "#111111" if contrast("#111111", background) >= contrast("#FFFFFF", background) else "#FFFFFF"


def with_visible_highlight(spec: StyleSpec) -> StyleSpec:
    if spec.karaoke != "none" and color_distance(spec.text_color, spec.highlight_color) < NEAR:
        return spec.model_copy(update={"highlight_color": contrast_for(spec.text_color)})
    return spec


class StyleLayers(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = "custom"
    name: str = "Personalizado"
    base: StyleSpec = StyleSpec()
    speakers: dict[str, dict] = Field(default_factory=dict)  # sobrescritas parciais por falante
    words: dict[str, WordStyle] = Field(default_factory=dict)

    def resolve(self, speaker_id: str | None, speaker_color: str | None = None) -> StyleSpec:
        style = self.base
        target = voice_target(style)
        if speaker_color and target != "none":
            style = style.model_copy(update={f"{target}_color": speaker_color.upper()})
        patch = self.speakers.get(speaker_id or "", {})
        if patch:
            style = StyleSpec.model_validate({**style.model_dump(), **patch})
        return with_visible_highlight(style)


STYLE_PRESETS: dict[str, StyleLayers] = {
    "classico": StyleLayers(
        id="classico", name="Clássico",
        base=StyleSpec(speaker_color_target="text", karaoke="none", animation_in="fade"),
    ),
    "karaoke_social": StyleLayers(
        id="karaoke_social", name="Karaokê para redes",
        base=StyleSpec(
            font_size_pct=6.0, uppercase=True, outline_px=4.0, max_lines=1,
            karaoke="color", animation_in="pop", speaker_color_target="highlight",
        ),
    ),
    "caixa": StyleLayers(
        id="caixa", name="Caixa de fundo",
        base=StyleSpec(box=True, outline_px=12.0, speaker_color_target="text", animation_in="fade"),
    ),
}

# A variedade combina comportamento temporal, tipografia e tratamento visual.
# Os três IDs anteriores continuam válidos em projetos já salvos.
STYLE_PRESETS.update({s.id: s for s in (
    StyleLayers(id="palavra_ativa", name="Palavra em destaque",
                base=StyleSpec(karaoke="active", outline_px=4, speaker_color_target="highlight")),
    StyleLayers(id="karaoke_preenchido", name="Karaokê preenchido",
                base=StyleSpec(karaoke="fill", highlight_color="#7CFC9A", speaker_color_target="none")),
    StyleLayers(id="palavra_pop", name="Palavra com pop",
                base=StyleSpec(text_mode="word", animation_in="pop", font_family="Arial",
                               uppercase=True, font_size_pct=8,
                               text_color="#FFD400", outline_px=4, speaker_color_target="text")),
    StyleLayers(id="palavra_salto", name="Palavra com salto",
                base=StyleSpec(text_mode="word", animation_in="bounce", animation_ms=220,
                               font_size_pct=7, position="middle", margin_pct=0, outline_px=4,
                               speaker_color_target="text")),
    StyleLayers(id="palavra_caixa", name="Palavra com caixa",
                base=StyleSpec(text_mode="word", box=True, box_color="#335CFF", box_opacity=1,
                               outline_px=12, animation_in="pop", position="middle", margin_pct=0,
                               font_size_pct=6.5, speaker_color_target="none")),
    StyleLayers(id="revelar", name="Revelação por palavra",
                base=StyleSpec(text_mode="progressive", karaoke="active", font_size_pct=6,
                               outline_px=4, speaker_color_target="highlight")),
    StyleLayers(id="digitacao", name="Máquina de escrever",
                base=StyleSpec(animation_in="typewriter", animation_ms=240, font_family="Consolas",
                               font_size_pct=5, text_color="#7DEBFF", outline_px=2,
                               speaker_color_target="none")),
    StyleLayers(id="neon", name="Neon",
                base=StyleSpec(glow_px=6, outline_color="#8B5CF6", outline_px=1.5,
                               text_color="#F7F0FF", highlight_color="#CCAAFF", karaoke="active",
                               speaker_color_target="none")),
    StyleLayers(id="cinema", name="Cinema minimalista",
                base=StyleSpec(font_family="Segoe UI", font_size_pct=4.2, bold=False,
                               spacing_px=2, outline_px=1.5, shadow_px=2, margin_pct=10,
                               animation_in="fade", animation_ms=200, speaker_color_target="none")),
    StyleLayers(id="manchete", name="Manchete",
                base=StyleSpec(font_family="Arial Black", uppercase=True, font_size_pct=6,
                               box=True, box_color="#173C80", box_opacity=.95, outline_px=14,
                               animation_in="slide", position="top", speaker_color_target="none")),
    StyleLayers(id="zoom", name="Zoom de impacto",
                base=StyleSpec(font_family="Impact", uppercase=True, font_size_pct=7,
                               animation_in="zoom", animation_ms=200, outline_px=4,
                               speaker_color_target="text")),
    StyleLayers(id="desfoque", name="Foco suave",
                base=StyleSpec(animation_in="blur", animation_ms=240, outline_px=2,
                               font_size_pct=5, speaker_color_target="text")),
    StyleLayers(id="rotacao", name="Inclinação",
                base=StyleSpec(animation_in="rotate", animation_ms=220, uppercase=True,
                               font_size_pct=6.5, text_color="#FFD400", outline_px=4,
                               speaker_color_target="text")),
)})

STYLE_PRESETS.update({s.id: s for s in (
    StyleLayers(id="tipografia_2", name="Duas linhas · destaque",
                base=StyleSpec(font_size_pct=5.5, animation_in="fade", speaker_color_target="text")),
    StyleLayers(id="tipografia_3", name="Três linhas · hierarquia",
                base=StyleSpec(font_size_pct=5, animation_in="fade", speaker_color_target="text")),
    StyleLayers(id="editorial", name="Editorial · tipografia mista",
                base=StyleSpec(font_family="Georgia", bold=False, italic=True, font_size_pct=5.3,
                               animation_in="fade", speaker_color_target="text")),
    StyleLayers(id="marca_texto", name="Marca-texto sincronizado",
                base=StyleSpec(marker="active", marker_color="#FFD400", marker_opacity=.85,
                               font_size_pct=5.5, speaker_color_target="text", animation_in="fade")),
    StyleLayers(id="palavra_pulso", name="Palavra com pulso na frase",
                base=StyleSpec(active_scale=1.18, font_size_pct=5.5, karaoke="active",
                               speaker_color_target="highlight", outline_px=4)),
)})


# 0.2.0: o painel mostra só um conjunto curto e bem diferente entre si (novos modelos virão depois).
# Os demais continuam válidos: projetos antigos abrem e renderizam igual.
# 0.2.9: Torção (contorno ondulado, estilo desenho animado) virou visual pronto.
STYLE_PRESETS["torcao"] = StyleLayers(
    id="torcao", name="Torção",
    base=StyleSpec(wobble=True, wobble_ms=125, wobble_pct=1.4, uppercase=True, font_size_pct=6.5,
                   text_color="#FFF000", outline_px=0, max_lines=1, animation_in="pop",
                   speaker_color_target="none"))

# 0.2.12: dois visuais medidos quadro a quadro no vídeo de referência (CapCut "texto + animação").
STYLE_PRESETS["desfoque_ascendente"] = StyleLayers(
    id="desfoque_ascendente", name="Desfoque ascendente",
    base=StyleSpec(animation_in="blur_ascend", animation_ms=500, font_size_pct=6.0, outline_px=3.0,
                   text_color="#FFFFFF", highlight_color="#E8B91F", speaker_color_target="none"))
STYLE_PRESETS["curva"] = StyleLayers(
    id="curva", name="Curva para cima",
    base=StyleSpec(animation_in="curve_up", animation_ms=100, arch_pct=4.0, font_family="Georgia", font_size_pct=7.5,
                   text_color="#FFFFFF", outline_color="#124681", outline_px=3.0, highlight_color="#E8B91F",
                   max_lines=1, speaker_color_target="none"))

VISIBLE_PRESETS = ("classico", "karaoke_social", "caixa", "palavra_pop", "marca_texto", "torcao",
                   "desfoque_ascendente", "curva")
HIDDEN_PRESETS = {k for k in STYLE_PRESETS if k not in VISIBLE_PRESETS}


def style_catalog():
    typography = {"tipografia_2":2, "tipografia_3":3, "editorial":2}
    word = {"palavra_ativa","karaoke_preenchido","palavra_pop","palavra_salto","palavra_caixa",
            "revelar","digitacao","palavra_pulso","karaoke_social"}
    return [{"id":s.id,"name":s.name,"spec":s.base.model_dump(),
             "category":"tipografia" if s.id in typography else "marcador" if s.id=="marca_texto"
                        else "palavra" if s.id in word else "essenciais",
             "lines":typography.get(s.id,0), "tags":[s.base.animation_in,s.base.text_mode],
             "hidden": s.id in HIDDEN_PRESETS}
            for s in STYLE_PRESETS.values()]
