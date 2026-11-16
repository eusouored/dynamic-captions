"""Modelos e dependências: verificar o que falta e baixar o Parakeet.

Uso:
    python -m smartcaption.modelos verificar
    python -m smartcaption.modelos baixar parakeet
    python -m smartcaption.modelos baixar falantes   (diarizador sem token, liberado para trabalho pago)
    python -m smartcaption.modelos baixar tudo

Os modelos Qwen3 e o SUPlime vêm do Hugging Face sozinhos na primeira transcrição
(vários GB no total). O Parakeet do sherpa-onnx é um arquivo das versões do sherpa-onnx no GitHub.
"""

from __future__ import annotations

import importlib
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from .engines.adapters.sherpa_parakeet import MODEL_DIR_NAME
from .engines.factory import EngineSettings
from .engines.profiles import choose_profile, detect_hardware
from .paths import config_dir
from .render.ffmpeg import find_ffmpeg

PARAKEET_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{MODEL_DIR_NAME}.tar.bz2"
PARAKEET_FILES = ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt")

PACKAGES = {
    "torch": "PyTorch",
    "soundfile": "leitura de WAV",
    "silero_vad": "VAD",
    "qwen_asr": "Qwen3-ASR e alinhador",
    "faster_whisper": "Whisper",
    "sherpa_onnx": "Parakeet",
    "pyannote.audio": "diarização",
    "suplime": "diarizador SUPlime",
}
NEEDED = {
    "preciso": {"torch", "soundfile", "silero_vad", "qwen_asr", "sherpa_onnx", "pyannote.audio", "suplime"},
    "equilibrado": {"torch", "soundfile", "silero_vad", "qwen_asr", "faster_whisper", "sherpa_onnx", "pyannote.audio", "suplime"},
    "universal": {"torch", "soundfile", "silero_vad", "sherpa_onnx", "pyannote.audio", "suplime"},
}


def _line(ok: bool, name: str, detail: str) -> None:
    print(f"  {'ok' if ok else 'falta':<7}{name:<16}{detail}")


def download_parakeet(models_dir: Path) -> Path:
    target = models_dir / MODEL_DIR_NAME
    if all((target / f).exists() for f in PARAKEET_FILES):
        print(f"Parakeet já está em {target}")
        return target
    models_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=models_dir) as tmp:
        archive = Path(tmp) / "parakeet.tar.bz2"
        print(f"Baixando {PARAKEET_URL}")

        def report(blocks: int, size: int, total: int) -> None:
            if total > 0:
                print(f"\r  {min(blocks * size, total) / 2**20:.0f} de {total / 2**20:.0f} MB", end="", flush=True)

        urllib.request.urlretrieve(PARAKEET_URL, archive, reporthook=report)
        print()
        with tarfile.open(archive, "r:bz2") as tar:
            members = [m for m in tar.getmembers() if m.name.split("/", 1)[0] == MODEL_DIR_NAME and Path(m.name).name in PARAKEET_FILES]
            _extract(tar, tmp, members)  # filtro impede caminhos fora da pasta
        extracted = Path(tmp) / MODEL_DIR_NAME
        missing = [f for f in PARAKEET_FILES if not (extracted / f).exists()]
        if missing:
            raise DownloadError(f"arquivo baixado sem {', '.join(missing)}")
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(extracted), target)
    print(f"Parakeet pronto em {target}")
    return target


class DownloadError(RuntimeError):
    """Download incompleto ou suspeito. Erro comum (não SystemExit): o motor trata e segue."""


def _extract(tar, dest, members) -> None:
    """Extrai só os arquivos esperados; o filtro "data" (Python 3.11.4+) impede caminhos fora da pasta."""
    for m in members:
        if m.name.startswith(("/", "\\")) or ".." in Path(m.name).parts or not (m.isfile() or m.isdir()):
            raise DownloadError(f"arquivo suspeito no pacote: {m.name}")
    try:
        tar.extractall(dest, members=members, filter="data")
    except TypeError:  # Python 3.11.0–3.11.3: sem o parâmetro filter (os nomes já foram conferidos acima)
        tar.extractall(dest, members=members)


def _fetch(url: str, dest: Path) -> None:
    last = [-1]

    def report(blocks: int, size: int, total: int) -> None:
        mb = min(blocks * size, total) // 2**20
        if total > 0 and mb != last[0]:  # só quando muda (no console do instalador, uma linha que se reescreve)
            last[0] = mb
            print(f"\r  {mb} de {total // 2**20} MB", end="", flush=True)

    print(f"Baixando {url}")
    urllib.request.urlretrieve(url, dest, reporthook=report)
    print()


def download_speakers(models_dir: Path) -> tuple[Path, Path]:
    """Segmentação pyannote 3.0 (ONNX, MIT) e voz WeSpeaker ResNet34 (CC BY 4.0), das versões do sherpa-onnx."""
    from .engines.adapters.sherpa_diarizer import EMB_URL, SEG_DIR, SEG_URL, model_paths

    seg, emb = model_paths(models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)
    if not seg.exists():
        with tempfile.TemporaryDirectory(dir=models_dir) as tmp:
            archive = Path(tmp) / "seg.tar.bz2"
            _fetch(SEG_URL, archive)
            with tarfile.open(archive, "r:bz2") as tar:
                members = [m for m in tar.getmembers() if m.name.split("/", 1)[0] == SEG_DIR and Path(m.name).name in ("model.onnx", "LICENSE", "README.md")]
                _extract(tar, tmp, members)
            got = Path(tmp) / SEG_DIR
            if not (got / "model.onnx").exists():
                raise DownloadError("arquivo baixado sem model.onnx")
            if seg.parent.exists():
                shutil.rmtree(seg.parent)
            shutil.move(str(got), seg.parent)
    if not emb.exists():
        part = emb.with_suffix(".parcial")
        _fetch(EMB_URL, part)
        if part.stat().st_size < 1_000_000:
            part.unlink(missing_ok=True)
            raise DownloadError("modelo de voz baixado incompleto")
        part.replace(emb)
    print(f"Modelos de falantes prontos em {models_dir}")
    return seg, emb


def _hf_logged_in() -> bool:
    """Há token da Hugging Face (variável HF_TOKEN ou login salvo pelo instalador)?"""
    import os
    if os.environ.get("HF_TOKEN"):
        return True
    try:
        from huggingface_hub import get_token
        return bool(get_token())
    except Exception:
        return False


def download_all(settings: EngineSettings) -> int:
    """Tudo o que o perfil deste computador usa, já na instalação (e não na primeira vez que o painel abre)."""
    import os

    hw = detect_hardware()
    commercial = os.environ.get("SMARTCAPTION_USO_COMERCIAL", "0") == "1"
    profile = choose_profile(hw, allow_noncommercial=not commercial)
    print(f"Perfil: {profile.label} ({hw.gpu_vendor}, {hw.vram_gb:.0f} GB de VRAM) · uso {'pago' if commercial else 'pessoal'}")
    failed = 0

    def step(name, fn):
        nonlocal failed
        try:
            fn()
        except Exception as exc:  # download que falhou segue com os outros; Ctrl+C interrompe
            failed += 1
            print(f"  não deu para baixar {name} agora: {type(exc).__name__}: {exc}")

    chosen = [c for c in (profile.primary, profile.verifier if settings.use_verifier else None, profile.aligner, profile.diarizer) if c]
    if any(c.model == "parakeet-tdt-0.6b-v3" for c in chosen):
        step("Parakeet", lambda: download_parakeet(settings.models_dir))
    # Sempre: é o separador de vozes do trabalho pago e a reserva do SUPlime (que passou a exigir login).
    step("modelos de falantes", lambda: download_speakers(settings.models_dir))
    repos = []
    for c in chosen:
        if c.model.startswith("qwen3-asr"):
            repos.append(settings.qwen_asr_model)
        elif c.model == "qwen3-forcedaligner-0.6b":
            repos.append(settings.qwen_aligner_model)
        elif c.model == "suplime" and _hf_logged_in():
            repos.append("rewayai/suplime")  # desde 11/2026 só com login; sem token o motor usa a reserva
    from .rede import acelerar, baixar_repo

    if repos:
        print("Download em várias conexões: " + ("ligado" if acelerar() else "indisponível (hf_transfer não instalado); uma conexão por arquivo"))
    for repo in repos:
        def get(repo=repo):
            print(f"Baixando {repo} (Hugging Face; o que já estiver no cache não é baixado de novo)")
            baixar_repo(repo)
        step(repo, get)
    if any(c.model == "whisper-large-v3-turbo" and c.runtime.startswith("faster-whisper") for c in chosen):
        def whisper():
            from faster_whisper.utils import download_model
            print(f"Baixando Whisper {settings.whisper_model}")
            download_model(settings.whisper_model)
        step("Whisper", whisper)
    print("Modelos prontos." if not failed else f"{failed} item(ns) ficaram para depois: baixam sozinhos quando o motor precisar (com internet), ou rode o instalador de novo.")
    return 0 if not failed else 1


def check() -> int:
    settings = EngineSettings.from_env(config_dir())
    hw = detect_hardware()
    profile = choose_profile(hw, allow_noncommercial=True)
    print(f"Hardware: {hw.gpu_vendor}, {hw.vram_gb:.1f} GB de VRAM, {hw.ram_gb:.1f} GB de RAM, {hw.cpu_cores} núcleos")
    print(f"Perfil: {profile.label} ({profile.id})")
    problems = 0
    needed = NEEDED[profile.id]
    for mod, role in PACKAGES.items():
        if mod not in needed:
            continue
        try:
            m = importlib.import_module(mod)
        except Exception as exc:
            _line(False, mod, f"{role} ({type(exc).__name__}: {exc})")
            problems += 1
            continue
        detail = role
        if mod == "torch":
            cuda = m.cuda.is_available()
            detail += f" {m.__version__}, CUDA: {m.cuda.get_device_name(0) if cuda else 'indisponível'}"
            if hw.gpu_vendor == "nvidia" and not cuda:
                detail += " — reinstale o PyTorch com CUDA (ver README)"
                _line(False, mod, detail)
                problems += 1
                continue
        _line(True, mod, detail)
    par = settings.models_dir / MODEL_DIR_NAME
    has_par = all((par / f).exists() for f in PARAKEET_FILES)
    _line(has_par, "Parakeet", str(par) + ("" if has_par else " — python -m smartcaption.modelos baixar parakeet"))
    ff = find_ffmpeg(required=False)
    _line(bool(ff), "FFmpeg", ff or "não encontrado — winget install Gyan.FFmpeg")
    problems += (not has_par) + (not ff)
    print("Tudo pronto." if not problems else f"{problems} item(ns) a resolver.")
    return 0 if not problems else 1


def main(argv: list[str]) -> int:
    if argv[:1] == ["verificar"]:
        return check()
    models = EngineSettings.from_env(config_dir()).models_dir
    if argv[:2] == ["baixar", "parakeet"]:
        download_parakeet(models)
        return 0
    if argv[:2] == ["baixar", "falantes"]:
        download_speakers(models)
        return 0
    if argv[:2] == ["baixar", "tudo"]:
        return download_all(EngineSettings.from_env(config_dir()))
    print(__doc__)
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except DownloadError as exc:
        print(f"Falhou: {exc}")
        raise SystemExit(1) from None
