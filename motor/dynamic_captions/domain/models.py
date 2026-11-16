"""Esquema de dados por palavra e falante.

Regras:
- Tempo sempre em microssegundos inteiros (``*_us``), relativo ao início da mídia ou da sequência.
- O ID de uma palavra nunca muda quando o texto é corrigido.
- Blocos de legenda são derivados das palavras; só as restrições manuais (quebrar/juntar) são guardadas.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .ids import new_id


class Agreement(str, Enum):
    CONFIRMED = "confirmed"  # os modelos concordam
    DISPUTED = "disputed"  # os modelos discordam
    SINGLE = "single"  # só um modelo rodou


class Origin(str, Enum):
    ASR = "asr"
    FUSION = "fusion"
    HUMAN = "human"


class Timing(str, Enum):
    ALIGNED = "aligned"  # alinhador forçado
    NATIVE = "native"  # timestamps do próprio ASR
    ESTIMATED = "estimated"  # interpolado (ex.: palavra inserida sem realinhar)
    MANUAL = "manual"  # ajustado pelo usuário


class Flag(str, Enum):
    OVERLAP = "overlap"
    HALLUCINATION_SUSPECT = "hallucination_suspect"
    DICTIONARY_TERM = "dictionary_term"
    LOW_AGREEMENT = "low_agreement"


class Word(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=new_id)
    text: str
    start_us: int = Field(ge=0)
    end_us: int = Field(ge=0)
    speaker_id: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    agreement: Agreement = Agreement.SINGLE
    origin: Origin = Origin.ASR
    timing: Timing = Timing.NATIVE
    locked: bool = False  # texto e tempo conferidos por uma pessoa
    speaker_locked: bool = False  # falante atribuído ou conferido por uma pessoa
    flags: frozenset[Flag] = frozenset()
    alternatives: tuple[str, ...] = ()  # hipóteses de outros modelos para revisão

    @model_validator(mode="after")
    def _check_times(self) -> "Word":
        if self.end_us < self.start_us:
            raise ValueError(f"end_us < start_us na palavra {self.id!r}")
        return self

    @property
    def duration_us(self) -> int:
        return self.end_us - self.start_us

    @property
    def mid_us(self) -> int:
        return (self.start_us + self.end_us) // 2


class SpeakerSource(str, Enum):
    DIARIZATION = "diarization"
    TRACK = "track"  # faixa de microfone própria
    MANUAL = "manual"


# Paleta padrão com contraste alto sobre vídeo; o usuário troca no painel.
DEFAULT_SPEAKER_COLORS = ("#FFD400", "#4FC3F7", "#FF7AA2", "#7CFC9A", "#FFA94D", "#C3A6FF")


class Speaker(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    label: str  # "Falante 1" — nunca deduzido da identidade real
    display_name: str | None = None
    color: str = "#FFFFFF"
    style_id: str | None = None
    source: SpeakerSource = SpeakerSource.DIARIZATION
    include_in_captions: bool = True

    @property
    def name(self) -> str:
        return self.display_name or self.label


class Transcript(BaseModel):
    """Estado completo de uma revisão da transcrição."""

    model_config = ConfigDict(frozen=True)

    rev: int = 0
    language: str = "pt-BR"
    words: tuple[Word, ...] = ()
    speakers: tuple[Speaker, ...] = ()
    # Restrições manuais de agrupamento (IDs de palavras):
    break_after: frozenset[str] = frozenset()
    join_after: frozenset[str] = frozenset()

    @model_validator(mode="after")
    def _check_order(self) -> "Transcript":
        ids = set()
        for prev, cur in zip(self.words, self.words[1:]):
            if cur.start_us < prev.start_us:
                raise ValueError("palavras fora de ordem temporal")
        for w in self.words:
            if w.id in ids:
                raise ValueError(f"ID de palavra duplicado: {w.id}")
            ids.add(w.id)
        return self

    def speaker(self, speaker_id: str | None) -> Speaker | None:
        return next((s for s in self.speakers if s.id == speaker_id), None)


class CaptionLine(BaseModel):
    model_config = ConfigDict(frozen=True)

    word_ids: tuple[str, ...]
    text: str


class CaptionBlock(BaseModel):
    """Bloco de legenda derivado das palavras."""

    model_config = ConfigDict(frozen=True)

    id: str
    word_ids: tuple[str, ...]
    lines: tuple[CaptionLine, ...]
    start_us: int
    end_us: int
    speaker_id: str | None
    flags: frozenset[Literal["reading_speed", "too_short", "too_long"]] = frozenset()

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


MAX_FRAME_SIDE = 16_384


class SequenceInfo(BaseModel):
    """Descrição da sequência enviada pelo painel do Premiere."""

    name: str = "Sequência"
    fps_num: int = Field(30000, gt=0)
    fps_den: int = Field(1001, gt=0)
    drop_frame: bool = False
    width: int = Field(1920, gt=0, le=MAX_FRAME_SIDE)
    height: int = Field(1080, gt=0, le=MAX_FRAME_SIDE)
    audio_tracks: int = Field(1, ge=0, le=128)

    @model_validator(mode="after")
    def _check_rate(self) -> "SequenceInfo":
        fps = self.fps_num / self.fps_den
        if not 1 <= fps <= 1000:
            raise ValueError(f"taxa de quadros fora do intervalo 1–1000: {fps:.3f}")
        if self.drop_frame and min(abs(fps - 30000 / 1001), abs(fps - 60000 / 1001)) > 1e-3:
            raise ValueError("drop-frame só existe para 29,97 e 59,94 fps")
        return self
