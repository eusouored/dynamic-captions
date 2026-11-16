"""Qwen3-ASR (1.7B ou 0.6B) e Qwen3-ForcedAligner-0.6B, via pacote ``qwen-asr`` (Apache 2.0).

Escrito contra o código-fonte do ``qwen-asr`` 0.0.6 (``Qwen3ASRModel.transcribe`` e
``Qwen3ForcedAligner.align``). Fatos dessa versão que o adaptador respeita:

- o reconhecimento devolve só texto; tempos por palavra vêm do alinhador;
- o alinhador aceita até 180 s por chamada (``MAX_FORCE_ALIGN_INPUT_SECONDS``);
- o alinhador quebra o texto por espaços e tira a pontuação de cada palavra (fica só letra,
  número e apóstrofo), então uma palavra só de pontuação não recebe tempo;
- ``max_new_tokens`` padrão é 512, curto para trechos longos: aqui o trecho tem no máximo 60 s
  e o limite sobe para 1024;
- o contexto (dicionário do usuário) entra como mensagem de sistema;
- o idioma é um nome em inglês ("Portuguese"); com ele a saída vem só com o texto.

Testado aqui com objetos simulados no lugar dos modelos (o contrato e o mapeamento de tempos).
Inferência real: rodar ``python -m dynamic_captions.transcrever`` na máquina com GPU.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..base import AsrWord, EngineInfo, TimeRange
from .audio import SAMPLE_RATE, cut, load_wav, plan_segments

MAX_ALIGN_US = 180_000_000
SEGMENT_US = 60_000_000

QWEN_LANGUAGES = {
    "pt": "Portuguese", "en": "English", "es": "Spanish", "fr": "French", "de": "German",
    "it": "Italian", "nl": "Dutch", "ru": "Russian", "ja": "Japanese", "ko": "Korean",
    "zh": "Chinese", "ar": "Arabic", "hi": "Hindi", "tr": "Turkish", "pl": "Polish",
}


class AlignmentMismatch(RuntimeError):
    """O alinhador devolveu um número de itens diferente do esperado para o texto."""


def qwen_language(code: str) -> str:
    base = code.split("-")[0].split("_")[0].lower()
    if base in QWEN_LANGUAGES:
        return QWEN_LANGUAGES[base]
    return code[:1].upper() + code[1:].lower()  # já é um nome ("portuguese" → "Portuguese")


def _cuda_device(device: str) -> str:
    import torch  # type: ignore

    if device.startswith("cuda"):
        if not torch.cuda.is_available():
            raise RuntimeError(
                "PyTorch sem CUDA. Reinstale com a versão para NVIDIA, por exemplo: "
                "pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128"
            )
        return "cuda:0" if device == "cuda" else device
    return device


def map_tokens_to_items(
    tokens: Sequence[str], counts: Sequence[int], items: Sequence[Any], offset_us: int
) -> list[AsrWord]:
    """Uma ``AsrWord`` por token do texto, com o tempo dos itens do alinhador.

    ``counts[i]`` é quantos itens o alinhador gera para ``tokens[i]`` (0 para pontuação
    isolada, que recebe duração zero no fim da palavra anterior).
    """
    if len(tokens) != len(counts):
        raise ValueError("tokens e contagens com tamanhos diferentes")
    if sum(counts) != len(items):
        raise AlignmentMismatch(f"esperados {sum(counts)} itens do alinhador, recebidos {len(items)}")
    out: list[AsrWord] = []
    k, prev_end = 0, offset_us
    for tok, n in zip(tokens, counts):
        if n == 0:
            out.append(AsrWord(tok, prev_end, prev_end))
            continue
        span = items[k : k + n]
        k += n
        start = offset_us + round(float(span[0].start_time) * 1_000_000)
        end = max(offset_us + round(float(span[-1].end_time) * 1_000_000), start)
        out.append(AsrWord(tok, start, end))
        prev_end = end
    return out


def attach_punctuation(words: list[AsrWord]) -> list[AsrWord]:
    """Pontuação isolada (sem duração) vai para o fim da palavra anterior: "isso —" → "isso—"."""
    out: list[AsrWord] = []
    pending = ""
    for w in words:
        if w.end_us == w.start_us and not any(ch.isalnum() for ch in w.text):
            if out:
                last = out[-1]
                out[-1] = AsrWord(last.text + w.text, last.start_us, last.end_us, last.confidence)
            else:
                pending += w.text
            continue
        if pending:
            w = AsrWord(pending + w.text, w.start_us, w.end_us, w.confidence)
            pending = ""
        out.append(w)
    return out


class Qwen3ForcedAligner:
    """Alinhador forçado: texto conhecido + áudio → tempo de cada palavra."""

    max_region_us = MAX_ALIGN_US

    def __init__(
        self,
        model: str = "Qwen/Qwen3-ForcedAligner-0.6B",
        device: str = "cuda",
        *,
        batch_size: int = 4,
        impl: Any | None = None,
    ):
        if impl is None:
            import torch  # type: ignore
            from qwen_asr import Qwen3ForcedAligner as _Impl  # type: ignore

            dev = _cuda_device(device)
            dtype = torch.bfloat16 if dev.startswith("cuda") else torch.float32
            impl = _Impl.from_pretrained(model, dtype=dtype, device_map=dev)
        self._impl = impl
        self._batch = max(1, batch_size)
        self.info = EngineInfo("qwen3-forcedaligner-0.6b", "aligner", device, "Apache-2.0")

    def _counts(self, tokens: Sequence[str]) -> list[int]:
        proc = self._impl.aligner_processor
        return [len(proc.tokenize_space_lang(t)) for t in tokens]

    def align_many(self, samples, jobs: Sequence[tuple[TimeRange, str]], *, language: str = "pt") -> list[list[AsrWord]]:
        """Alinha vários trechos do mesmo áudio, em lotes. Cada trecho tem no máximo 180 s."""
        results: list[list[AsrWord]] = [[] for _ in jobs]
        todo = [(i, r, text) for i, (r, text) in enumerate(jobs) if text.split()]
        for r_i, r, _ in todo:
            if r.duration_us > MAX_ALIGN_US:
                raise ValueError(f"trecho {r_i} com {r.duration_us / 1e6:.0f} s; o alinhador aceita até 180 s")
        lang = qwen_language(language)
        for b in range(0, len(todo), self._batch):
            batch = todo[b : b + self._batch]
            out = self._impl.align(
                audio=[(cut(samples, r), SAMPLE_RATE) for _, r, _ in batch],
                text=[text for _, _, text in batch],
                language=lang,
            )
            for (i, r, text), res in zip(batch, out):
                tokens = text.split()
                results[i] = map_tokens_to_items(tokens, self._counts(tokens), list(res.items), r.start_us)
        return results

    def align(self, audio: Path, region: TimeRange, text: str, *, language: str = "pt") -> list[AsrWord]:
        return self.align_many(load_wav(audio), [(region, text)], language=language)[0]


class Qwen3Asr:
    """Reconhecimento com Qwen3-ASR; os tempos vêm do Qwen3-ForcedAligner (o mesmo do realinhamento)."""

    produces_aligned_timestamps = True  # o pipeline não alinha de novo

    def __init__(
        self,
        model: str = "Qwen/Qwen3-ASR-1.7B",
        device: str = "cuda",
        *,
        aligner: Qwen3ForcedAligner,
        batch_size: int = 4,
        max_new_tokens: int = 1024,
        segment_us: int = SEGMENT_US,
        impl: Any | None = None,
    ):
        if impl is None:
            import torch  # type: ignore
            from qwen_asr import Qwen3ASRModel  # type: ignore

            dev = _cuda_device(device)
            dtype = torch.bfloat16 if dev.startswith("cuda") else torch.float32
            impl = Qwen3ASRModel.from_pretrained(
                model,
                dtype=dtype,
                device_map=dev,
                max_inference_batch_size=batch_size,
                max_new_tokens=max_new_tokens,
            )
        self._impl = impl
        self._aligner = aligner
        self._segment_us = min(segment_us, MAX_ALIGN_US)
        name = model.rsplit("/", 1)[-1].lower()
        self.info = EngineInfo(name, "asr", device, "Apache-2.0")

    def transcribe(
        self, audio: Path, regions: list[TimeRange], *, language: str = "pt", context: str | None = None
    ) -> list[AsrWord]:
        if not regions:
            return []  # sem fala detectada: o modelo não roda
        samples = load_wav(audio)
        segments = plan_segments(samples, regions, max_len_us=self._segment_us)
        results = self._impl.transcribe(
            audio=[(cut(samples, s), SAMPLE_RATE) for s in segments],
            context=context or "",
            language=qwen_language(language),
            return_time_stamps=False,
        )
        texts = [(r.text or "").strip() for r in results]
        aligned = self._aligner.align_many(samples, list(zip(segments, texts)), language=language)
        words: list[AsrWord] = []
        for seg_words in aligned:
            words.extend(attach_punctuation(seg_words))
        return words
