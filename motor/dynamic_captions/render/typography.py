"""Geometria compartilhada pela prévia e pelo ASS, com métricas das fontes locais."""
from functools import lru_cache
from dataclasses import dataclass, asdict
from PIL import ImageFont
from .style import LINE_GAP_PCT, WordStyle
from ..system_info import _font_dirs


@lru_cache(maxsize=1)
def font_index():
    from fontTools.ttLib import TTCollection, TTFont
    found=[]
    for directory in _font_dirs():
        for path in directory.rglob('*'):
            if path.suffix.lower() not in ('.ttf','.otf','.ttc'):continue
            try:
                fonts=TTCollection(str(path),lazy=True).fonts if path.suffix.lower()=='.ttc' else [TTFont(str(path),lazy=True)]
                for index,f in enumerate(fonts):
                    names=f['name']; family=names.getDebugName(16) or names.getDebugName(1)
                    sub=(names.getDebugName(17) or names.getDebugName(2) or '').casefold()
                    if family:found.append((family,str(path),index,'bold' in sub,'italic' in sub or 'oblique' in sub))
                    f.close()
            except Exception:continue
    return tuple(found)


@lru_cache(maxsize=256)
def resolve_font(family,bold=False,italic=False):
    fonts=font_index()
    matches=[f for f in fonts if f[0].casefold()==family.casefold()]
    if not matches:
        matches=[f for f in fonts if f[0].casefold()=='arial']
    if not matches:return None
    return min(matches,key=lambda f:(f[3]!=bold)+(f[4]!=italic))


@lru_cache(maxsize=512)
def font(family,size,bold=False,italic=False):
    match=resolve_font(family,bold,italic)
    if match:
        return ImageFont.truetype(match[1],max(1,round(size)),index=match[2]),match[0]
    return ImageFont.load_default(size=max(1,round(size))),family


@lru_cache(maxsize=8192)
def text_width(text,family,size,bold,italic,spacing):
    face,_=font(family,size,bold,italic)
    return face.getlength(text)+max(0,len(text)-1)*spacing


@dataclass(frozen=True)
class Token:
    word_id:str
    text:str
    x:float
    y:float
    width:float
    height:float
    font_size:float
    font_family:str
    bold:bool
    italic:bool
    text_color:str


STAGGERED=('blur_rise','curve_up','custom')  # entradas palavra a palavra / letra a letra: precisam de posições


@lru_cache(maxsize=256)
def ass_ratio(family,bold=False,italic=False):
    """Relação entre o tamanho \\fs do ASS (altura da linha) e o corpo da fonte medido pelo PIL."""
    match=resolve_font(family,bold,italic)
    if not match:return 1.0
    try:
        from fontTools.ttLib import TTCollection, TTFont
        f=TTCollection(match[1],lazy=True).fonts[match[2]] if match[1].lower().endswith('.ttc') else TTFont(match[1],lazy=True)
        os2=f['OS/2'];total=os2.usWinAscent+os2.usWinDescent
        return f['head'].unitsPerEm/total if total else 1.0
    except Exception:
        return 1.0


@lru_cache(maxsize=16384)
def _em_width(text,family,bold,italic):
    face,_=font(family,1000,bold,italic)
    return face.getlength(text)/1000


def ass_width(text,family,fs,bold,italic,spacing=0):
    """Largura em pixels que o libass dá ao texto com \\fs ``fs`` (o corpo real é fs × ass_ratio)."""
    return _em_width(text,family,bold,italic)*fs*ass_ratio(family,bold,italic)+max(0,len(text)-1)*spacing


SIMPLE_ENTRIES=('none','fade')  # as demais aberturas usam posições por palavra (rich_ass)


def needs_layout(block,spec,layers):
    return (spec.marker!='none' or spec.active_scale!=1 or spec.animation_in not in SIMPLE_ENTRIES
            or spec.wobble or spec.arch_pct>0 or any(wid in layers.words for wid in block.word_ids))


def entry_peak(spec):
    """Maior escala que a abertura atinge (reserva de largura)."""
    return {'pop':1.08,'bounce':1.06}.get(spec.animation_in,1)


def line_fit(spec,width,height):
    """Função "esta linha cabe no quadro?" com a fonte, o tamanho e o espaçamento do estilo."""
    scale=height/1080
    fs=height*spec.font_size_pct/100
    pad=max(spec.marker_padding_px if spec.marker!='none' else 0,spec.outline_px+spec.glow_px)*scale
    room=max(width*.86/max(spec.active_scale,entry_peak(spec))-pad*2,1)
    def fit(text):
        text=text.upper() if spec.uppercase else text
        return ass_width(text,spec.font_family,fs,spec.bold,spec.italic,spec.spacing_px*scale)<=room
    return fit


def layout_tokens(block,words,spec,layers,width,height,*,only_word=None):
    ordered=[words[i] for i in block.word_ids if i in words and (only_word is None or i==only_word)]
    if not ordered:return []
    if only_word is not None:
        groups=[ordered]
    else:
        by_id={w.id:w for w in ordered}
        groups=[[by_id[i] for i in line.word_ids if i in by_id] for line in block.lines]
    groups=[g for g in groups if g]
    # Quebras manuais não reordenam nem alteram o texto da fala.
    split=[]
    for group in groups:
        row=[]
        for w in group:
            if layers.words.get(w.id,WordStyle()).break_before and row:
                split.append(row);row=[]
            row.append(w)
        if row:split.append(row)
    groups=split
    base=height*spec.font_size_pct/100
    scale=height/1080
    pad=max(spec.marker_padding_px if spec.marker!='none' else 0,spec.outline_px+spec.glow_px)*scale
    peak=max(spec.active_scale,entry_peak(spec))
    available=max(width*.86/peak-pad*2,1)

    def measure(w,factor):
        patch=layers.words.get(w.id,WordStyle())
        size=base*factor*patch.font_scale
        family=patch.font_family or spec.font_family
        bold=spec.bold if patch.bold is None else patch.bold
        italic=spec.italic if patch.italic is None else patch.italic
        _,actual=font(family,size,bold,italic)
        text=w.text.upper() if spec.uppercase else w.text
        # Mesma régua do libass: \\fs é a altura da linha, não o corpo da fonte.
        tw=ass_width(text,actual,size,bold,italic,spec.spacing_px*scale*factor)
        space=ass_width(' ',actual,size,bold,italic,0)
        return {'word':w,'text':text,'size':size,'family':actual,'bold':bold,'italic':italic,
                'color':patch.text_color or spec.text_color,'width':tw,'height':size*1.12,'space':space}

    factor=1
    if only_word is None:
        wrapped=[]
        for group in groups:
            row=[];used=0
            for w in group:
                token=measure(w,1)
                gap_before=measure(row[-1],1)['space'] if row else 0
                if row and used+gap_before+token['width']>available:
                    wrapped.append(row);row=[];used=0;gap_before=0
                row.append(w);used+=gap_before+token['width']
            if row:wrapped.append(row)
        groups=wrapped
    for _ in range(5):
        measured=[[measure(w,factor) for w in row] for row in groups]
        widest=max((sum(t['width']+t['space'] for t in row)-row[-1]['space'] for row in measured),default=1)
        total=sum(max(t['height'] for t in row) for row in measured)+max(0,len(measured)-1)*height*LINE_GAP_PCT/100*factor
        fit=min(1,available/max(widest,1),height*.75/max(total,1))
        if fit>=.999:break
        factor*=fit*.98
    heights=[max(t['height'] for t in row) for row in measured]
    gap=height*LINE_GAP_PCT/100*factor
    total=sum(heights)+gap*(len(heights)-1)
    top=(height-total)/2 if spec.position=='middle' else height*spec.margin_pct/100 if spec.position=='top' else height*(1-spec.margin_pct/100)-total
    # Arco: o meio da linha sobe a flecha e as letras de lá crescem; reserva esse espaço em cima.
    arch=0
    if spec.arch_pct>0 and not spec.wobble:
        a=spec.arch_pct/100
        arch=max(a*(sum(t['width']+t['space'] for t in row)-row[-1]['space'])+5*a*rh for row,rh in zip(measured,heights))
        arch=min(arch,height*.25)
    top=max(pad+height*.02+arch,min(top,height-total-pad-height*.02))
    result=[]
    for row,rh in zip(measured,heights):
        row_width=sum(t['width']+t['space'] for t in row)-row[-1]['space']
        x=(width-row_width)/2
        for t in row:
            result.append(Token(t['word'].id,t['text'],round(x,3),round(top+(rh-t['height'])/2,3),
                                round(t['width'],3),round(t['height'],3),round(t['size'],3),
                                t['family'],t['bold'],t['italic'],t['color']))
            x+=t['width']+t['space']
        top+=rh+gap
    return result


def layout_dict(block,words,spec,layers,width,height):
    tokens=[]
    for t in layout_tokens(block,words,spec,layers,width,height):
        d=asdict(t);d['em_size']=round(t.font_size*ass_ratio(t.font_family,t.bold,t.italic),3)  # corpo real, para a prévia
        tokens.append(d)
    return {'width':width,'height':height,'tokens':tokens}
