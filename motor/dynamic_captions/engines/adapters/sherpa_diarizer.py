"""Diarizador sem token e liberado para uso comercial (sherpa-onnx).

- sherpa-onnx: Apache-2.0 (já é dependência do motor, para o Parakeet).
- Segmentação: pyannote segmentation-3.0 exportado para ONNX pelo projeto sherpa-onnx (MIT).
- Voz (embedding): WeSpeaker ResNet34 treinado no VoxCeleb (CC BY 4.0: uso comercial com crédito).

Os dois arquivos vêm das versões do sherpa-onnx no GitHub (sem conta no Hugging Face):
``python -m dynamic_captions.modelos baixar falantes``. Qualidade abaixo do SUPlime, mas sem restrição de uso.
"""

from __future__ import annotations

from pathlib import Path

from ..base import EngineInfo, SpeakerTurn
from .audio import SAMPLE_RATE, load_wav

SEG_DIR = "sherpa-onnx-pyannote-segmentation-3-0"
SEG_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/"
           f"{SEG_DIR}.tar.bz2")
EMB_FILE = "wespeaker_en_voxceleb_resnet34.onnx"
EMB_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/{EMB_FILE}"
THRESHOLD = 0.38  # distância para juntar trechos no mesmo falante; calibrado: 4 vozes do teste do sherpa-onnx separadas, a mesma pessoa em gravações diferentes fica junta


def model_paths(models_dir: Path) -> tuple[Path, Path]:
    return Path(models_dir) / SEG_DIR / "model.onnx", Path(models_dir) / EMB_FILE


class SherpaDiarizer:
    def __init__(self, models_dir: Path, num_threads: int = 4):
        import sherpa_onnx  # type: ignore

        seg, emb = model_paths(models_dir)
        missing = [p.name for p in (seg, emb) if not p.exists()]
        if missing:
            raise FileNotFoundError(f"modelos de falantes ausentes em {models_dir} ({', '.join(missing)}); "
                                    "baixe com: python -m dynamic_captions.modelos baixar falantes")
        self._sherpa = sherpa_onnx
        self._seg, self._emb, self._threads = str(seg), str(emb), num_threads
        self._sd = self._make(-1)
        self.info = EngineInfo("sherpa-diarization", "diarizer", "cpu",
                               "Apache-2.0 + MIT (segmentação) + CC-BY-4.0 (WeSpeaker/VoxCeleb)")

    def _make(self, num_clusters: int):
        s = self._sherpa
        config = s.OfflineSpeakerDiarizationConfig(
            segmentation=s.OfflineSpeakerSegmentationModelConfig(
                pyannote=s.OfflineSpeakerSegmentationPyannoteModelConfig(model=self._seg),
                num_threads=self._threads),
            embedding=s.SpeakerEmbeddingExtractorConfig(model=self._emb, num_threads=self._threads),
            clustering=s.FastClusteringConfig(num_clusters=num_clusters, threshold=THRESHOLD),
            min_duration_on=0.3, min_duration_off=0.5)
        if not config.validate():
            raise RuntimeError("configuração do diarizador sherpa-onnx inválida")
        return s.OfflineSpeakerDiarization(config)

    def diarize(self, audio: Path, *, num_speakers=None, min_speakers=None, max_speakers=None) -> list[SpeakerTurn]:
        samples = load_wav(audio)
        assert SAMPLE_RATE == 16000
        sd = self._make(int(num_speakers)) if num_speakers else self._sd
        result = sd.process(samples).sort_by_start_time()
        return [SpeakerTurn(f"SPEAKER_{seg.speaker:02d}", int(seg.start * 1e6), int(seg.end * 1e6)) for seg in result]
