"""Contratos que todo modelo implementa. O resto do sistema só conhece estas interfaces.

Todos os tempos são absolutos (µs desde o início do áudio). Cada adaptador roda dentro de um
worker isolado; nada aqui importa PyTorch ou bibliotecas de modelo.

Regiões de voz: uma lista vazia significa "o VAD não encontrou fala". Nesse caso um ASR
devolve ``[]`` sem rodar o modelo; nunca trata a lista vazia como "áudio inteiro".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class TimeRange:
    start_us: int
    end_us: int

    @property
    def duration_us(self) -> int:
        return self.end_us - self.start_us

    def contains(self, us: int, pad_us: int = 0) -> bool:
        return self.start_us - pad_us <= us < self.end_us + pad_us


@dataclass(frozen=True)
class AsrWord:
    text: str
    start_us: int
    end_us: int
    confidence: float | None = None


@dataclass(frozen=True)
class SpeakerTurn:
    speaker: str  # rótulo interno do diarizador ("SPEAKER_00")
    start_us: int
    end_us: int


@dataclass(frozen=True)
class EngineInfo:
    name: str
    family: str  # "vad" | "asr" | "aligner" | "diarizer"
    device: str  # "cuda" | "cpu" | "vulkan" | "directml"
    license: str


@runtime_checkable
class VadEngine(Protocol):
    info: EngineInfo

    def detect(self, audio: Path) -> list[TimeRange]: ...


@runtime_checkable
class AsrEngine(Protocol):
    info: EngineInfo

    def transcribe(
        self,
        audio: Path,
        regions: list[TimeRange],
        *,
        language: str = "pt",
        context: str | None = None,
    ) -> list[AsrWord]: ...


@runtime_checkable
class Aligner(Protocol):
    info: EngineInfo
    max_region_us: int  # Qwen3-ForcedAligner: 300 s

    def align(self, audio: Path, region: TimeRange, text: str, *, language: str = "pt") -> list[AsrWord]: ...


@runtime_checkable
class Diarizer(Protocol):
    info: EngineInfo

    def diarize(
        self,
        audio: Path,
        *,
        num_speakers: int | None = None,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
    ) -> list[SpeakerTurn]: ...
