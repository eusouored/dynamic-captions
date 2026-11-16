"""Parakeet-TDT-0.6B-v3 via sherpa-onnx (CPU, CUDA ou DirectML). CC-BY-4.0.

Perfil Universal (motor principal) e verificador nos outros perfis.
Não testado neste repositório (exige o modelo ONNX exportado para o sherpa-onnx).
"""

from __future__ import annotations

from pathlib import Path

from ..base import AsrWord, EngineInfo, TimeRange
from .audio import SAMPLE_RATE, cut, load_wav, plan_segments

MODEL_DIR_NAME = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"


def merge_subwords(
    tokens: list[str],
    starts_s: list[float],
    offset_us: int,
    end_us: int,
    durations_s: list[float] | None = None,
) -> list[AsrWord]:
    """Junta tokens SentencePiece em palavras; '▁' (ou espaço) marca o início de palavra.

    O Parakeet TDT informa a duração de cada token; com ela, a palavra termina quando o último
    token termina (não se estende pela pausa até a palavra seguinte).
    """
    words: list[list] = []  # [texto, início, fim conhecido ou None]
    for i, (tok, t) in enumerate(zip(tokens, starts_s)):
        start = offset_us + round(t * 1e6)
        tok_end = None
        if durations_s and i < len(durations_s) and durations_s[i] > 0:
            tok_end = start + round(durations_s[i] * 1e6)
        if not words or tok.startswith(("▁", " ")):
            words.append([tok.lstrip("▁ "), start, tok_end])
        else:
            words[-1][0] += tok
            if tok_end is not None:
                words[-1][2] = tok_end
    out = []
    for i, (text, start, known_end) in enumerate(words):
        nxt = words[i + 1][1] if i + 1 < len(words) else end_us
        stop = min(known_end, nxt) if known_end is not None else nxt
        if text:
            out.append(AsrWord(text, start, max(stop, start)))
    return out


class SherpaParakeetAsr:
    def __init__(self, model_dir: Path, provider: str = "cpu", num_threads: int = 4):
        import sherpa_onnx  # type: ignore

        d = Path(model_dir)
        missing = [n for n in ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt") if not (d / n).exists()]
        if missing:
            raise FileNotFoundError(
                f"modelo Parakeet incompleto em {d} (faltam {', '.join(missing)}); "
                "baixe com: python -m dynamic_captions.modelos baixar parakeet"
            )
        self._rec = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(d / "encoder.int8.onnx"),
            decoder=str(d / "decoder.int8.onnx"),
            joiner=str(d / "joiner.int8.onnx"),
            tokens=str(d / "tokens.txt"),
            num_threads=num_threads,
            provider=provider,
            model_type="nemo_transducer",
        )
        self.info = EngineInfo("parakeet-tdt-0.6b-v3", "asr", provider, "CC-BY-4.0")

    def transcribe(self, audio: Path, regions: list[TimeRange], *, language: str = "pt", context: str | None = None) -> list[AsrWord]:
        if not regions:
            return []  # sem fala detectada
        samples = load_wav(audio)
        out: list[AsrWord] = []
        for r in plan_segments(samples, regions, max_len_us=60_000_000):  # trechos com contexto
            stream = self._rec.create_stream()
            stream.accept_waveform(SAMPLE_RATE, cut(samples, r))
            self._rec.decode_stream(stream)
            res = stream.result
            durations = list(getattr(res, "durations", []) or [])
            out.extend(merge_subwords(list(res.tokens), list(res.timestamps), r.start_us, r.end_us, durations))
        return out
