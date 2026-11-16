"""Comparação entre o motor principal e o verificador.

As duas hipóteses são normalizadas e alinhadas palavra a palavra, em janelas de tempo.
Resultado:
- palavras em que os modelos concordam ficam ``confirmed``;
- trechos divergentes viram ``Dispute`` (fila de revisão) e as palavras ficam ``disputed``;
- sequências de 3+ palavras que só o principal produziu são suspeitas de alucinação.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Literal, Protocol

from ..domain.ids import new_id
from ..domain.models import Agreement, Flag, Word
from .align import align_tokens
from .normalize import normalize_token

DisputeKind = Literal["substitution", "primary_only", "verifier_only"]


class TimedText(Protocol):
    text: str
    start_us: int
    end_us: int


@dataclass(frozen=True)
class Dispute:
    kind: DisputeKind
    # Palavras do principal envolvidas; em verifier_only, a âncora mais próxima, ou vazio quando
    # o principal não produziu nenhuma palavra (a divergência continua na fila de revisão).
    word_ids: tuple[str, ...]
    primary_text: str
    alternative_text: str
    start_us: int
    end_us: int
    suspected_hallucination: bool = False
    id: str = field(default_factory=new_id)


@dataclass
class CompareResult:
    words: list[Word]
    disputes: list[Dispute] = field(default_factory=list)

    @property
    def agreement_ratio(self) -> float:
        if not self.words:
            return 1.0
        ok = sum(1 for w in self.words if w.agreement is Agreement.CONFIRMED)
        return ok / len(self.words)


def _windows(words: Sequence[Word], max_words: int, gap_us: int) -> list[list[int]]:
    windows: list[list[int]] = []
    cur: list[int] = []
    for i, w in enumerate(words):
        if cur and (len(cur) >= max_words or w.start_us - words[cur[-1]].end_us >= gap_us):
            windows.append(cur)
            cur = []
        cur.append(i)
    if cur:
        windows.append(cur)
    return windows


def _runs_by_gap(items: Sequence[TimedText], gap_us: int) -> list[list[TimedText]]:
    runs: list[list[TimedText]] = []
    for it in items:
        if runs and it.start_us - runs[-1][-1].end_us < gap_us:
            runs[-1].append(it)
        else:
            runs.append([it])
    return runs


def compare_hypotheses(
    primary: Sequence[Word],
    verifier: Sequence[TimedText],
    *,
    max_window_words: int = 150,
    window_gap_us: int = 1_000_000,
    hallucination_run: int = 3,
) -> CompareResult:
    words = list(primary)
    if not words:
        heard = sorted((v for v in verifier if normalize_token(v.text)), key=lambda v: v.start_us)
        disputes = []
        for run in _runs_by_gap(heard, window_gap_us):
            disputes.append(
                Dispute(
                    kind="verifier_only",
                    word_ids=(),
                    primary_text="",
                    alternative_text=" ".join(v.text.strip() for v in run),
                    start_us=run[0].start_us,
                    end_us=run[-1].end_us,
                )
            )
        return CompareResult(words=[], disputes=disputes)
    windows = _windows(words, max_window_words, window_gap_us)
    # Fronteiras entre janelas: ponto médio entre o fim de uma e o início da seguinte.
    bounds = [
        (words[a[-1]].end_us + words[b[0]].start_us) // 2 for a, b in zip(windows, windows[1:])
    ]
    buckets: list[list[TimedText]] = [[] for _ in windows]
    k = 0
    for vw in sorted(verifier, key=lambda v: v.start_us):
        mid = (vw.start_us + vw.end_us) // 2
        while k < len(bounds) and mid >= bounds[k]:
            k += 1
        buckets[k].append(vw)

    agreement: dict[int, Agreement] = {}
    alternatives: dict[int, str] = {}
    disputes: list[Dispute] = []
    primary_only_runs: list[list[int]] = []

    for win, bucket in zip(windows, buckets):
        ref_idx = [i for i in win if normalize_token(words[i].text)]
        for i in win:
            if i not in ref_idx:
                agreement[i] = Agreement.CONFIRMED  # só pontuação
        hyp = [v for v in bucket if normalize_token(v.text)]
        edits = align_tokens(
            [normalize_token(words[i].text) for i in ref_idx],
            [normalize_token(v.text) for v in hyp],
        )
        run_p: list[int] = []
        run_h: list[int] = []

        def flush() -> None:
            if not run_p and not run_h:
                return
            alt = " ".join(hyp[j].text.strip() for j in run_h)
            if run_p:
                ids = [ref_idx[p] for p in run_p]
                kind: DisputeKind = "substitution" if run_h else "primary_only"
                for i in ids:
                    agreement[i] = Agreement.DISPUTED
                    alternatives[i] = alt
                if kind == "primary_only":
                    primary_only_runs.append(ids)
                disputes.append(
                    Dispute(
                        kind=kind,
                        word_ids=tuple(words[i].id for i in ids),
                        primary_text=" ".join(words[i].text for i in ids),
                        alternative_text=alt,
                        start_us=words[ids[0]].start_us,
                        end_us=words[ids[-1]].end_us,
                    )
                )
            else:
                # Palavras que só o verificador ouviu: ancora na palavra mais próxima no tempo.
                start, end = hyp[run_h[0]].start_us, hyp[run_h[-1]].end_us
                anchor = min(win, key=lambda i: abs(words[i].mid_us - (start + end) // 2))
                disputes.append(
                    Dispute(
                        kind="verifier_only",
                        word_ids=(words[anchor].id,),
                        primary_text="",
                        alternative_text=alt,
                        start_us=start,
                        end_us=end,
                    )
                )
            run_p.clear()
            run_h.clear()

        for e in edits:
            if e.op == "equal":
                flush()
                agreement[ref_idx[e.i]] = Agreement.CONFIRMED
            else:
                if e.i is not None:
                    run_p.append(e.i)
                if e.j is not None:
                    run_h.append(e.j)
        flush()

    suspects: set[int] = set()
    for run in primary_only_runs:
        if len(run) >= hallucination_run:
            suspects.update(run)
    suspect_ids = {words[i].id for i in suspects}
    disputes = [
        replace(d, suspected_hallucination=True)
        if d.kind == "primary_only" and set(d.word_ids) <= suspect_ids
        else d
        for d in disputes
    ]

    out: list[Word] = []
    for i, w in enumerate(words):
        ag = agreement.get(i, Agreement.SINGLE)
        flags = set(w.flags)
        alts = w.alternatives
        if ag is Agreement.DISPUTED:
            flags.add(Flag.LOW_AGREEMENT)
            alt = alternatives.get(i, "")
            alts = (alt,) if alt else ()
        if i in suspects:
            flags.add(Flag.HALLUCINATION_SUSPECT)
        out.append(w.model_copy(update={"agreement": ag, "flags": frozenset(flags), "alternatives": alts}))
    return CompareResult(words=out, disputes=disputes)


def open_disputes(stored: Sequence[dict], transcript, dismissed: set[str] | frozenset[str] = frozenset()) -> list[dict]:
    """Divergências que ainda pedem decisão, a partir das guardadas no projeto.

    - substituição ou só no principal: abertas enquanto alguma palavra envolvida seguir em disputa;
    - só no verificador, com âncora: aberta enquanto a âncora existir e não estiver travada;
    - só no verificador, sem âncora (o principal não produziu nada): aberta até uma pessoa
      escrever algo naquele trecho ou dispensar a divergência.
    """
    live = {w.id: w for w in transcript.words}
    out = []
    for i, d in enumerate(stored):
        did = d.get("id") or f"d{i}"
        if did in dismissed:
            continue
        ids = d.get("word_ids") or []
        if d["kind"] != "verifier_only":
            still = any(wid in live and live[wid].agreement is Agreement.DISPUTED for wid in ids)
        elif ids:
            anchor = live.get(ids[0])
            still = anchor is not None and not anchor.locked
        else:
            lo, hi = d["start_us"] - 200_000, d["end_us"] + 200_000
            still = not any(w.locked and w.end_us > lo and w.start_us < hi for w in transcript.words)
        if still:
            out.append({**d, "id": did})
    return out
