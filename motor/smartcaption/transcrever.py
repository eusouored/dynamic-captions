"""Transcreve uma mídia com os motores reais e mede o resultado (teste de ponta a ponta).

Uso:
    python -m smartcaption.transcrever video.mp4
    python -m smartcaption.transcrever video.mp4 --saida resultado --dicionario "Premiere, Ana" --falantes 2

Grava, na pasta de saída: legenda .srt, transcrição .json e ``relatorio.json`` com perfil,
motores usados, avisos, tempo de cada etapa, velocidade (vezes o tempo real) e pico de VRAM.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path

from .domain.grouping import build_blocks
from .domain.presets import get_preset
from .domain.timecode import FrameRate
from .engines.adapters.audio import load_wav
from .engines.factory import EngineSettings, EngineUnavailable, build_engines
from .engines.profiles import choose_profile, detect_hardware
from .export.json_export import to_json
from .export.subtitles import to_srt
from .paths import config_dir
from .pipeline.run import TranscribeOptions, transcribe


def _vram_peak_gb() -> float | None:
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            return round(torch.cuda.max_memory_allocated() / 2**30, 2)
    except Exception:
        pass
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m smartcaption.transcrever", description=__doc__.splitlines()[0])
    ap.add_argument("midia", type=Path)
    ap.add_argument("--saida", type=Path, default=None, help="pasta de saída (padrão: ao lado da mídia)")
    ap.add_argument("--idioma", default="pt")
    ap.add_argument("--dicionario", default="", help="nomes e termos separados por vírgula")
    ap.add_argument("--falantes", type=int, default=None, help="número de falantes, se souber")
    ap.add_argument("--preset", default="legenda_tradicional")
    ap.add_argument("--fps", default="29.97")
    ap.add_argument("--sem-verificador", action="store_true")
    ap.add_argument("--sem-falantes", action="store_true")
    ap.add_argument("--uso-comercial", action="store_true", help="só modelos com licença comercial")
    args = ap.parse_args(argv)

    media: Path = args.midia
    out_dir = args.saida or media.with_name(f"{media.stem}_smartcaption")
    out_dir.mkdir(parents=True, exist_ok=True)

    hw = detect_hardware()
    profile = choose_profile(hw, allow_noncommercial=not args.uso_comercial)
    print(f"Hardware: {hw.gpu_vendor}, {hw.vram_gb:.1f} GB de VRAM, {hw.ram_gb:.1f} GB de RAM")
    print(f"Perfil: {profile.label}")

    settings = EngineSettings.from_env(config_dir())
    settings.use_verifier = settings.use_verifier and not args.sem_verificador
    settings.use_diarizer = settings.use_diarizer and not args.sem_falantes

    t0 = time.perf_counter()
    print("Carregando modelos (na primeira vez, o download pode levar vários minutos)...")
    try:
        engines = build_engines(profile, settings)
    except EngineUnavailable as exc:
        print(f"\nNão foi possível montar os motores: {exc}", file=sys.stderr)
        print("Diagnóstico completo: python -m smartcaption.modelos verificar", file=sys.stderr)
        return 1
    load_s = time.perf_counter() - t0
    for w in engines.warnings:
        print(f"Aviso: {w}")

    stage_times: dict[str, float] = {}
    marks: list[tuple[str, float]] = []

    def progress(stage: str, _value: float) -> None:
        now = time.perf_counter()
        if marks:
            prev, started = marks[-1]
            stage_times[prev] = round(now - started, 2)
        marks.append((stage, now))
        if stage != "done":
            print(f"  {stage}...", flush=True)

    opts = TranscribeOptions(
        language=args.idioma,
        dictionary=[t.strip() for t in args.dicionario.split(",") if t.strip()],
        num_speakers=args.falantes,
    )
    t1 = time.perf_counter()
    result = transcribe(media, engines, opts, progress=progress)
    run_s = time.perf_counter() - t1

    prepared = engines.prepare(media) if engines.prepare else media  # do cache, sem converter de novo
    duration_s = len(load_wav(prepared)) / 16_000
    t = result.transcript
    rate = FrameRate.parse(args.fps)
    blocks = build_blocks(t, get_preset(args.preset))
    srt = out_dir / f"{media.stem}.srt"
    srt.write_text(to_srt(blocks, t, rate, speaker_names=len(t.speakers) > 1), encoding="utf-8")
    (out_dir / f"{media.stem}.json").write_text(to_json(t, blocks), encoding="utf-8")

    report = {
        "midia": str(media),
        "duracao_s": round(duration_s, 2),
        "sistema": {"python": platform.python_version(), "so": platform.platform()},
        "hardware": hw.__dict__,
        "perfil": profile.id,
        "motores": result.engines_used,
        "avisos": result.warnings,
        "carregar_modelos_s": round(load_s, 2),
        "processar_s": round(run_s, 2),
        "vezes_tempo_real": round(duration_s / run_s, 1) if run_s else None,
        "etapas_s": stage_times,
        "pico_vram_gb": _vram_peak_gb(),
        "palavras": len(t.words),
        "falantes": len(t.speakers),
        "blocos": len(blocks),
        "divergencias": len(result.disputes),
        "suspeitas_alucinacao": sum(1 for w in t.words if "hallucination_suspect" in {f.value for f in w.flags}),
    }
    (out_dir / "relatorio.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nPronto: {len(t.words)} palavras, {len(t.speakers)} falante(s), {len(result.disputes)} divergência(s).")
    print(f"Áudio de {duration_s:.0f} s processado em {run_s:.1f} s ({report['vezes_tempo_real']}x o tempo real).")
    if report["pico_vram_gb"] is not None:
        print(f"Pico de VRAM (PyTorch): {report['pico_vram_gb']} GB")
    print(f"Arquivos em {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
