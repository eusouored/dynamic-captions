"""SRT e WebVTT com tempos ajustados ao quadro da sequência (sem desvio acumulado)."""

from __future__ import annotations

from collections.abc import Sequence

from ..domain.models import CaptionBlock, Transcript
from ..domain.timecode import FrameRate, format_srt_time, format_vtt_time


def snap_blocks(blocks: Sequence[CaptionBlock], rate: FrameRate) -> list[tuple[CaptionBlock, int, int]]:
    """Ajusta início e fim ao quadro mais próximo, sem sobreposição e com no mínimo 1 quadro."""
    out = []
    prev_end = -1
    for b in blocks:
        f0 = max(rate.us_to_frame(b.start_us), prev_end)
        f1 = max(rate.us_to_frame(b.end_us), f0 + 1)
        out.append((b, rate.frame_to_us(f0), rate.frame_to_us(f1)))
        prev_end = f1
    return out


def _escape_vtt(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_srt(blocks: Sequence[CaptionBlock], transcript: Transcript, rate: FrameRate, *, speaker_names: bool = False) -> str:
    cues = []
    prev_speaker = None
    for n, (b, start, end) in enumerate(snap_blocks(blocks, rate), start=1):
        text = b.text
        if speaker_names and b.speaker_id != prev_speaker:
            sp = transcript.speaker(b.speaker_id)
            if sp is not None:
                text = f"[{sp.name}] {text}"
        prev_speaker = b.speaker_id
        cues.append(f"{n}\n{format_srt_time(start)} --> {format_srt_time(end)}\n{text}\n")
    return "\n".join(cues)


def to_vtt(blocks: Sequence[CaptionBlock], transcript: Transcript, rate: FrameRate, *, speaker_names: bool = False) -> str:
    cues = ["WEBVTT\n"]
    for b, start, end in snap_blocks(blocks, rate):
        text = _escape_vtt(b.text)
        sp = transcript.speaker(b.speaker_id)
        if speaker_names and sp is not None:
            text = f"<v {_escape_vtt(sp.name)}>{text}"
        cues.append(f"{format_vtt_time(start)} --> {format_vtt_time(end)}\n{text}\n")
    return "\n".join(cues)
