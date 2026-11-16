"""Orquestração: áudio → VAD → ASR → alinhamento → verificador → falantes → filtros → Transcript."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..domain.models import Agreement, Flag, Speaker, Timing, Transcript, Word
from ..engines.base import Aligner, AsrEngine, AsrWord, Diarizer, TimeRange, VadEngine
from . import hallucination
from .compare import Dispute, compare_hypotheses
from .normalize import normalize_token
from .speakers import assign_from_tracks, assign_from_turns, speakers_from_turns

ProgressFn = Callable[[str, float], None]


from ..cancel import Cancelled  # noqa: E402  (reexportado: pipeline.run.Cancelled continua válido)


@dataclass
class PipelineEngines:
    vad: VadEngine
    primary: AsrEngine
    verifier: AsrEngine | None = None
    aligner: Aligner | None = None
    diarizer: Diarizer | None = None
    # Converte a mídia no WAV de trabalho (motores reais); None = o arquivo já serve.
    prepare: Callable[[Path], Path] | None = None
    # Avisos da montagem dos motores (ex.: verificador indisponível), repassados ao resultado.
    warnings: list[str] = field(default_factory=list)


@dataclass
class TranscribeOptions:
    language: str = "pt"
    dictionary: list[str] = field(default_factory=list)  # nomes, marcas, jargão
    num_speakers: int | None = None
    min_speakers: int | None = None
    max_speakers: int | None = None
    track_envelopes: Mapping[str, Sequence[float]] | None = None  # uma faixa por pessoa
    envelope_hop_us: int = 10_000

    @property
    def context(self) -> str | None:
        return ", ".join(self.dictionary) if self.dictionary else None


@dataclass
class TranscribeResult:
    transcript: Transcript
    disputes: list[Dispute]
    regions: list[TimeRange]
    engines_used: dict[str, str]
    warnings: list[str] = field(default_factory=list)
    audio: Path | None = None  # WAV de trabalho efetivamente usado (para ouvir trechos na revisão)


STAGES = ("audio", "vad", "asr", "align", "verify", "speakers", "quality")


def _to_words(asr: Sequence[AsrWord], timing: Timing) -> list[Word]:
    return [
        Word(text=w.text.strip(), start_us=w.start_us, end_us=max(w.end_us, w.start_us), confidence=w.confidence, timing=timing)
        for w in sorted(asr, key=lambda w: w.start_us)
        if w.text.strip()
    ]


def _realign(audio: Path, words: list[Word], regions: list[TimeRange], aligner: Aligner, language: str) -> list[Word]:
    """Realinha por região de voz, em pedaços que respeitam o limite do alinhador."""
    out = list(words)
    for region in regions:
        idx = [i for i, w in enumerate(out) if region.contains(w.mid_us)]
        chunks: list[list[int]] = []
        for i in idx:
            if chunks and out[i].end_us - out[chunks[-1][0]].start_us <= aligner.max_region_us:
                chunks[-1].append(i)
            else:
                chunks.append([i])
        for chunk in chunks:
            span = TimeRange(max(region.start_us, out[chunk[0]].start_us - 200_000), min(region.end_us, out[chunk[-1]].end_us + 200_000))
            aligned = aligner.align(audio, span, " ".join(out[i].text for i in chunk), language=language)
            if len(aligned) != len(chunk):
                continue  # tokenização divergente: mantém os tempos nativos
            for i, a in zip(chunk, aligned):
                out[i] = out[i].model_copy(update={"start_us": a.start_us, "end_us": max(a.end_us, a.start_us), "timing": Timing.ALIGNED})
    out.sort(key=lambda w: w.start_us)
    return out


def _flag_dictionary(words: list[Word], dictionary: Sequence[str]) -> list[Word]:
    terms = {normalize_token(t) for term in dictionary for t in term.split()}
    terms.discard("")
    if not terms:
        return words
    return [
        w.model_copy(update={"flags": frozenset(set(w.flags) | {Flag.DICTIONARY_TERM})})
        if normalize_token(w.text) in terms
        else w
        for w in words
    ]


def transcribe(
    audio: Path,
    engines: PipelineEngines,
    options: TranscribeOptions | None = None,
    *,
    progress: ProgressFn | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> TranscribeResult:
    opts = options or TranscribeOptions()

    def step(stage: str) -> None:
        if should_cancel and should_cancel():
            raise Cancelled(stage)
        if progress:
            progress(stage, STAGES.index(stage) / len(STAGES))

    used = {"vad": engines.vad.info.name, "primary": engines.primary.info.name}
    warnings = list(engines.warnings)

    step("audio")
    if engines.prepare is not None:
        audio = engines.prepare(audio)

    step("vad")
    regions = sorted(engines.vad.detect(audio), key=lambda r: r.start_us)
    if not regions:
        # Sem voz detectada, nenhum modelo de reconhecimento roda: no silêncio, ASR inventa texto.
        for stage in STAGES[STAGES.index("vad") + 1 :]:
            step(stage)
        if progress:
            progress("done", 1.0)
        return TranscribeResult(
            transcript=Transcript(rev=0, language=opts.language),
            disputes=[],
            regions=[],
            engines_used=used,
            warnings=warnings + ["nenhuma fala detectada"],
            audio=audio,
        )

    step("asr")
    already_aligned = bool(getattr(engines.primary, "produces_aligned_timestamps", False))
    words = _to_words(
        engines.primary.transcribe(audio, regions, language=opts.language, context=opts.context),
        Timing.ALIGNED if already_aligned else Timing.NATIVE,
    )

    step("align")
    if engines.aligner is not None:
        if not already_aligned:  # o principal já usou o mesmo alinhador: não alinhar duas vezes
            words = _realign(audio, words, regions, engines.aligner, opts.language)
        used["aligner"] = engines.aligner.info.name

    step("verify")
    disputes: list[Dispute] = []
    if engines.verifier is not None:
        verifier_words = engines.verifier.transcribe(audio, regions, language=opts.language, context=opts.context)
        cmp = compare_hypotheses(words, verifier_words)
        words, disputes = cmp.words, cmp.disputes
        used["verifier"] = engines.verifier.info.name
    else:
        words = [w.model_copy(update={"agreement": Agreement.SINGLE}) for w in words]

    step("speakers")
    speakers: list[Speaker]
    if opts.track_envelopes:
        words, speakers = assign_from_tracks(words, opts.track_envelopes, hop_us=opts.envelope_hop_us)
        used["speakers"] = "tracks"
    elif engines.diarizer is not None and opts.num_speakers != 1:
        turns = engines.diarizer.diarize(
            audio, num_speakers=opts.num_speakers, min_speakers=opts.min_speakers, max_speakers=opts.max_speakers
        )
        speakers, mapping = speakers_from_turns(turns)
        words = assign_from_turns(words, turns, mapping)
        used["speakers"] = engines.diarizer.info.name
    else:
        speakers = [Speaker(id="spk_1", label="Falante 1", color="#FFD400")]
        words = [w.model_copy(update={"speaker_id": "spk_1"}) for w in words]
        used["speakers"] = "single"

    step("quality")
    words = hallucination.apply_all(words, regions)
    words = _flag_dictionary(words, opts.dictionary)

    transcript = Transcript(rev=0, language=opts.language, words=tuple(words), speakers=tuple(speakers))
    if progress:
        progress("done", 1.0)
    return TranscribeResult(transcript=transcript, disputes=disputes, regions=list(regions), engines_used=used, warnings=warnings, audio=audio)
