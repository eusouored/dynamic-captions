"""Operações de edição sobre a transcrição.

Cada lote de operações gera uma nova revisão (``rev + 1``) e registros no log, com o autor
(usuário, regra automática ou modelo). O log permite desfazer, auditar e diferenciar o que
foi transcrito, revisado automaticamente e alterado por uma pessoa.

Proteção do trabalho humano:
- ``locked``: texto e tempo conferidos por uma pessoa. Regras e modelos não alteram, não apagam
  e não retemporizam a palavra; o reprocessamento a mantém.
- ``speaker_locked``: falante atribuído por uma pessoa. Regras e modelos não trocam o falante;
  no reprocessamento, a atribuição passa para as palavras novas do mesmo trecho.
- Só uma pessoa trava ou destrava. Uma operação automática que esbarra numa trava falha
  inteira (nada do lote é aplicado), para o chamador filtrar e tentar de novo.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from .ids import new_id
from .models import (
    DEFAULT_SPEAKER_COLORS,
    Agreement,
    Flag,
    Origin,
    Speaker,
    SpeakerSource,
    Timing,
    Transcript,
    Word,
)

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_REVIEW_FLAGS = {Flag.LOW_AGREEMENT, Flag.HALLUCINATION_SUSPECT}
MIN_ESTIMATED_WORD_US = 40_000
SPEAKER_CARRY_US = 500_000  # distância máxima para levar um falante travado a uma palavra nova
BOUNDARY_CARRY_US = 1_000_000  # idem para quebras e junções manuais


class Author(str, Enum):
    USER = "user"
    RULE = "rule"
    MODEL = "model"


class RevisionConflict(Exception):
    """A revisão base do cliente não é a atual (edição concorrente)."""

    def __init__(self, expected: int, actual: int):
        super().__init__(f"revisão base {expected} difere da atual {actual}")
        self.expected, self.actual = expected, actual


class OpError(ValueError):
    pass


class LockedWordError(OpError):
    """Operação automática tentou mudar algo que uma pessoa já conferiu."""

    def __init__(self, what: str, word_ids: list[str]):
        super().__init__(f"{what} travado por revisão humana: {word_ids}")
        self.word_ids = word_ids


class _Op(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ReplaceText(_Op):
    kind: Literal["replace_text"] = "replace_text"
    word_id: str
    text: str


class DeleteWords(_Op):
    kind: Literal["delete_words"] = "delete_words"
    word_ids: list[str]


class InsertWords(_Op):
    kind: Literal["insert_words"] = "insert_words"
    after_word_id: str | None  # None = no início
    text: str
    speaker_id: str | None = None


class SetSpeaker(_Op):
    kind: Literal["set_speaker"] = "set_speaker"
    word_ids: list[str]
    speaker_id: str


class AddSpeaker(_Op):
    kind: Literal["add_speaker"] = "add_speaker"
    # O id vira nome de estilo no ASS e atributo no HTML do painel: só caracteres seguros.
    speaker_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,40}$")
    label: str | None = None


class RenameSpeaker(_Op):
    kind: Literal["rename_speaker"] = "rename_speaker"
    speaker_id: str
    display_name: str | None


class SetSpeakerColor(_Op):
    kind: Literal["set_speaker_color"] = "set_speaker_color"
    speaker_id: str
    color: str


class SetSpeakerIncluded(_Op):
    kind: Literal["set_speaker_included"] = "set_speaker_included"
    speaker_id: str
    included: bool


class MergeSpeakers(_Op):
    kind: Literal["merge_speakers"] = "merge_speakers"
    source_id: str
    target_id: str


class RetimeWord(_Op):
    kind: Literal["retime_word"] = "retime_word"
    word_id: str
    start_us: int = Field(ge=0)
    end_us: int = Field(ge=0)


class LockWords(_Op):
    kind: Literal["lock_words"] = "lock_words"
    word_ids: list[str]
    locked: bool = True


class BreakAfter(_Op):
    kind: Literal["break_after"] = "break_after"
    word_id: str
    enabled: bool = True


class JoinAfter(_Op):
    kind: Literal["join_after"] = "join_after"
    word_id: str
    enabled: bool = True


class NewWord(BaseModel):
    text: str
    start_us: int = Field(ge=0)
    end_us: int = Field(ge=0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    speaker_id: str | None = None
    timing: Timing = Timing.NATIVE

    @model_validator(mode="after")
    def _check(self) -> "NewWord":
        if self.end_us < self.start_us:
            raise ValueError("fim antes do início")
        if not self.text.strip():
            raise ValueError("palavra vazia")
        return self


class ReplaceRange(_Op):
    """Resultado de reprocessar um trecho: troca só as palavras não travadas do intervalo."""

    kind: Literal["replace_range"] = "replace_range"
    start_us: int = Field(ge=0)
    end_us: int = Field(ge=0)
    words: list[NewWord]


Operation = Annotated[
    Union[
        ReplaceText,
        DeleteWords,
        InsertWords,
        SetSpeaker,
        AddSpeaker,
        RenameSpeaker,
        SetSpeakerColor,
        SetSpeakerIncluded,
        MergeSpeakers,
        RetimeWord,
        LockWords,
        BreakAfter,
        JoinAfter,
        ReplaceRange,
    ],
    Field(discriminator="kind"),
]
OPERATION_ADAPTER: TypeAdapter[list[Operation]] = TypeAdapter(list[Operation])


class OperationRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    rev: int
    seq: int
    kind: str
    author: Author
    payload: dict
    created_at: str


class _State:
    """Cópia mutável usada só durante a aplicação de um lote."""

    def __init__(self, t: Transcript):
        self.words: list[Word] = list(t.words)
        self.speakers: list[Speaker] = list(t.speakers)
        self.break_after = set(t.break_after)
        self.join_after = set(t.join_after)

    def index(self, word_id: str) -> int:
        for i, w in enumerate(self.words):
            if w.id == word_id:
                return i
        raise OpError(f"palavra inexistente: {word_id}")

    def speaker_ids(self) -> set[str]:
        return {s.id for s in self.speakers}

    def require_speaker(self, speaker_id: str) -> None:
        if speaker_id not in self.speaker_ids():
            raise OpError(f"falante inexistente: {speaker_id}")


def _human_touch(w: Word, author: Author, **update) -> Word:
    if author is Author.USER:
        update.setdefault("origin", Origin.HUMAN)
        update.setdefault("locked", True)
        update.setdefault("agreement", Agreement.CONFIRMED)
        update.setdefault("alternatives", ())
        update.setdefault("flags", frozenset(f for f in w.flags if f not in _REVIEW_FLAGS))
    elif "text" in update and update["text"] != w.text:
        update.setdefault("origin", Origin.FUSION)  # texto mudado por regra ou modelo
    return w.model_copy(update=update)


def _guard(words: list[Word], author: Author, *, speaker: bool = False) -> None:
    """Regras e modelos não mexem no que uma pessoa travou."""
    if author is Author.USER:
        return
    if speaker:
        hit = [w.id for w in words if w.speaker_locked]
        if hit:
            raise LockedWordError("falante", hit)
    else:
        hit = [w.id for w in words if w.locked]
        if hit:
            raise LockedWordError("texto", hit)


def _distance_us(us: int, w: Word) -> int:
    return max(0, w.start_us - us, us - w.end_us)


def _split_time(start: int, end: int, tokens: list[str]) -> list[tuple[int, int]]:
    total = sum(len(t) for t in tokens) or 1
    spans, cursor = [], start
    for i, tok in enumerate(tokens):
        stop = end if i == len(tokens) - 1 else cursor + (end - start) * len(tok) // total
        spans.append((cursor, max(stop, cursor)))
        cursor = stop
    return spans


def _apply_one(s: _State, op: _Op, author: Author) -> None:
    if isinstance(op, ReplaceText):
        i = s.index(op.word_id)
        w = s.words[i]
        _guard([w], author)
        tokens = op.text.split()
        if not tokens:
            _apply_one(s, DeleteWords(word_ids=[w.id]), author)
            return
        if len(tokens) == 1:
            s.words[i] = _human_touch(w, author, text=tokens[0])
            return
        spans = _split_time(w.start_us, w.end_us, tokens)
        new_words = [
            _human_touch(
                w,
                author,
                id=w.id if k == 0 else new_id(),
                text=tok,
                start_us=a,
                end_us=b,
                timing=Timing.ESTIMATED,
            )
            for k, (tok, (a, b)) in enumerate(zip(tokens, spans))
        ]
        s.words[i : i + 1] = new_words
    elif isinstance(op, DeleteWords):
        ids = set(op.word_ids)
        missing = ids - {w.id for w in s.words}
        if missing:
            raise OpError(f"palavras inexistentes: {sorted(missing)}")
        _guard([w for w in s.words if w.id in ids], author)
        s.words = [w for w in s.words if w.id not in ids]
        s.break_after -= ids
        s.join_after -= ids
    elif isinstance(op, InsertWords):
        tokens = op.text.split()
        if not tokens:
            raise OpError("texto vazio")
        if op.after_word_id is None:
            pos, prev = 0, None
        else:
            pos = s.index(op.after_word_id) + 1
            prev = s.words[pos - 1]
        nxt = s.words[pos] if pos < len(s.words) else None
        speaker = op.speaker_id or (prev.speaker_id if prev else nxt.speaker_id if nxt else None)
        if speaker is not None:
            s.require_speaker(speaker)
        start = prev.end_us if prev else max(0, (nxt.start_us if nxt else 0) - 300_000 * len(tokens))
        end = nxt.start_us if nxt else start + 300_000 * len(tokens)
        end = max(end, start + MIN_ESTIMATED_WORD_US * len(tokens))
        origin = Origin.HUMAN if author is Author.USER else Origin.FUSION
        inserted = [
            Word(
                text=tok,
                start_us=a,
                end_us=b,
                speaker_id=speaker,
                origin=origin,
                timing=Timing.ESTIMATED,
                agreement=Agreement.CONFIRMED if author is Author.USER else Agreement.SINGLE,
                locked=author is Author.USER,
                speaker_locked=author is Author.USER and op.speaker_id is not None,
            )
            for tok, (a, b) in zip(tokens, _split_time(start, end, tokens))
        ]
        s.words[pos:pos] = inserted
    elif isinstance(op, SetSpeaker):
        s.require_speaker(op.speaker_id)
        idx = [s.index(wid) for wid in op.word_ids]
        _guard([s.words[i] for i in idx], author, speaker=True)
        for i in idx:
            update: dict = {"speaker_id": op.speaker_id}
            if author is Author.USER:
                update["speaker_locked"] = True  # o reprocessamento preserva a escolha
            s.words[i] = s.words[i].model_copy(update=update)
    elif isinstance(op, AddSpeaker):
        n = 1  # menor spk_n livre (depois de juntar vozes, len()+1 pode já existir)
        while f"spk_{n}" in s.speaker_ids():
            n += 1
        sid = op.speaker_id or f"spk_{n}"
        if sid in s.speaker_ids():
            raise OpError(f"falante já existe: {sid}")
        color = DEFAULT_SPEAKER_COLORS[len(s.speakers) % len(DEFAULT_SPEAKER_COLORS)]
        s.speakers.append(
            Speaker(id=sid, label=op.label or f"Falante {n}", color=color, source=SpeakerSource.MANUAL)
        )
    elif isinstance(op, RenameSpeaker):
        s.require_speaker(op.speaker_id)
        s.speakers = [
            sp.model_copy(update={"display_name": op.display_name}) if sp.id == op.speaker_id else sp
            for sp in s.speakers
        ]
    elif isinstance(op, SetSpeakerColor):
        s.require_speaker(op.speaker_id)
        if not _HEX.match(op.color):
            raise OpError(f"cor inválida: {op.color}")
        s.speakers = [
            sp.model_copy(update={"color": op.color.upper()}) if sp.id == op.speaker_id else sp
            for sp in s.speakers
        ]
    elif isinstance(op, SetSpeakerIncluded):
        s.require_speaker(op.speaker_id)
        s.speakers = [sp.model_copy(update={"include_in_captions": op.included})
                      if sp.id == op.speaker_id else sp for sp in s.speakers]
    elif isinstance(op, MergeSpeakers):
        s.require_speaker(op.source_id)
        s.require_speaker(op.target_id)
        if op.source_id == op.target_id:
            raise OpError("falantes iguais")
        _guard([w for w in s.words if w.speaker_id == op.source_id], author, speaker=True)
        s.words = [
            w.model_copy(update={"speaker_id": op.target_id}) if w.speaker_id == op.source_id else w
            for w in s.words
        ]
        s.speakers = [sp for sp in s.speakers if sp.id != op.source_id]
    elif isinstance(op, RetimeWord):
        if op.end_us < op.start_us:
            raise OpError("fim antes do início")
        i = s.index(op.word_id)
        _guard([s.words[i]], author)
        s.words[i] = _human_touch(
            s.words[i],
            author,
            start_us=op.start_us,
            end_us=op.end_us,
            timing=Timing.MANUAL if author is Author.USER else Timing.ALIGNED,
        )
    elif isinstance(op, LockWords):
        if author is not Author.USER:
            raise OpError("só uma pessoa trava ou destrava palavras")
        for wid in op.word_ids:
            i = s.index(wid)
            s.words[i] = s.words[i].model_copy(update={"locked": op.locked, "speaker_locked": op.locked})
    elif isinstance(op, BreakAfter):
        s.index(op.word_id)
        if op.enabled:
            s.break_after.add(op.word_id)
            s.join_after.discard(op.word_id)
        else:
            s.break_after.discard(op.word_id)
    elif isinstance(op, JoinAfter):
        s.index(op.word_id)
        if op.enabled:
            s.join_after.add(op.word_id)
            s.break_after.discard(op.word_id)
        else:
            s.join_after.discard(op.word_id)
    elif isinstance(op, ReplaceRange):
        _replace_range(s, op)
    else:  # pragma: no cover - a união discriminada impede
        raise OpError(f"operação desconhecida: {op!r}")


def _replace_range(s: _State, op: ReplaceRange) -> None:
    """Troca as palavras não travadas do intervalo pelo resultado do reprocessamento.

    Tudo é validado antes de qualquer mudança. Falantes travados e quebras/junções manuais
    das palavras removidas passam para as palavras novas mais próximas no tempo.
    """
    if op.end_us <= op.start_us:
        raise OpError("intervalo inválido")
    outside = [nw for nw in op.words if nw.start_us < op.start_us or nw.end_us > op.end_us]
    if outside:
        raise OpError(
            f"{len(outside)} palavra(s) fora do intervalo {op.start_us}-{op.end_us} µs "
            f"(primeira: {outside[0].text!r} em {outside[0].start_us}-{outside[0].end_us} µs)"
        )
    for nw in op.words:
        if nw.speaker_id is not None:
            s.require_speaker(nw.speaker_id)

    def inside(w: Word) -> bool:
        return op.start_us <= w.mid_us < op.end_us

    removed = [w for w in s.words if inside(w) and not w.locked]
    kept = [w for w in s.words if not (inside(w) and not w.locked)]
    locked_inside = [w for w in kept if inside(w)]
    speaker_anchors = [w for w in removed if w.speaker_locked]

    fresh: list[Word] = []
    for nw in op.words:
        mid = (nw.start_us + nw.end_us) // 2
        if any(lw.start_us <= mid < lw.end_us for lw in locked_inside):
            continue  # palavra já corrigida por uma pessoa; não duplicar
        speaker_id, speaker_locked = nw.speaker_id, False
        if speaker_anchors:
            anchor = min(speaker_anchors, key=lambda w: _distance_us(mid, w))
            if _distance_us(mid, anchor) <= SPEAKER_CARRY_US:
                speaker_id, speaker_locked = anchor.speaker_id, True
        fresh.append(
            Word(
                text=nw.text.strip(),
                start_us=nw.start_us,
                end_us=nw.end_us,
                confidence=nw.confidence,
                speaker_id=speaker_id,
                speaker_locked=speaker_locked,
                timing=nw.timing,
                origin=Origin.ASR,
            )
        )

    # Quebras e junções manuais: levar para a palavra nova (ou travada) cujo fim é mais próximo.
    targets = fresh + locked_inside
    for marks, other in ((s.break_after, s.join_after), (s.join_after, s.break_after)):
        for w in removed:
            if w.id not in marks:
                continue
            marks.discard(w.id)
            if not targets:
                continue
            best = min(targets, key=lambda t: abs(t.end_us - w.end_us))
            if abs(best.end_us - w.end_us) <= BOUNDARY_CARRY_US and best.id not in other:
                marks.add(best.id)
    s.words = kept + fresh


def apply_ops(
    t: Transcript, ops: list[_Op], *, base_rev: int, author: Author = Author.USER
) -> tuple[Transcript, list[OperationRecord]]:
    if base_rev != t.rev:
        raise RevisionConflict(base_rev, t.rev)
    state = _State(t)
    for op in ops:
        _apply_one(state, op, author)
    state.words.sort(key=lambda w: w.start_us)  # ordenação estável
    new_rev = t.rev + 1
    now = datetime.now(timezone.utc).isoformat()
    records = [
        OperationRecord(rev=new_rev, seq=i, kind=op.kind, author=author, payload=op.model_dump(mode="json"), created_at=now)
        for i, op in enumerate(ops)
    ]
    new_t = t.model_copy(
        update={
            "rev": new_rev,
            "words": tuple(state.words),
            "speakers": tuple(state.speakers),
            "break_after": frozenset(state.break_after),
            "join_after": frozenset(state.join_after),
        }
    )
    # Revalida invariantes (ordem e IDs únicos).
    return Transcript.model_validate(new_t.model_dump()), records


def parse_ops(payload: list[dict]) -> list[_Op]:
    return OPERATION_ADAPTER.validate_python(payload)
