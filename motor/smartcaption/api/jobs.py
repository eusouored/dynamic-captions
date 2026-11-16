"""Fila de trabalhos: um de cada vez (GPU), com progresso, cancelamento e eventos."""

from __future__ import annotations

import asyncio
import sys
import threading
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from ..domain.ids import new_id

JobState = Literal["queued", "running", "done", "failed", "cancelled"]


class EventBus:
    """Repassa eventos de threads de trabalho para conexões WebSocket (asyncio)."""

    def __init__(self) -> None:
        self._subs: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._lock = threading.Lock()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._subs.append((asyncio.get_running_loop(), q))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs = [(loop, s) for loop, s in self._subs if s is not q]

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            subs = list(self._subs)
        for loop, q in subs:
            try:
                loop.call_soon_threadsafe(q.put_nowait, event)
            except RuntimeError:  # loop encerrado
                self.unsubscribe(q)


@dataclass
class Job:
    id: str
    kind: str
    project_id: str
    state: JobState = "queued"
    stage: str = ""
    progress: float = 0.0
    error: str | None = None
    result: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "project_id": self.project_id, "state": self.state,
            "stage": self.stage, "progress": round(self.progress, 3), "error": self.error,
            "result": self.result, "created_at": self.created_at,
        }


def release_cuda_cache() -> bool:
    """Devolve ao sistema a memória de GPU que o PyTorch reservou e não usa mais.

    Os modelos continuam carregados (a memória alocada não muda); só a reserva ociosa sai,
    o que deixa ~5 GiB livres para o Premiere entre trabalhos (medição: reserva de
    11,8 para 6,2 GiB em 0,02 s). Não importa o PyTorch se ele ainda não foi carregado e nunca
    deixa um erro aqui esconder o resultado do trabalho.
    """
    torch = sys.modules.get("torch")
    if torch is None:
        return False
    try:
        if torch.cuda.is_available() and torch.cuda.is_initialized():
            torch.cuda.empty_cache()
            return True
    except Exception:
        pass
    return False


class JobManager:
    def __init__(self, bus: EventBus, max_workers: int = 1):
        self._bus = bus
        self._jobs: dict[str, Job] = {}
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="sc-job")

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def active(self, project_id: str, kind: str) -> bool:
        return any(
            j.project_id == project_id and j.kind == kind and j.state in ("queued", "running")
            for j in list(self._jobs.values())
        )

    def _emit(self, job: Job) -> None:
        self._bus.publish({"type": "job", "job": job.public()})

    def submit(self, kind: str, project_id: str, fn: Callable[[Job, Callable[[str, float], None]], dict]) -> Job:
        job = Job(id=new_id(), kind=kind, project_id=project_id)
        self._jobs[job.id] = job
        self._emit(job)

        def progress(stage: str, value: float) -> None:
            job.stage, job.progress = stage, value
            self._emit(job)

        def run() -> None:
            if job._cancel.is_set():
                job.state = "cancelled"
                self._emit(job)
                return
            job.state = "running"
            self._emit(job)
            try:
                job.result = fn(job, progress) or {}
                job.state = "cancelled" if job._cancel.is_set() else "done"
                job.progress = 1.0 if job.state == "done" else job.progress
            except (Exception, SystemExit) as exc:  # o erro vai para o painel com mensagem legível (nunca "rodando" para sempre)
                from ..cancel import Cancelled

                if isinstance(exc, Cancelled):
                    job.state = "cancelled"
                else:
                    job.state, job.error = "failed", f"{type(exc).__name__}: {exc}"
                    job.result = {"trace": traceback.format_exc(limit=5)}
            finally:
                release_cuda_cache()
            self._emit(job)

        self._pool.submit(run)
        return job

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None or job.state in ("done", "failed", "cancelled"):
            return False
        job._cancel.set()
        return True

    def any_active(self) -> bool:
        return any(j.state in ("queued", "running") for j in list(self._jobs.values()))

    def cancel_all(self) -> None:
        """Pede o cancelamento de todos os trabalhos na fila ou em andamento (encerrar o motor)."""
        for job in list(self._jobs.values()):
            if job.state in ("queued", "running"):
                job._cancel.set()

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
