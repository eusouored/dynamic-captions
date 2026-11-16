"""Reanalisa só as vozes, preservando texto, tempos, IDs e decisões do editor."""

from collections import Counter

from ..domain.models import DEFAULT_SPEAKER_COLORS, Flag, Speaker, Transcript
from .speakers import assign_from_turns, speakers_from_turns


def reassign_speakers(transcript: Transcript, turns) -> Transcript:
    detected, mapping = speakers_from_turns(turns)
    clean = [w.model_copy(update={"flags": frozenset(set(w.flags) - {Flag.OVERLAP})})
             if not w.speaker_locked else w for w in transcript.words]
    assigned = assign_from_turns(clean, turns, mapping)
    scores = Counter()
    for old, new in zip(transcript.words, assigned):
        if old.speaker_id and new.speaker_id:
            scores[new.speaker_id, old.speaker_id] += max(old.duration_us, 1) * (100 if old.speaker_locked else 1)
    matches, used = {}, set()
    for (new_id, old_id), _weight in scores.most_common():
        if new_id not in matches and old_id not in used:
            matches[new_id] = old_id
            used.add(old_id)
    speakers = list(transcript.speakers)
    occupied = {s.id for s in speakers}
    present = {w.speaker_id for w in assigned}
    for speaker in detected:
        if speaker.id in matches or speaker.id not in present:
            continue
        n = 1
        while f"spk_{n}" in occupied:
            n += 1
        sid = f"spk_{n}"
        occupied.add(sid)
        matches[speaker.id] = sid
        speakers.append(Speaker(id=sid, label=f"Falante {n}", color=DEFAULT_SPEAKER_COLORS[(n-1) % len(DEFAULT_SPEAKER_COLORS)]))
    words = tuple(old if old.speaker_locked else new.model_copy(update={
        "speaker_id": matches.get(new.speaker_id, old.speaker_id)
    }) for old, new in zip(transcript.words, assigned))
    return transcript.model_copy(update={"rev": transcript.rev + 1, "words": words, "speakers": tuple(speakers)})
