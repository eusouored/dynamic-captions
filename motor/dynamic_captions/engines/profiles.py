"""Escolha do perfil de motores pelo hardware. Espelha a tabela de decisão do documento.

As escolhas são provisórias até o benchmark PT-BR da Fase 0; mudar um modelo é mudar
uma linha aqui, não o resto do sistema.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Literal

GpuVendor = Literal["nvidia", "amd", "intel", "apple", "none"]


@dataclass(frozen=True)
class HardwareInfo:
    gpu_vendor: GpuVendor = "none"
    vram_gb: float = 0.0
    cpu_cores: int = 1
    ram_gb: float = 0.0


@dataclass(frozen=True)
class EngineChoice:
    model: str
    device: str  # cuda | cpu | vulkan
    runtime: str


@dataclass(frozen=True)
class Profile:
    id: Literal["preciso", "equilibrado", "universal"]
    label: str
    primary: EngineChoice
    verifier: EngineChoice | None
    aligner: EngineChoice | None
    diarizer: EngineChoice
    vad: EngineChoice


SILERO = EngineChoice("silero-vad-v6", "cpu", "onnx")


def _diarizer(device: str, allow_noncommercial: bool) -> EngineChoice:
    """Uso pessoal: SUPlime (CC BY-NC 4.0), o menor DER médio. Desde 11/2026 o repositório exige login
    no Hugging Face; sem token, o build_engines cai no diarizador do sherpa-onnx.

    Trabalho remunerado (``allow_noncommercial=False``, DYNAMIC_CAPTIONS_USO_COMERCIAL=1): Community-1
    (CC-BY-4.0) se houver HF_TOKEN (o modelo exige aceitar os termos no Hugging Face); sem token, o
    diarizador do sherpa-onnx (Apache-2.0/MIT/CC-BY-4.0), que funciona sem conta nenhuma.
    """
    if allow_noncommercial:
        return EngineChoice("suplime", device, "pyannote.audio+suplime")
    if os.environ.get("HF_TOKEN"):
        return EngineChoice("pyannote-community-1", device, "pyannote.audio")
    return EngineChoice("sherpa-diarization", "cpu", "sherpa-onnx")


def choose_profile(hw: HardwareInfo, *, allow_noncommercial: bool = False) -> Profile:
    enough_ram = hw.ram_gb == 0 or hw.ram_gb >= 8  # 0 = desconhecido
    if hw.gpu_vendor == "nvidia" and hw.vram_gb >= 8:
        return Profile(
            id="preciso",
            label="Preciso",
            primary=EngineChoice("qwen3-asr-1.7b", "cuda", "transformers"),
            # Parakeet int8 na CPU: o pacote sherpa-onnx do PyPI é só CPU e a GPU fica com o Qwen.
            verifier=EngineChoice("parakeet-tdt-0.6b-v3", "cpu", "sherpa-onnx") if enough_ram else None,
            aligner=EngineChoice("qwen3-forcedaligner-0.6b", "cuda", "transformers"),
            diarizer=_diarizer("cuda", allow_noncommercial),
            vad=SILERO,
        )
    if hw.gpu_vendor == "nvidia" and hw.vram_gb >= 4:
        return Profile(
            id="equilibrado",
            label="Equilibrado",
            primary=EngineChoice("whisper-large-v3-turbo", "cuda", "faster-whisper-int8"),
            verifier=EngineChoice("parakeet-tdt-0.6b-v3", "cpu", "sherpa-onnx") if enough_ram else None,
            aligner=EngineChoice("qwen3-forcedaligner-0.6b", "cuda", "transformers") if hw.vram_gb >= 6 else None,
            diarizer=_diarizer("cuda", allow_noncommercial),
            vad=SILERO,
        )
    has_vulkan_gpu = hw.gpu_vendor in ("amd", "intel", "nvidia") and hw.vram_gb >= 4
    return Profile(
        id="universal",
        label="Universal",
        primary=EngineChoice("parakeet-tdt-0.6b-v3", "cpu", "sherpa-onnx"),
        verifier=EngineChoice("whisper-large-v3-turbo", "vulkan", "whisper.cpp") if has_vulkan_gpu and enough_ram else None,
        aligner=None,  # timestamps nativos do Parakeet
        diarizer=_diarizer("cpu", allow_noncommercial),
        vad=SILERO,
    )


def _ram_gb() -> float:
    try:
        import psutil  # type: ignore

        return psutil.virtual_memory().total / 2**30
    except Exception:
        pass
    if sys.platform == "win32":
        return _windows_ram_gb()
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
    except (ValueError, OSError, AttributeError):
        return 0.0


def _windows_ram_gb() -> float:
    """GlobalMemoryStatusEx, sem dependências (o psutil não é obrigatório)."""
    import ctypes

    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    stat = MemoryStatusEx()
    stat.dwLength = ctypes.sizeof(MemoryStatusEx)
    try:
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):  # type: ignore[attr-defined]
            return stat.ullTotalPhys / 2**30
    except (AttributeError, OSError):
        pass
    return 0.0


def detect_hardware() -> HardwareInfo:
    """Detecção simples; a versão completa (AMD/Intel via Vulkan, DirectML) vem na Fase 1."""
    cores = os.cpu_count() or 1
    ram = _ram_gb()
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5, check=True,
                **({"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)} if sys.platform == "win32" else {}),
            ).stdout.strip().splitlines()
            if out:
                return HardwareInfo("nvidia", max(float(x) for x in out) / 1024, cores, ram)
        except (subprocess.SubprocessError, ValueError, OSError):
            pass
    return HardwareInfo("none", 0.0, cores, ram)
