"""Diarizadores no formato pyannote.audio 4: SUPlime (padrão no uso pessoal) e Community-1.

- SUPlime: ``pip install suplime``; pesos CC BY-NC 4.0, sem token do Hugging Face.
- Community-1: pesos CC-BY-4.0; aceitar os termos uma vez no Hugging Face.

Não testado neste repositório (exige os pesos baixados).
"""

from __future__ import annotations

from pathlib import Path

from ..base import EngineInfo, SpeakerTurn
from .audio import SAMPLE_RATE, load_wav

SUPLIME = "rewayai/suplime"
COMMUNITY_1 = "pyannote/speaker-diarization-community-1"
_LICENSES = {SUPLIME: "CC-BY-NC-4.0", COMMUNITY_1: "CC-BY-4.0"}


class PyannoteDiarizer:
    def __init__(self, pipeline: str = SUPLIME, device: str = "cuda", token: str | None = None):
        import torch  # type: ignore
        from pyannote.audio import Pipeline  # type: ignore

        self._pipe = Pipeline.from_pretrained(pipeline, token=token)
        self._pipe.to(torch.device(device))
        name = "suplime" if pipeline == SUPLIME else pipeline.rsplit("/", 1)[-1]
        self.info = EngineInfo(name, "diarizer", device, _LICENSES.get(pipeline, "ver model card"))

    def diarize(self, audio: Path, *, num_speakers=None, min_speakers=None, max_speakers=None) -> list[SpeakerTurn]:
        kwargs = {k: v for k, v in dict(num_speakers=num_speakers, min_speakers=min_speakers, max_speakers=max_speakers).items() if v}
        import torch  # type: ignore

        # Forma de onda em memória: o pyannote 4 decodificaria o arquivo com torchcodec, que no
        # Windows exige as bibliotecas compartilhadas do FFmpeg.
        waveform = torch.from_numpy(load_wav(audio).copy()).unsqueeze(0)
        output = self._pipe({"waveform": waveform, "sample_rate": SAMPLE_RATE}, **kwargs)
        # Community-1 oferece saída exclusiva; outros pipelines devolvem a anotação comum, com
        # sobreposição, que a atribuição por palavra já trata (marca de fala simultânea).
        annotation = (
            getattr(output, "exclusive_speaker_diarization", None)
            or getattr(output, "speaker_diarization", None)
            or output
        )
        return [
            SpeakerTurn(str(label), int(segment.start * 1e6), int(segment.end * 1e6))
            for segment, _track, label in annotation.itertracks(yield_label=True)
        ]
