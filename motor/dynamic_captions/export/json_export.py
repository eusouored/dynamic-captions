"""JSON por palavra para outras ferramentas e para o painel (schema ``dynamic-captions-transcript-v1``)."""

from __future__ import annotations

import json
from collections.abc import Sequence

from ..domain.models import CaptionBlock, Transcript

SCHEMA = "dynamic-captions-transcript-v1"


def to_json(transcript: Transcript, blocks: Sequence[CaptionBlock] | None = None, *, indent: int | None = 2) -> str:
    data = {
        "schema": SCHEMA,
        "rev": transcript.rev,
        "language": transcript.language,
        "speakers": [s.model_dump(mode="json") for s in transcript.speakers],
        "words": [
            {**w.model_dump(mode="json", exclude={"flags"}), "flags": sorted(f.value for f in w.flags)}
            for w in transcript.words
        ],
    }
    if blocks is not None:
        data["blocks"] = [
            {"id": b.id, "start_us": b.start_us, "end_us": b.end_us, "speaker_id": b.speaker_id,
             "lines": [line.text for line in b.lines], "word_ids": list(b.word_ids), "flags": sorted(b.flags)}
            for b in blocks
        ]
    return json.dumps(data, ensure_ascii=False, indent=indent)
