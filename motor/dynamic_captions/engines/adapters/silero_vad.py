"""Silero VAD v6 (MIT), modelo TorchScript embutido no pacote ``silero-vad`` (sem download).

Lê o WAV de trabalho (mono, 16 kHz) com ``soundfile``; não usa ``read_audio`` do pacote, que
depende do torchaudio/torchcodec. Não testado aqui com o modelo real (exige PyTorch).
"""

from __future__ import annotations

from pathlib import Path

from ..base import EngineInfo, TimeRange
from .audio import SAMPLE_RATE, load_wav, sample_to_us


class SileroVad:
    def __init__(self, threshold: float = 0.5, min_silence_ms: int = 300, speech_pad_ms: int = 100, *, onnx: bool = False):
        from silero_vad import load_silero_vad  # type: ignore

        self._model = load_silero_vad(onnx=onnx)
        self._params = dict(threshold=threshold, min_silence_duration_ms=min_silence_ms, speech_pad_ms=speech_pad_ms)
        self.info = EngineInfo("silero-vad-v6", "vad", "cpu", "MIT")

    def detect(self, audio: Path) -> list[TimeRange]:
        import torch  # type: ignore
        from silero_vad import get_speech_timestamps  # type: ignore

        wav = torch.from_numpy(load_wav(audio).copy())
        stamps = get_speech_timestamps(wav, self._model, sampling_rate=SAMPLE_RATE, **self._params)
        return [TimeRange(sample_to_us(s["start"]), sample_to_us(s["end"])) for s in stamps]
