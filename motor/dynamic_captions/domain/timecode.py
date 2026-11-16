"""Conversões de tempo: microssegundos, ticks do Premiere, quadros e timecode.

O Premiere usa 254.016.000.000 ticks por segundo, então 1 µs = 254.016 ticks (inteiro exato).
Taxas de quadro são racionais (24000/1001 para 23,976) para não acumular desvio.
"""

from __future__ import annotations

from dataclasses import dataclass

US_PER_SECOND = 1_000_000


@dataclass(frozen=True)
class FrameRate:
    num: int
    den: int = 1

    def __post_init__(self) -> None:
        if self.num <= 0 or self.den <= 0:
            raise ValueError("taxa de quadros inválida")

    @classmethod
    def parse(cls, value: str | float) -> "FrameRate":
        """Aceita '23.976', '29.97', '59.94', '25', '30000/1001'."""
        if isinstance(value, str) and "/" in value:
            n, d = value.split("/")
            return cls(int(n), int(d))
        f = float(value)
        for nominal in (24, 30, 48, 60, 120):
            if abs(f - nominal * 1000 / 1001) < 0.005:
                return cls(nominal * 1000, 1001)
        if abs(f - round(f)) < 1e-6:
            return cls(int(round(f)), 1)
        raise ValueError(f"taxa de quadros não reconhecida: {value}")

    @property
    def fps(self) -> float:
        return self.num / self.den

    @property
    def nominal(self) -> int:
        """Quadros por segundo usados no timecode (30 para 29,97)."""
        return round(self.num / self.den)

    def us_to_frame(self, us: int) -> int:
        """Índice do quadro mais próximo do instante dado."""
        denom = self.den * US_PER_SECOND
        return (us * self.num + denom // 2) // denom

    def frame_to_us(self, frame: int) -> int:
        """Instante (µs, arredondado) em que o quadro começa."""
        num = frame * self.den * US_PER_SECOND
        return (num + self.num // 2) // self.num


def frames_to_timecode(frame: int, rate: FrameRate, drop_frame: bool = False) -> str:
    """Timecode SMPTE. Drop-frame só vale para 29,97 e 59,94 (separador ';')."""
    if frame < 0:
        raise ValueError("quadro negativo")
    fps = rate.nominal
    if drop_frame:
        if rate.den != 1001 or fps not in (30, 60):
            raise ValueError("drop-frame só existe para 29,97 e 59,94")
        drop = 2 if fps == 30 else 4
        frames_per_10min = fps * 600 - drop * 9
        frames_per_min = fps * 60 - drop
        tens, rem = divmod(frame, frames_per_10min)
        if rem > drop:
            frame += drop * 9 * tens + drop * ((rem - drop) // frames_per_min)
        else:
            frame += drop * 9 * tens
    ff = frame % fps
    total_seconds = frame // fps
    ss = total_seconds % 60
    mm = (total_seconds // 60) % 60
    hh = total_seconds // 3600
    sep = ";" if drop_frame else ":"
    return f"{hh:02d}:{mm:02d}:{ss:02d}{sep}{ff:02d}"


def format_srt_time(us: int) -> str:
    ms = (us + 500) // 1000
    hh, rem = divmod(ms, 3_600_000)
    mm, rem = divmod(rem, 60_000)
    ss, ms = divmod(rem, 1000)
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"


def format_vtt_time(us: int) -> str:
    return format_srt_time(us).replace(",", ".")


def format_ass_time(us: int) -> str:
    cs = (us + 5000) // 10_000
    hh, rem = divmod(cs, 360_000)
    mm, rem = divmod(rem, 6000)
    ss, cs = divmod(rem, 100)
    return f"{hh:d}:{mm:02d}:{ss:02d}.{cs:02d}"
