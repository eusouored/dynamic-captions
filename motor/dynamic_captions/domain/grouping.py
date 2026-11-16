"""Agrupamento de palavras em blocos de legenda.

Critérios, em ordem:
1. Quebras obrigatórias: troca de falante, quebra manual, pausa longa, duração ou palavras máximas.
2. Capacidade: o bloco precisa caber em ``max_lines`` linhas de ``max_chars_per_line``.
   Ao estourar, o corte volta para o melhor ponto (pontuação, sem terminar em palavra fraca).
3. Preferência por pontuação: um bloco razoavelmente cheio fecha ao fim de uma frase.
4. Tempo: blocos curtos são estendidos até a duração mínima e o limite de leitura (CPS),
   sem invadir o próximo bloco.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from .models import CaptionBlock, CaptionLine, Transcript, Word
from .presets import ReadingPreset

PUNCT_END = (".", "!", "?", "…")
PUNCT_SOFT = (",", ";", ":")
# Palavras que soam mal no fim de uma linha em português.
WEAK_LINE_END = frozenset(
    "a o as os e é de do da dos das em no na nos nas um uma uns umas que para pra "
    "com por se ao à aos às mas ou nem seu sua".split()
)


def _clean(word: str) -> str:
    return word.strip().lower().strip(".,;:!?…\"'()")


def _text(words: Sequence[Word]) -> str:
    return " ".join(w.text.strip() for w in words if w.text.strip())


Fit = Callable[[str], bool]  # "esta linha cabe?" (medida real da fonte); None = só contagem de caracteres


def _break_lines(words: Sequence[Word], max_chars: int, max_lines: int, fit: Fit | None = None) -> list[list[Word]] | None:
    """Distribui as palavras em linhas. Retorna None se não couber."""
    if not words:
        return []

    def ok(ws: Sequence[Word]) -> bool:
        text = _text(ws)
        return len(text) <= max_chars and (fit is None or fit(text))

    if ok(words):
        return [list(words)]
    if max_lines >= 2:
        best: tuple[float, int] | None = None
        for k in range(1, len(words)):
            l1, l2 = len(_text(words[:k])), len(_text(words[k:]))
            if not ok(words[:k]) or not ok(words[k:]):
                continue
            score = float(max(l1, l2))
            tail = words[k - 1].text.strip()
            if _clean(tail) in WEAK_LINE_END:
                score += 6
            if tail.endswith(PUNCT_END + PUNCT_SOFT):
                score -= 4
            if l1 > l2:  # leve preferência por linha de cima mais curta
                score += 1
            if best is None or score < best[0]:
                best = (score, k)
        if best is not None:
            k = best[1]
            return [list(words[:k]), list(words[k:])]
    if max_lines >= 3:
        lines: list[list[Word]] = [[]]
        for w in words:
            candidate = lines[-1] + [w]
            if ok(candidate):
                lines[-1] = candidate
            else:
                lines.append([w])
        if len(lines) <= max_lines and all(ok(line) for line in lines):
            return lines
    return None


def _fits(words: Sequence[Word], preset: ReadingPreset, fit: Fit | None = None) -> bool:
    return _break_lines(words, preset.max_chars_per_line, preset.max_lines, fit) is not None


def _must_close(cur: list[Word], nxt: Word, t: Transcript, p: ReadingPreset) -> bool:
    last = cur[-1]
    if p.split_on_speaker_change and nxt.speaker_id != last.speaker_id:
        return True
    if last.id in t.break_after:
        return True
    if last.id in t.join_after:
        return False
    # As palavras vêm ordenadas pelo início; o fim do grupo é o maior fim, não o da última.
    if nxt.start_us - max(w.end_us for w in cur) >= p.pause_split_us:
        return True
    if nxt.end_us - cur[0].start_us > p.max_duration_us:
        return True
    if p.max_words is not None and len(cur) >= p.max_words:
        return True
    return False


def _soft_close(cur: list[Word], t: Transcript, p: ReadingPreset) -> bool:
    last = cur[-1]
    if not p.prefer_punctuation or last.id in t.join_after:
        return False
    tail = last.text.strip()
    fill = len(_text(cur)) / (p.max_chars_per_line * p.max_lines)
    if tail.endswith(PUNCT_END):
        return p.max_words is not None or fill >= 0.4
    if tail.endswith(PUNCT_SOFT):
        return fill >= 0.7
    return False


def _split_point(cur: list[Word], t: Transcript, p: ReadingPreset, fit: Fit | None = None) -> int:
    """Melhor índice k para fechar ``cur[:k]`` e levar ``cur[k:]`` ao próximo bloco."""
    best_k, best_score = len(cur) - 1, float("-inf")
    for k in range(1, len(cur)):
        head = cur[:k]
        if cur[k - 1].id in t.join_after or not _fits(head, p, fit):
            continue
        tail = cur[k - 1].text.strip()
        score = k / len(cur)
        if tail.endswith(PUNCT_END):
            score += 3
        elif tail.endswith(PUNCT_SOFT):
            score += 2
        if _clean(tail) in WEAK_LINE_END:
            score -= 2
        if score > best_score:
            best_k, best_score = k, score
    return max(best_k, 1)


def group_words(t: Transcript, p: ReadingPreset, fit: Fit | None = None) -> list[list[Word]]:
    groups: list[list[Word]] = []
    cur: list[Word] = []
    for w in t.words:
        if not w.text.strip():
            continue
        if cur and _must_close(cur, w, t, p):
            groups.append(cur)
            cur = []
        cur.append(w)
        if not _fits(cur, p, fit):
            if len(cur) == 1:  # palavra sozinha maior que a linha: fica isolada
                groups.append(cur)
                cur = []
                continue
            k = _split_point(cur, t, p, fit)
            groups.append(cur[:k])
            cur = cur[k:]
        elif _soft_close(cur, t, p):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


MIN_TAIL_US = 40_000  # bloco que sobraria com menos de ~1 quadro antes do fim é descartado


def build_blocks(t: Transcript, p: ReadingPreset, *, fit: Fit | None = None, limit_us: int | None = None) -> list[CaptionBlock]:
    """``fit``: a linha cabe no quadro com a fonte do estilo (texto que não cabe vira um novo bloco).
    ``limit_us``: fim do trecho legendado; nenhum bloco passa dele (a camada não fica maior que a sequência)."""
    groups = group_words(t, p, fit)
    blocks: list[CaptionBlock] = []
    for i, g in enumerate(groups):
        if limit_us is not None and g[0].start_us >= limit_us - MIN_TAIL_US:
            break  # fala depois do fim (trecho cortado da sequência): não vira legenda
        lines = _break_lines(g, p.max_chars_per_line, p.max_lines, fit) or [g]
        chars = sum(len(_text(line)) for line in lines)
        start, end = g[0].start_us, max(w.end_us for w in g)
        next_start = groups[i + 1][0].start_us if i + 1 < len(groups) else None
        needed = max(p.min_duration_us, int(chars / p.max_cps * 1_000_000))
        if end - start < needed:
            target = start + needed
            if next_start is not None:
                target = min(target, next_start - p.min_gap_us)
            end = max(end, target)
        if next_start is not None and 0 < next_start - end < p.chain_gap_us:
            end = max(end, next_start - p.min_gap_us)  # encadeia blocos próximos
        if limit_us is not None and end > limit_us > start:
            end = limit_us
        flags = set()
        duration = max(end - start, 1)
        if chars / (duration / 1_000_000) > p.max_cps + 1e-9:
            flags.add("reading_speed")
        if duration < p.min_duration_us:
            flags.add("too_short")
        if duration > p.max_duration_us:
            flags.add("too_long")
        blocks.append(
            CaptionBlock(
                id=f"blk_{g[0].id}",
                word_ids=tuple(w.id for w in g),
                lines=tuple(
                    CaptionLine(word_ids=tuple(w.id for w in line), text=_text(line)) for line in lines
                ),
                start_us=start,
                end_us=end,
                speaker_id=g[0].speaker_id,
                flags=frozenset(flags),
            )
        )
    return blocks
