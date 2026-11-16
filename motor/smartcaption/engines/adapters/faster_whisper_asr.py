"""Whisper large-v3-turbo via faster-whisper (CTranslate2). Perfil Equilibrado e verificador.

Não testado neste repositório (exige o modelo baixado). Importação adiada para não exigir
a dependência em quem não usa este perfil.
"""

from __future__ import annotations

from pathlib import Path

from ..base import AsrWord, EngineInfo, TimeRange


class FasterWhisperAsr:
    def __init__(self, model: str = "large-v3-turbo", device: str = "cuda", compute_type: str = "int8_float16"):
        from faster_whisper import WhisperModel  # type: ignore

        self._model = WhisperModel(model, device=device, compute_type=compute_type)
        self.info = EngineInfo(f"whisper-{model}", "asr", device, "MIT")

    def transcribe(
        self, audio: Path, regions: list[TimeRange], *, language: str = "pt", context: str | None = None
    ) -> list[AsrWord]:
        if not regions:
            return []  # o VAD não achou fala: não chamar o modelo (evita texto inventado no silêncio)
        clips = [x for r in regions for x in (r.start_us / 1e6, r.end_us / 1e6)]
        segments, _info = self._model.transcribe(
            str(audio),
            language=language.split("-")[0],
            word_timestamps=True,
            initial_prompt=context,  # dicionário do usuário como contexto
            clip_timestamps=clips,  # só os trechos com voz
            vad_filter=False,  # o VAD já rodou antes
            condition_on_previous_text=False,  # reduz laços de repetição
        )
        words: list[AsrWord] = []
        for seg in segments:
            for w in seg.words or []:
                words.append(AsrWord(w.word.strip(), int(w.start * 1e6), int(w.end * 1e6), float(w.probability)))
        return words
