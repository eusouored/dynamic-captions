"""Monta os motores reais de um perfil e os mantém carregados entre trabalhos.

Obrigatórios: VAD e motor principal (e o alinhador, quando o principal é o Qwen3-ASR, que
depende dele para ter tempo por palavra). Opcionais: verificador e diarizador; se um deles
não carregar, o trabalho segue sem ele e o motivo aparece nos avisos do resultado.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

from ..pipeline.audio_prep import prepare_audio
from ..pipeline.run import PipelineEngines
from .profiles import EngineChoice, Profile


class EngineUnavailable(RuntimeError):
    """Um motor obrigatório não pôde ser carregado; a mensagem diz como resolver."""


@dataclass
class EngineSettings:
    models_dir: Path
    cache_dir: Path
    hf_token: str | None = None  # só o pyannote Community-1 exige (termos aceitos no Hugging Face)
    qwen_asr_model: str = "Qwen/Qwen3-ASR-1.7B"
    qwen_aligner_model: str = "Qwen/Qwen3-ForcedAligner-0.6B"
    qwen_batch_size: int = 4
    whisper_model: str = "large-v3-turbo"
    # 0.2.0: o segundo modelo (Parakeet, CPU) só divergia e atrasava; ligue com DYNAMIC_CAPTIONS_VERIFICADOR=1.
    use_verifier: bool = False
    use_diarizer: bool = True

    @classmethod
    def from_env(cls, base_dir: Path) -> "EngineSettings":
        env = os.environ
        return cls(
            models_dir=Path(env.get("DYNAMIC_CAPTIONS_MODELS") or Path(base_dir) / "models"),
            cache_dir=Path(env.get("DYNAMIC_CAPTIONS_CACHE") or Path(base_dir) / "cache"),
            hf_token=env.get("HF_TOKEN") or None,
            qwen_asr_model=env.get("DYNAMIC_CAPTIONS_QWEN_ASR", "Qwen/Qwen3-ASR-1.7B"),
            qwen_batch_size=int(env.get("DYNAMIC_CAPTIONS_QWEN_BATCH", "4")),
            use_verifier=env.get("DYNAMIC_CAPTIONS_VERIFICADOR", "0") == "1" and env.get("DYNAMIC_CAPTIONS_SEM_VERIFICADOR", "0") != "1",
            use_diarizer=env.get("DYNAMIC_CAPTIONS_SEM_FALANTES", "0") != "1",
        )


EXTRA_FOR_PROFILE = {"preciso": "preciso", "equilibrado": "equilibrado", "universal": "universal"}

Loader = Callable[[EngineChoice, EngineSettings, dict[str, Any]], Any]


def _silero(choice: EngineChoice, s: EngineSettings, shared: dict[str, Any]):
    from .adapters.silero_vad import SileroVad

    return SileroVad()


def _qwen_aligner(choice: EngineChoice, s: EngineSettings, shared: dict[str, Any]):
    from .adapters.qwen3 import Qwen3ForcedAligner

    if "qwen_aligner" not in shared:
        shared["qwen_aligner"] = Qwen3ForcedAligner(s.qwen_aligner_model, choice.device, batch_size=s.qwen_batch_size)
    return shared["qwen_aligner"]


def _qwen_asr(choice: EngineChoice, s: EngineSettings, shared: dict[str, Any]):
    from .adapters.qwen3 import Qwen3Asr

    aligner = shared.get("qwen_aligner")
    if aligner is None:
        raise EngineUnavailable("o Qwen3-ASR precisa do Qwen3-ForcedAligner, que não carregou")
    return Qwen3Asr(s.qwen_asr_model, choice.device, aligner=aligner, batch_size=s.qwen_batch_size)


def _whisper(choice: EngineChoice, s: EngineSettings, shared: dict[str, Any]):
    if choice.runtime == "whisper.cpp":
        raise EngineUnavailable("whisper.cpp (Vulkan) ainda não tem adaptador")
    from .adapters.faster_whisper_asr import FasterWhisperAsr

    compute = "int8_float16" if choice.device == "cuda" else "int8"
    return FasterWhisperAsr(s.whisper_model, device=choice.device, compute_type=compute)


def _parakeet(choice: EngineChoice, s: EngineSettings, shared: dict[str, Any]):
    from .adapters.sherpa_parakeet import MODEL_DIR_NAME, SherpaParakeetAsr

    threads = max(2, min(8, (os.cpu_count() or 4) // 2))
    try:
        return SherpaParakeetAsr(s.models_dir / MODEL_DIR_NAME, provider="cpu", num_threads=threads)
    except FileNotFoundError:  # não veio na instalação (sem internet na hora): baixa agora
        from ..modelos import download_parakeet
        download_parakeet(s.models_dir)
        return SherpaParakeetAsr(s.models_dir / MODEL_DIR_NAME, provider="cpu", num_threads=threads)


def _pyannote(choice: EngineChoice, s: EngineSettings, shared: dict[str, Any]):
    from .adapters.pyannote_diarizer import COMMUNITY_1, SUPLIME, PyannoteDiarizer

    pipeline = SUPLIME if choice.model == "suplime" else COMMUNITY_1
    if choice.model == "suplime":
        import suplime  # noqa: F401  (registra as classes que o checkpoint do SUPlime referencia)
    return PyannoteDiarizer(pipeline, device=choice.device, token=s.hf_token)


def _sherpa_diarizer(choice: EngineChoice, s: EngineSettings, shared: dict[str, Any]):
    from .adapters.sherpa_diarizer import SherpaDiarizer

    threads = max(2, min(8, (os.cpu_count() or 4) // 2))
    try:
        return SherpaDiarizer(s.models_dir, num_threads=threads)
    except FileNotFoundError:  # não veio na instalação: baixa agora (32 MB)
        from ..modelos import download_speakers
        download_speakers(s.models_dir)
        return SherpaDiarizer(s.models_dir, num_threads=threads)


LOADERS: dict[str, Loader] = {
    "silero-vad-v6": _silero,
    "qwen3-forcedaligner-0.6b": _qwen_aligner,
    "qwen3-asr-1.7b": _qwen_asr,
    "qwen3-asr-0.6b": _qwen_asr,
    "whisper-large-v3-turbo": _whisper,
    "parakeet-tdt-0.6b-v3": _parakeet,
    "suplime": _pyannote,
    "pyannote-community-1": _pyannote,
    "sherpa-diarization": _sherpa_diarizer,
}

ROLE_NAMES = {"vad": "VAD", "aligner": "alinhador", "primary": "motor principal", "verifier": "verificador", "diarizer": "diarizador"}


def build_engines(
    profile: Profile, settings: EngineSettings, *, loaders: dict[str, Loader] | None = None
) -> PipelineEngines:
    loaders = LOADERS if loaders is None else loaders
    shared: dict[str, Any] = {}
    warnings: list[str] = []
    extra = EXTRA_FOR_PROFILE[profile.id]

    def load(role: str, choice: EngineChoice | None, *, required: bool):
        if choice is None:
            return None
        what = f"{ROLE_NAMES[role]} {choice.model}"
        try:
            loader = loaders.get(choice.model)
            if loader is None:
                raise EngineUnavailable(f"{choice.model} ({choice.runtime}) ainda não tem adaptador")
            return loader(choice, settings, shared)
        except ImportError as exc:
            msg = f"{what}: falta o pacote {exc.name or exc}. Instale com: pip install -e \".[{extra}]\""
        except EngineUnavailable as exc:
            msg = f"{what}: {exc}"
        except Exception as exc:  # erro do modelo (download, memória, CUDA): mensagem legível
            msg = f"{what} não carregou: {type(exc).__name__}: {exc}"
        if required:
            raise EngineUnavailable(msg)
        warnings.append(f"{msg} — seguindo sem ele")
        return None

    vad = load("vad", profile.vad, required=True)
    needs_aligner = profile.primary.model.startswith("qwen3-asr")
    aligner = load("aligner", profile.aligner, required=needs_aligner)
    primary = load("primary", profile.primary, required=True)
    verifier = load("verifier", profile.verifier, required=False) if settings.use_verifier else None
    diarizer = load("diarizer", profile.diarizer, required=False) if settings.use_diarizer else None
    if settings.use_diarizer and diarizer is None and profile.diarizer.model != "sherpa-diarization":
        # SUPlime/Community-1 indisponível (o SUPlime passou a exigir login no Hugging Face em 11/2026;
        # sem internet; sem token): cai no separador sem conta, em vez de seguir com um falante só.
        failed = warnings.pop() if warnings else ""
        diarizer = load("diarizer", EngineChoice("sherpa-diarization", "cpu", "sherpa-onnx"), required=False)
        if diarizer is not None:
            name = "SUPlime" if profile.diarizer.model == "suplime" else profile.diarizer.model
            warnings.append(f"{name} indisponível neste computador; separando as vozes com o modelo alternativo (sherpa-onnx)")
        else:
            warnings.insert(len(warnings) - 1, failed)  # mostra os dois motivos
    if settings.use_diarizer and diarizer is None:
        warnings.append("sem diarizador: todas as falas ficam com um falante só")
    return PipelineEngines(
        vad=vad,
        primary=primary,
        verifier=verifier,
        aligner=aligner,
        diarizer=diarizer,
        prepare=partial(prepare_audio, cache_dir=settings.cache_dir),
        warnings=warnings,
    )


@dataclass
class EngineCache:
    """Fábrica para a API: carrega os modelos uma vez por perfil e reaproveita entre trabalhos."""

    settings: EngineSettings
    loaders: dict[str, Loader] | None = None
    _built: dict[Profile, PipelineEngines] = field(default_factory=dict)
    _diarizers: dict[Profile, Any] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __call__(self, profile: Profile) -> PipelineEngines:
        with self._lock:
            if profile not in self._built:
                loaders = self.loaders
                if profile in self._diarizers and profile.diarizer:
                    loaders = dict(loaders or LOADERS)
                    loaders[profile.diarizer.model] = lambda *_args: self._diarizers[profile]
                self._built[profile] = build_engines(profile, self.settings, loaders=loaders)
                self._diarizers[profile] = self._built[profile].diarizer
            return self._built[profile]

    def get_diarizer(self, profile: Profile):
        """Revisar vozes não precisa carregar ASR, alinhador e verificador novamente."""
        with self._lock:
            if profile not in self._diarizers:
                if not self.settings.use_diarizer or profile.diarizer is None:
                    raise EngineUnavailable("a identificação de falantes não está disponível neste perfil")
                loader = (self.loaders or LOADERS)[profile.diarizer.model]
                self._diarizers[profile] = loader(profile.diarizer, self.settings, {})
            if self._diarizers[profile] is None:
                raise EngineUnavailable("o modelo de falantes não carregou")
            return self._diarizers[profile]
