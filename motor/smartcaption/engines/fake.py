"""Motores falsos e determinísticos para testes e para desenvolver a interface sem GPU."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .base import AsrWord, EngineInfo, SpeakerTurn, TimeRange


@dataclass
class ScriptedVad:
    regions: list[TimeRange]
    info: EngineInfo = field(default_factory=lambda: EngineInfo("fake-vad", "vad", "cpu", "MIT"))

    def detect(self, audio: Path) -> list[TimeRange]:
        return list(self.regions)


@dataclass
class ScriptedAsr:
    """Devolve as palavras roteirizadas que caem nas regiões pedidas."""

    words: list[AsrWord]
    name: str = "fake-asr"
    info: EngineInfo = field(init=False)
    calls: list[tuple[list[TimeRange], str | None]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.info = EngineInfo(self.name, "asr", "cpu", "MIT")

    def transcribe(self, audio: Path, regions: list[TimeRange], *, language: str = "pt", context: str | None = None) -> list[AsrWord]:
        self.calls.append((list(regions), context))
        out = []
        for w in self.words:
            mid = (w.start_us + w.end_us) // 2
            if any(r.contains(mid) for r in regions):  # sem regiões = sem fala
                out.append(w)
        return out


@dataclass
class ScriptedDiarizer:
    turns: list[SpeakerTurn]
    info: EngineInfo = field(default_factory=lambda: EngineInfo("fake-diarizer", "diarizer", "cpu", "MIT"))

    def diarize(self, audio: Path, *, num_speakers=None, min_speakers=None, max_speakers=None) -> list[SpeakerTurn]:
        return list(self.turns)
