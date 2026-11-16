"""Texto em posições estáveis e formas vetoriais, sem dependência de mídia externa."""
from .typography import layout_tokens, ass_width
from . import wobble
from ..domain.timecode import format_ass_time
from .style import readable_on


def _complex_script(text):
    """Texto que não pode ser dividido letra a letra: escrita da direita, ligaduras, emoji compostos."""
    import unicodedata
    for ch in text:
        o=ord(ch)
        if unicodedata.bidirectional(ch) in ('R','AL') or unicodedata.category(ch) in ('Mn','Me','Cf','Cs') \
           or 0x1F000<=o<=0x1FFFF or 0xFE00<=o<=0xFE0F or 0x2600<=o<=0x27BF:
            return True
    return False


def rounded_rect(x,y,w,h,r):
    r=min(max(r,0),w/2,h/2);k=.55228475
    return (f'm {x+r:g} {y:g} l {x+w-r:g} {y:g} '
            f'b {x+w-r+r*k:g} {y:g} {x+w:g} {y+r-r*k:g} {x+w:g} {y+r:g} '
            f'l {x+w:g} {y+h-r:g} b {x+w:g} {y+h-r+r*k:g} {x+w-r+r*k:g} {y+h:g} {x+w-r:g} {y+h:g} '
            f'l {x+r:g} {y+h:g} b {x+r-r*k:g} {y+h:g} {x:g} {y+h-r+r*k:g} {x:g} {y+h-r:g} '
            f'l {x:g} {y+r:g} b {x:g} {y+r-r*k:g} {x+r-r*k:g} {y:g} {x+r:g} {y:g}')


PIECE_US=40_000      # passo das aberturas: a curva é desenhada em trechos lineares deste tamanho
MIN_ACTIVE_US=100_000  # toda palavra fica ativa (marca-texto, destaque) pelo menos isto: ~3 quadros


def _ease(fx,p):
    """Avanço 0→1 da abertura; "back" passa do ponto e assenta."""
    p=min(1,max(0,p))
    if fx['ease']=='back':
        k=fx['over'];q=p-1
        return 1+(k+1)*q**3+k*q**2
    if fx['ease']=='quad':return 1-(1-p)**2
    return 1-(1-p)**3


def _state(fx,p,rise_px,blur_px):
    """Estado de um item no avanço p: (escala, deslocamento y, opacidade, desfoque, giro, avanço)."""
    if fx['instant']:return 1,0,1,0,0,1
    e=_ease(fx,p)
    return (fx['scale']+(1-fx['scale'])*e, rise_px*(1-e), min(1,p/.6) if fx['fade'] else 1,
            blur_px*(1-min(1,max(0,p)))**2, fx['rotate']*(1-e), e)


def _mix(c0,c1,k):
    """Cor entre c0 e c1 (#RRGGBB), k de 0 a 1."""
    k=min(1,max(0,k))
    a=[int(c0[i:i+2],16) for i in (1,3,5)];b=[int(c1[i:i+2],16) for i in (1,3,5)]
    return '#'+''.join(f'{round(x+(y-x)*k):02X}' for x,y in zip(a,b))


def _ranks(n,order,seed):
    """Posição de cada item na fila de entrada: na ordem, de trás para a frente ou sorteada."""
    if order=='reverse':return [n-1-i for i in range(n)]
    if order=='random':
        import random
        idx=list(range(n));random.Random(seed).shuffle(idx)
        rank=[0]*n
        for pos,i in enumerate(idx):rank[i]=pos
        return rank
    return list(range(n))


def active_windows(block,ordered):
    """Quando cada palavra é "a da vez": do seu início até o início da próxima (sem buracos nas
    pausas), com duração mínima para palavras curtinhas ("a", "é", "de") não serem puladas."""
    ws=sorted(ordered,key=lambda w:(w.start_us,w.end_us))
    starts=[];prev=None
    for w in ws:
        t=max(w.start_us,block.start_us)
        if prev is not None:t=max(t,prev+MIN_ACTIVE_US)
        starts.append(t);prev=t
    # Bloco curto demais para o mínimo de todas: divide o tempo igualmente.
    if starts and starts[-1]>=block.end_us-MIN_ACTIVE_US//2 and len(ws)>1:
        span=(block.end_us-block.start_us)/len(ws)
        starts=[round(block.start_us+i*span) for i in range(len(ws))]
    return {w.id:(starts[i],starts[i+1] if i+1<len(ws) else max(block.end_us,starts[i]+1)) for i,w in enumerate(ws)}


ARCH_BULGE=5.0  # letras do meio crescem 5× o arco (arco de 4% → 20% maiores, como no CapCut)


def _row(t):
    """Linha do token: pelo centro vertical (uma palavra com tamanho próprio tem outro y, o mesmo centro)."""
    return round(t.y+t.height/2)


def _arch_lines(tokens,arch_pct):
    """Por linha: centro, meia largura e flecha do arco (∩)."""
    lines={}
    for t in tokens:lines.setdefault(_row(t),[]).append(t)
    out={}
    for y,ts in lines.items():
        left=min(t.x for t in ts);right=max(t.x+t.width for t in ts)
        hw=max(1.0,(right-left)/2)
        out[y]=((left+right)/2,hw,arch_pct/100*(right-left))
    return out


def _arch_at(arc,x):
    """Deslocamento vertical, giro (graus, ASS: positivo = anti-horário) e escala de uma letra em ``x``."""
    import math
    cx,hw,sag=arc
    u=max(-1.0,min(1.0,(x-cx)/hw))
    dy=-sag*(1-u*u)
    rot=-math.degrees(math.atan(2*sag*u/hw))
    bump=1+ARCH_BULGE*sag/(2*hw)*(1-u*u)
    return dy,rot,bump


def _pieces(start,end,reveal,anim_us,instant=False):
    """Trechos de [start,end) em que o item existe; dentro da janela da abertura, em passos curtos."""
    start,end,reveal=round(start),round(end),round(reveal)
    a=max(start,reveal)
    if a>=end:return
    stop=reveal+(0 if instant else round(anim_us))
    while a<min(end,stop):
        b=min(end,stop,a+PIECE_US)
        yield a,b
        a=b
    if a<end:yield a,end


def events(block,words,spec,layers,name,width,height,offset):
    from .ass import ass_color,_escape
    from .style import cascade,entry_ms
    scale=height/1080
    tokens=layout_tokens(block,words,spec,layers,width,height)
    ordered=[words[t.word_id] for t in tokens]
    duration=max(1,block.end_us-block.start_us)
    anim_us=min(entry_ms(spec)*1000,duration//2 if spec.animation_in not in ('none','fade') else duration//3)
    win=active_windows(block,ordered)
    bounds={block.start_us,block.end_us}
    for w in ordered:
        bounds.update(t for t in (*win[w.id],w.end_us) if block.start_us<t<block.end_us)
    fade_only=spec.animation_in=='fade' and anim_us
    if fade_only:
        bounds.update((block.start_us+anim_us,block.end_us-anim_us))
    bounds=sorted(bounds)
    word_mode=spec.text_mode=='word'

    def emit(layer,start,end,tags,text):
        a=max(start-offset,0);b=max(end-offset,0)
        if b>a:return f'Dialogue: {layer},{format_ass_time(a)},{format_ass_time(b)},{name},,0,0,0,,{{{tags}}}{text}'
        return None

    def opacity(t):
        if not fade_only:return 1
        return max(0,min(1,(t-block.start_us)/anim_us,(block.end_us-t)/anim_us))

    def alpha_tags(start,end,multiplier=1,o0=None,o1=None):
        o0=opacity(start) if o0 is None else o0;o1=opacity(end) if o1 is None else o1
        a=round(255*(1-o0*multiplier));b=round(255*(1-o1*multiplier))
        tag=f'\\alpha&H{a:02X}&'
        if a!=b:tag+=f'\\t(0,{max(1,round((end-start)/1000))},\\alpha&H{b:02X}&)'
        return tag

    # Aberturas (ver style.cascade): frase, palavra ou letra partem de um estado inicial e chegam ao
    # final seguindo uma curva; cada item tem o seu instante de entrada.
    fx=cascade(spec) if anim_us else None
    rise_px=height*fx['rise']/100 if fx else 0
    shift_px=height*fx['shift']/100 if fx else 0
    blur_px=fx['blur']*scale if fx else 0

    def cadence(n_units,span):
        """Intervalo entre a entrada de um item e a do seguinte."""
        if not fx or n_units<=1:return 0
        if fx['instant']:return min(max(25_000,min(80_000,anim_us/6)),span*.6/(n_units-1))
        return min(anim_us*fx['stagger'],span*.5/(n_units-1))

    n_units=(1 if not fx or fx['unit']=='phrase' else len(tokens) if fx['unit']=='word' else sum(len(t.text) for t in tokens))
    step=cadence(n_units,duration)
    order={t.word_id:i for i,t in enumerate(tokens)}
    first_letter={};count=0
    for t in tokens:
        first_letter[t.word_id]=count;count+=len(t.text)
    seed=block.id
    word_rank=_ranks(len(tokens),fx['order'] if fx else 'forward',seed)
    letter_rank=_ranks(count,fx['order'] if fx else 'forward',seed)

    out=[]
    for start,end in zip(bounds,bounds[1:]):
        mid=(start+end)//2
        active=next((w for w in ordered if win[w.id][0]<=mid<win[w.id][1]),None)
        current=layout_tokens(block,words,spec,layers,width,height,only_word=active.id) if word_mode and active else tokens
        if word_mode and active is None:continue
        if not current:continue
        bx=min(t.x for t in current);by=min(t.y for t in current)
        bw=max(t.x+t.width for t in current)-bx;bh=max(t.y+t.height for t in current)-by
        gx=bx+bw/2;gy=by+bh/2   # centro do conjunto: a frase cresce/encolhe em torno dele
        arcs=_arch_lines(current,spec.arch_pct) if spec.arch_pct>0 and not spec.wobble else {}
        if spec.box:
            pad=max(3,spec.outline_px)*scale
            if arcs:  # a caixa abraça o arco: o meio sobe (flecha) e cresce; as pontas giram
                lift=max(sag+max(t.font_size for t in current)*ARCH_BULGE*sag/(2*hw) for _,hw,sag in arcs.values())
                tilt=max(t.font_size for t in current)*.35*max(2*sag/hw for _,hw,sag in arcs.values())
                side=max(t.font_size for t in current)*.25   # letras das pontas giram para fora
                by-=lift;bh+=lift+tilt;bx-=side;bw+=2*side
            tags='\\an7\\pos(0,0)\\bord0\\shad0\\p1'+f'\\1c{ass_color(spec.box_color)}&'+alpha_tags(start,end,spec.box_opacity)
            out.append(emit(0,start,end,tags,rounded_rect(bx-pad,by-pad,bw+2*pad,bh+2*pad,spec.marker_radius_px*scale)))
        for t in current:
            word=words[t.word_id]
            w0,w1=win[word.id]
            if spec.text_mode=='progressive' and w0>=end:continue
            is_active=active is not None and active.id==t.word_id
            if spec.marker=='active' and is_active:
                pad=spec.marker_padding_px*scale
                tags='\\an7\\pos(0,0)\\bord0\\shad0\\p1'+f'\\1c{ass_color(spec.marker_color)}&'+alpha_tags(start,end,spec.marker_opacity)
                mx,my,mw,mh=t.x-pad,t.y-pad,t.width+2*pad,t.height+2*pad
                marc=arcs.get(_row(t))
                if marc:  # no arco, o marca-texto acompanha a palavra: sobe, cresce e gira com ela
                    dy,rot,bump=_arch_at(marc,t.x+t.width/2)
                    my+=dy-(bump-1)*t.height;mh*=bump
                    tags+=f'\\org({t.x+t.width/2:g},{t.y+t.height+dy:g})\\frz{rot:g}'
                out.append(emit(1,start,end,tags,rounded_rect(mx,my,mw,mh,spec.marker_radius_px*scale)))
            color=t.text_color
            if spec.karaoke=='active' and is_active:color=spec.highlight_color
            marked=spec.marker=='active' and is_active
            if marked:color=readable_on(color,spec.marker_color)  # contraste de leitura sobre o marca-texto
            if spec.karaoke=='color' and w0<=mid:color=spec.highlight_color
            cx=t.x+t.width/2;cy=t.y+t.height/2
            look=(f'\\fn{t.font_family}\\fs{t.font_size:g}'
                  f'\\b{1 if t.bold else 0}\\i{1 if t.italic else 0}\\fsp{spec.spacing_px*scale:g}'
                  f'\\bord{0 if spec.box or marked else spec.outline_px*scale:g}\\shad{spec.shadow_px*scale:g}\\1c{ass_color(color)}&')  # na Caixa, contorno = margem da caixa

            def pulse(time):
                if not is_active or spec.active_scale==1:return 1
                p=min(1,max(0,(time-w0)/max(1,min(150000,(w1-w0)/2))))
                return spec.active_scale-(spec.active_scale-1)*p

            def motion(a,b,reveal,x,y,anchor,pivot,rot=0.0,bump=1.0):
                """Tags de posição/escala/opacidade de um item no trecho [a,b). ``rot``/``bump``: arco."""
                dt=max(1,round((b-a)/1000))
                if fx:
                    sa,ya,oa,ba,ra,ea=_state(fx,(a-reveal)/max(anim_us,1),rise_px,blur_px)
                    sb,yb,ob,bb,rb,eb=_state(fx,(b-reveal)/max(anim_us,1),rise_px,blur_px)
                else:
                    sa=sb=1;ya=yb=ba=bb=ra=rb=0;oa=ob=None;ea=eb=1
                px,py=pivot
                xa=px+(x-px)*sa+shift_px*(1-ea);xb=px+(x-px)*sb+shift_px*(1-eb)
                y0=py+(y-py)*sa+ya;y1=py+(y-py)*sb+yb
                tags=f'\\{anchor}'+(f'\\pos({xa:g},{y0:g})' if abs(xa-xb)<.05 and abs(y0-y1)<.05 else f'\\move({xa:g},{y0:g},{xb:g},{y1:g},0,{dt})')
                if ra or rb:tags+=f'\\org({px:g},{py:g})'
                ra+=rot;rb+=rot   # o giro do arco é em volta do próprio ponto (base da letra)
                tags+=look+alpha_tags(a,b,1,oa,ob)
                s0=sa*pulse(a)*100*bump;s1=sb*pulse(b)*100*bump
                tags+=f'\\fscx{s0:g}\\fscy{s0:g}'
                if abs(s0-s1)>.01:tags+=f'\\t(0,{dt},\\fscx{s1:g}\\fscy{s1:g})'
                if ba or bb:tags+=f'\\blur{ba:g}'+(f'\\t(0,{dt},\\blur{bb:g})' if abs(ba-bb)>.01 else '')
                if ra or rb:tags+=f'\\frz{ra:g}'+(f'\\t(0,{dt},\\frz{rb:g})' if abs(ra-rb)>.01 else '')
                if fx and fx['tint'] and ea<1:
                    # Entra na cor de destaque e chega à cor do texto.
                    c0=_mix(spec.highlight_color,color,ea);c1=_mix(spec.highlight_color,color,eb)
                    tags+=f'\\1c{ass_color(c0)}&'+(f'\\t(0,{dt},\\1c{ass_color(c1)}&)' if c0!=c1 else '')
                return tags

            def glow_of(tags):
                return tags+f'\\blur{spec.glow_px*scale:g}\\bord{(spec.glow_px+spec.outline_px)*scale:g}'

            def fill_clip(a,b):
                p0=max(0,min(1,(a-word.start_us)/max(word.duration_us,1)))
                p1=max(0,min(1,(b-word.start_us)/max(word.duration_us,1)))
                if p1<=0:return None
                clip=f'\\clip({round(t.x-2)},{round(t.y-2)},{round(t.x+t.width*p0)},{round(t.y+t.height+2)})'
                if p1!=p0:clip+=f'\\t(0,{max(1,round((b-a)/1000))},\\clip({round(t.x-2)},{round(t.y-2)},{round(t.x+t.width*p1)},{round(t.y+t.height+2)}))'
                return f'\\1c{ass_color(spec.highlight_color)}&'+clip

            def whole(a,b,reveal,pivot):
                """A palavra inteira no trecho [a,b): texto comum ou forma torcida."""
                tags=motion(a,b,reveal,cx,cy,'an5',pivot)
                if spec.wobble:
                    shapes=[wobble.drawing(t.text,t.font_family,t.bold,t.italic,t.font_size,spec.spacing_px*scale,v,t.x,t.y,spec.wobble_pct/100)
                            for v in range(wobble.VARIANTS)]
                    if shapes[0]:
                        shape_tags=tags.replace('\\an5','\\an7',1)+'\\p1'
                        for c,d,v in wobble.slices(a,b,spec.wobble_ms*1000):
                            local=wobble.shift_tags(shape_tags,round((c-a)/1000))
                            if spec.glow_px:out.append(emit(2,c,d,glow_of(local),shapes[v]))
                            out.append(emit(3,c,d,local,shapes[v]))
                            if spec.karaoke=='fill':
                                clip=fill_clip(c,d)
                                if clip:out.append(emit(4,c,d,local+clip,shapes[v]))
                        return
                text=_escape(t.text)
                if spec.glow_px:out.append(emit(2,a,b,glow_of(tags),text))
                out.append(emit(3,a,b,tags,text))
                if spec.karaoke=='fill':
                    clip=fill_clip(a,b)
                    if clip:out.append(emit(4,a,b,tags+clip,text))

            arc=arcs.get(_row(t))
            if arc and not _complex_script(t.text) and len(t.text)>0:
                # Arco: cada letra no seu lugar da curva, o tempo todo (com ou sem abertura).
                lcolor=color
                if spec.karaoke=='fill' and w0<=mid:lcolor=spec.highlight_color
                look_word=look
                look=look.replace(f'\\1c{ass_color(color)}&',f'\\1c{ass_color(lcolor)}&')
                base_t=w0 if word_mode else block.start_us
                unit=fx['unit'] if fx else 'phrase'
                sp=spec.spacing_px*scale
                left=cx-ass_width(t.text,t.font_family,t.font_size,t.bold,t.italic,sp)/2
                bottom=cy+t.font_size/2
                if fx and unit=='letter':
                    st=cadence(len(t.text),w1-w0) if word_mode else step
                    g0=first_letter.get(t.word_id,0)
                    ranks=_ranks(len(t.text),fx['order'],seed+t.word_id) if word_mode else [letter_rank[g0+i] for i in range(len(t.text))]
                else:
                    k=0 if not fx or unit=='phrase' or word_mode else word_rank[order.get(t.word_id,0)]
                    ranks=None;word_reveal=round(base_t+k*step) if fx else -10**12
                for i,ch in enumerate(t.text):
                    if ch.isspace():continue
                    x=left+ass_width(t.text[:i],t.font_family,t.font_size,t.bold,t.italic,sp)+(sp if i else 0)+ass_width(ch,t.font_family,t.font_size,t.bold,t.italic,0)/2
                    dy,rot,bump=_arch_at(arc,x)
                    if ranks is not None:
                        reveal=base_t+ranks[i]*st;pivot=(x,bottom+dy)
                    else:
                        reveal=word_reveal;pivot=(gx,gy) if unit=='phrase' else (cx,cy)
                    for a,b in (_pieces(start,end,round(reveal),anim_us,fx['instant']) if fx else [(start,end)]):
                        tags=motion(a,b,reveal,x,bottom+dy,'an2',pivot,rot,bump)
                        if spec.glow_px:out.append(emit(2,a,b,glow_of(tags),_escape(ch)))
                        out.append(emit(3,a,b,tags,_escape(ch)))
                look=look_word
                continue
            if not fx:
                whole(start,end,block.start_us,(cx,cy))
                continue
            # Instante de entrada deste item. No modo "uma palavra por vez", cada palavra abre na sua vez.
            base_t=w0 if word_mode else block.start_us
            unit=fx['unit']
            if unit=='letter' and not _complex_script(t.text) and len(t.text)>0:
                # Árabe/hebraico e emoji compostos não se dividem: entram como palavra (abaixo).
                st=cadence(len(t.text),w1-w0) if word_mode else step
                g0=first_letter.get(t.word_id,0)
                ranks=_ranks(len(t.text),fx['order'],seed+t.word_id) if word_mode else [letter_rank[g0+i] for i in range(len(t.text))]
                last=base_t+max(ranks)*st
                done=round(last+(0 if fx['instant'] else anim_us))   # daqui em diante a palavra é uma peça só
                if start<done:
                    sp=spec.spacing_px*scale
                    left=cx-ass_width(t.text,t.font_family,t.font_size,t.bold,t.italic,sp)/2
                    bottom=cy+t.font_size/2
                    stop=min(end,done)
                    for i,ch in enumerate(t.text):
                        if ch.isspace():continue
                        reveal=base_t+ranks[i]*st
                        x=left+ass_width(t.text[:i],t.font_family,t.font_size,t.bold,t.italic,sp)+(sp if i else 0)+ass_width(ch,t.font_family,t.font_size,t.bold,t.italic,0)/2
                        for a,b in _pieces(start,stop,round(reveal),anim_us,fx['instant']):
                            tags=motion(a,b,reveal,x,bottom,'an2',(x,bottom))
                            if spec.glow_px:out.append(emit(2,a,b,glow_of(tags),_escape(ch)))
                            out.append(emit(3,a,b,tags,_escape(ch)))
                if end>done:
                    whole(max(start,round(done)),end,-10**12,(cx,cy))
                continue
            k=0 if unit=='phrase' or word_mode else word_rank[order.get(t.word_id,0)] if unit=='word' else letter_rank[first_letter.get(t.word_id,0)]
            reveal=round(base_t+k*step)
            pivot=(gx,gy) if unit=='phrase' else (cx,cy)
            for a,b in _pieces(start,end,reveal,anim_us,fx['instant']):
                whole(a,b,reveal,pivot)
    return [event for event in out if event]
