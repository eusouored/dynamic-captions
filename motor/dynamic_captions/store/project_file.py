"""Arquivo de projeto SQLite: metadados, revisões da transcrição e log de operações.

Cada revisão é guardada inteira (JSON). Para o arquivo não crescer sem limite (~0,5 MB por
edição numa transcrição de 2000 palavras), ficam só a primeira revisão (a transcrição original) e as
últimas ``KEEP_REVISIONS``; o log de operações continua completo.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from ..domain.ids import new_id
from ..domain.models import SequenceInfo, Transcript
from ..domain.ops import OperationRecord, RevisionConflict

SCHEMA_VERSION = 1
KEEP_REVISIONS = 30

_DDL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS revisions (
    rev INTEGER PRIMARY KEY,
    transcript TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS operations (
    rev INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL,
    author TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (rev, seq)
);
"""


class ProjectFile:
    def __init__(self, path: Path):
        self.path = Path(path)
        # Uma conexão compartilhada entre threads: todo acesso passa pela trava (leitura inclusive).
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        try:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(_DDL)
            self._db.commit()
        except Exception:
            self._db.close()  # no Windows, um arquivo aberto não pode ir para a quarentena
            raise

    # --- criação e metadados -------------------------------------------------
    @classmethod
    def create(cls, folder: Path, name: str, *, media_path: str | None = None, sequence: SequenceInfo | None = None) -> "ProjectFile":
        project_id = new_id()
        # Uma queda entre o esquema e os metadados deixa um arquivo sem id: o motor o põe em
        # quarentena ao abrir (api.app), em vez de deixar de subir.
        pf = cls(Path(folder) / f"{project_id}.scproj")
        meta = {
            "schema_version": SCHEMA_VERSION,
            "id": project_id,
            "name": name,
            "media_path": media_path,
            "sequence": (sequence or SequenceInfo()).model_dump(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with pf._lock, pf._db:
            pf._db.executemany("INSERT OR REPLACE INTO meta VALUES (?, ?)", [(k, json.dumps(v)) for k, v in meta.items()])
        return pf

    def meta(self) -> dict:
        with self._lock:
            rows = self._db.execute("SELECT key, value FROM meta").fetchall()
        return {k: json.loads(v) for k, v in rows}

    @property
    def id(self) -> str:
        pid = self.meta().get("id")
        if not isinstance(pid, str) or not pid:
            raise ValueError(f"{self.path.name}: projeto sem id (arquivo incompleto)")
        return pid

    def sequence(self) -> SequenceInfo:
        return SequenceInfo.model_validate(self.meta()["sequence"])

    # --- revisões ------------------------------------------------------------
    def save_revision(self, transcript: Transcript, records: list[OperationRecord] | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            try:
                with self._db:
                    self._write_revision(transcript, records or [], now)
            except sqlite3.IntegrityError as exc:  # outra gravação chegou antes com a mesma revisão
                raise RevisionConflict(transcript.rev - 1, self.latest_rev() or 0) from exc

    def _write_revision(self, transcript: Transcript, records: list[OperationRecord], now: str) -> None:
        self._db.execute("INSERT INTO revisions VALUES (?, ?, ?)", (transcript.rev, transcript.model_dump_json(), now))
        # Guarda a original (menor revisão) e as últimas KEEP_REVISIONS; o espaço liberado é reaproveitado.
        self._db.execute(
            "DELETE FROM revisions WHERE rev > (SELECT MIN(rev) FROM revisions) AND rev <= ?",
            (transcript.rev - KEEP_REVISIONS,),
        )
        for r in records:
            self._db.execute(
                "INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?)",
                (r.rev, r.seq, r.kind, r.author.value, json.dumps(r.payload, ensure_ascii=False), r.created_at),
            )

    def set_meta(self, key: str, value) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, json.dumps(value, ensure_ascii=False)))

    def latest_rev(self) -> int | None:
        with self._lock:
            row = self._db.execute("SELECT MAX(rev) FROM revisions").fetchone()
        return row[0]

    def load(self, rev: int | None = None) -> Transcript | None:
        with self._lock:
            if rev is None:
                row = self._db.execute("SELECT transcript FROM revisions ORDER BY rev DESC LIMIT 1").fetchone()
            else:
                row = self._db.execute("SELECT transcript FROM revisions WHERE rev = ?", (rev,)).fetchone()
        return Transcript.model_validate_json(row[0]) if row else None

    def operations(self, since_rev: int = 0) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT rev, seq, kind, author, payload, created_at FROM operations WHERE rev > ? ORDER BY rev, seq",
                (since_rev,),
            ).fetchall()
        return [
            {"rev": r, "seq": s, "kind": k, "author": a, "payload": json.loads(p), "created_at": c}
            for r, s, k, a, p, c in rows
        ]

    def close(self) -> None:
        with self._lock:
            self._db.close()
