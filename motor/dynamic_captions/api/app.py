"""Aplicação FastAPI do motor local.

Segurança (o motor só escuta em 127.0.0.1):
- cabeçalho ``X-DC-Token`` obrigatório (WebSocket: ``?token=``), exceto em ``/v1/health``;
- ``Host`` precisa ser 127.0.0.1 ou localhost (defesa contra DNS rebinding);
- sem CORS: navegadores comuns não conseguem chamar a API.
"""

from __future__ import annotations

import io
import json
import logging
import os
import secrets
import sqlite3
import threading
import wave
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from datetime import datetime
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .. import __version__
from ..domain.grouping import build_blocks
from ..domain.models import SequenceInfo
from ..domain.ops import Author, LockedWordError, OpError, RevisionConflict, apply_ops, parse_ops
from ..domain.presets import PRESETS, get_preset
from ..domain.timecode import FrameRate
from ..engines.factory import EngineCache, EngineSettings
from ..engines.profiles import HardwareInfo, Profile, choose_profile, detect_hardware
from ..export.json_export import to_json
from ..export.subtitles import to_srt, to_vtt
from ..pipeline.compare import open_disputes
from ..pipeline.run import PipelineEngines, TranscribeOptions, transcribe
from ..render.ass import compile_ass
from ..render.ffmpeg import find_ffmpeg
from ..render.layout import preset_for_frame
from ..render.animated import clips_as_dicts, render_animated
from ..render.style import LEGACY_STYLE_KEYS, STYLE_PRESETS, StyleLayers, StyleSpec, WordStyle, style_catalog as catalog_entries
from ..system_info import find_audio_presets, list_fonts
from ..store import limpeza
from ..store.project_file import ProjectFile
from .jobs import EventBus, JobManager

ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
PORT_RANGE = range(47310, 47315)

EnginesFactory = Callable[[Profile], PipelineEngines]


class PathsBody(BaseModel):
    paths: list[str] = Field(default_factory=list, max_length=5000)


class CreateProject(BaseModel):
    name: str
    media_path: str | None = None
    sequence: SequenceInfo | None = None
    # Trecho da sequência que foi transcrito (In/Out ou sequência inteira), em µs desde o
    # início da sequência. Os tempos da transcrição são relativos a range_start_us.
    range_start_us: int = Field(0, ge=0)
    range_end_us: int | None = Field(None, ge=0)
    sequence_guid: str | None = None


class RenderBody(BaseModel):
    content_end_us: int | None = Field(None, gt=0)  # fim do último clipe da sequência (sem as legendas)


class ProjectStyle(BaseModel):
    preset: str = "karaoke_social"
    reading: str | None = None  # None = conforme a orientação do quadro
    overrides: dict[str, Any] = Field(default_factory=dict)
    word_overrides: dict[str, WordStyle] = Field(default_factory=dict, max_length=20000)

    @field_validator("overrides")
    @classmethod
    def _drop_legacy(cls, v: dict[str, Any]) -> dict[str, Any]:
        # Projetos de até 0.2.11 guardam a hierarquia (composição/palavra principal): não existe mais.
        return {k: x for k, x in v.items() if k not in LEGACY_STYLE_KEYS}

    @field_validator("word_overrides")
    @classmethod
    def _drop_empty_words(cls, v: dict[str, WordStyle]) -> dict[str, WordStyle]:
        # Palavra que só era "principal" fica sem ajuste nenhum: não guarda (nem força o caminho rico).
        empty = WordStyle()
        return {k: w for k, w in v.items() if w != empty}


class AppliedState(BaseModel):
    mode: Literal["animated", "native"]
    rev: int
    style_key: str
    clips: list[dict] = Field(default_factory=list)  # manifesto confirmado pelo Premiere
    sequence_guid: str | None = None
    track_index: int | None = None


class OpsRequest(BaseModel):
    base_rev: int
    author: Author = Author.USER
    ops: list[dict]


class TranscribeJobOptions(BaseModel):
    language: str = "pt"
    dictionary: list[str] = Field(default_factory=list)
    num_speakers: int | None = Field(None, ge=1, le=16)
    min_speakers: int | None = Field(None, ge=1, le=16)
    max_speakers: int | None = Field(None, ge=1, le=16)


class DiarizeRequest(BaseModel):
    num_speakers: int | None = Field(None, ge=1, le=16)
    context_audio_path: str | None = None
    context_start_us: int = Field(0, ge=0)


class JobRequest(BaseModel):
    kind: Literal["transcribe"]
    project_id: str
    audio_path: str
    options: TranscribeJobOptions = TranscribeJobOptions()


class ExportRequest(BaseModel):
    format: Literal["srt", "vtt", "json", "ass"]
    preset: str = "legenda_tradicional"
    style: str = "classico"
    speaker_names: bool = False
    content_end_us: int | None = Field(None, gt=0)  # fim do conteúdo da sequência agora (ver RenderBody)



def with_labels(clips: list[dict], project_name: str, seq) -> list[dict]:
    """Nome legível de cada clipe no painel Projeto do Premiere (o arquivo continua DC_<hash>.mov).

    Ex.: "react2 · 003 · 00;00;04;12 · Quando a gente sentiu isso…"
    """
    from ..domain.timecode import FrameRate, frames_to_timecode

    rate = FrameRate(seq.fps_num, seq.fps_den)
    out = []
    for i, c in enumerate(clips, 1):
        text = " ".join(str(c.get("text") or "").split())
        short = text if len(text) <= 40 else text[:39].rstrip() + "…"
        try:
            tc = frames_to_timecode(rate.us_to_frame(c["seq_start_us"]), rate, drop_frame=bool(seq.drop_frame))
        except Exception:  # noqa: BLE001 - o nome é só um rótulo
            tc = ""
        parts = [project_name.strip()[:40], f"{i:03d}", tc, short]
        out.append({**c, "label": " · ".join(p for p in parts if p)})
    return out

def quarantine_project(path: Path, exc: BaseException) -> None:
    """Move um .scproj ilegível para quarentena/ (com os arquivos -wal/-shm) e registra o motivo."""
    logging.getLogger("dynamic_captions").warning("projeto ignorado (%s): %s", path.name, exc)
    dest = path.parent / "quarentena"
    try:
        dest.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        for extra in ("", "-wal", "-shm"):
            src = Path(str(path) + extra)
            if src.exists():
                src.replace(dest / f"{path.stem}.{stamp}{path.suffix}{extra}")
    except OSError:
        pass  # sem mover, o arquivo só fica de fora desta sessão


def create_app(
    workspace: Path,
    *,
    token: str | None = None,
    engines_factory: EnginesFactory | None = None,
    hardware: HardwareInfo | None = None,
    allow_noncommercial: bool = False,
) -> FastAPI:
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    if engines_factory is None:
        # Motores reais, carregados no primeiro trabalho e mantidos na memória.
        engines_factory = EngineCache(EngineSettings.from_env(workspace.parent))
    token = token or secrets.token_urlsafe(32)
    bus = EventBus()
    jobs = JobManager(bus)
    projects: dict[str, ProjectFile] = {}
    edit_locks: dict[str, threading.Lock] = {}
    edit_locks_guard = threading.Lock()

    def edit_lock(pid: str) -> threading.Lock:
        with edit_locks_guard:
            return edit_locks.setdefault(pid, threading.Lock())

    # Um arquivo danificado (queda de energia, disco cheio) não pode impedir o motor de subir:
    # ele vai para quarentena/ e os outros projetos continuam abrindo.
    for path in sorted(workspace.glob("*.scproj")):
        pf = None
        try:
            pf = ProjectFile(path)
            projects[pf.id] = pf
        except Exception as exc:  # noqa: BLE001 - qualquer falha de leitura vale para a quarentena
            if pf is not None:
                try:
                    pf.close()
                except Exception:  # noqa: BLE001
                    pass
            if isinstance(exc, sqlite3.OperationalError) and ("locked" in str(exc) or "busy" in str(exc)):
                # Arquivo bom, só ocupado agora (antivírus, OneDrive): fica de fora desta sessão.
                logging.getLogger("dynamic_captions").warning("projeto ocupado, ignorado nesta sessão: %s", path.name)
                continue
            quarantine_project(path, exc)

    try:  # prévias, quadros e descartados vencidos saem sozinhos a cada início do motor
        limpeza.prune(workspace)
    except OSError:
        pass

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        jobs.shutdown()
        for pf in projects.values():
            pf.close()

    app = FastAPI(title="Dynamic Captions Engine", version=__version__, lifespan=lifespan)
    app.state.token = token
    app.state.jobs = jobs

    # O painel CEP do Premiere carrega a interface de um arquivo local (origem "null") e chama
    # a API com o token. Só essa origem recebe CORS; páginas da web comuns continuam sem acesso.
    cors_headers = {
        "Access-Control-Allow-Origin": "null",
        "Access-Control-Allow-Headers": "X-DC-Token, Content-Type",
        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE",
        "Vary": "Origin",
    }

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
        if host not in ALLOWED_HOSTS:
            return JSONResponse({"detail": "host não permitido"}, status_code=403)
        local_page = request.headers.get("origin") == "null"
        if request.method == "OPTIONS" and local_page:
            return Response(status_code=204, headers=cors_headers)
        public = request.url.path == "/v1/health" or request.url.path == "/" or request.url.path.startswith("/ui")
        # <audio> e <img> não mandam cabeçalho: nesses dois GETs o token pode vir na URL.
        given = request.headers.get("x-dc-token", "")
        if not given and request.method == "GET" and request.url.path.endswith(("/audio", "/preview.png", "/preview.mp4", "/frame.jpg")):
            given = request.query_params.get("token", "")
        if not public and not secrets.compare_digest(given.encode("utf-8"), token.encode("utf-8")):
            resp = JSONResponse({"detail": "token ausente ou inválido"}, status_code=401)
        else:
            resp = await call_next(request)
        if local_page:
            resp.headers.update(cors_headers)
        return resp

    def project(pid: str) -> ProjectFile:
        pf = projects.get(pid)
        if pf is None:
            raise HTTPException(404, "projeto não encontrado")
        return pf

    def transcript_or_404(pf: ProjectFile, rev: int | None = None):
        t = pf.load(rev)
        if t is None:
            # Revisões antigas são descartadas (ficam a original e as últimas); a atual sempre existe.
            raise HTTPException(404, "projeto ainda sem transcrição" if rev is None or pf.latest_rev() is None else f"revisão {rev} não disponível")
        return t

    @app.get("/v1/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__}

    # ---- aquecimento: modelos, fontes e presets carregam assim que o motor sobe, em segundo
    # plano, para que o primeiro "Legendar" não espere por eles.
    warm: dict[str, Any] = {"models": "idle", "error": None, "seconds": None}

    def warmup() -> None:
        def run() -> None:
            import time as _time
            t0 = _time.perf_counter()
            warm["models"] = "loading"
            try:
                find_audio_presets()
                list_fonts()
                profile = choose_profile(hardware or detect_hardware(), allow_noncommercial=allow_noncommercial)
                engines_factory(profile)
                warm["models"] = "ready"
            except Exception as exc:  # o trabalho real repete e mostra o erro completo
                warm["models"], warm["error"] = "error", f"{type(exc).__name__}: {exc}"
            warm["seconds"] = round(_time.perf_counter() - t0, 2)
        threading.Thread(target=run, name="dynamic-captions-warmup", daemon=True).start()

    app.state.warmup = warmup

    @app.get("/v1/engine/status")
    def engine_status() -> dict[str, Any]:
        return {"version": __version__, **warm}

    # O painel encerra um motor de versão diferente antes de iniciar o novo (atualização sem
    # precisar abrir o Gerenciador de Tarefas). Trabalhos em andamento são cancelados.
    app.state.exit = os._exit

    @app.post("/v1/engine/shutdown")
    def engine_shutdown() -> dict[str, Any]:
        def bye() -> None:
            import time as _time
            _time.sleep(0.3)  # deixa a resposta sair
            try:
                # O cancelamento é cooperativo (entre pedaços do render): espera um pouco para os
                # trabalhos pararem e limparem os arquivos parciais antes de encerrar o processo.
                jobs.cancel_all()
                for _ in range(30):
                    if not jobs.any_active():
                        break
                    _time.sleep(0.1)
                jobs.shutdown()
            except Exception:
                pass
            for pf in list(projects.values()):
                try:
                    pf.close()
                except Exception:
                    pass
            app.state.exit(0)
        threading.Thread(target=bye, name="dynamic-captions-shutdown", daemon=True).start()
        return {"ok": True}

    @app.get("/v1/style-catalog")
    def style_catalog():
        return {"version":1,"presets":catalog_entries()}

    @app.get("/v1/capabilities")
    def capabilities() -> dict[str, Any]:
        hw = hardware or detect_hardware()
        profile = choose_profile(hw, allow_noncommercial=allow_noncommercial)
        return {
            "hardware": asdict(hw),
            "profile": asdict(profile),
            "allow_noncommercial": allow_noncommercial,
            "reading_presets": [p.model_dump() for p in PRESETS.values()],
            "style_presets": [{"id": s.id, "name": s.name} for s in STYLE_PRESETS.values()],
        }

    @app.post("/v1/projects", status_code=201)
    def create_project(body: CreateProject) -> dict[str, Any]:
        if body.range_end_us is not None and body.range_end_us <= body.range_start_us:
            raise HTTPException(422, "fim do trecho antes do início")
        pf = ProjectFile.create(workspace, body.name, media_path=body.media_path, sequence=body.sequence)
        pf.set_meta("range", {"start_us": body.range_start_us, "end_us": body.range_end_us})
        if body.sequence_guid:
            pf.set_meta("sequence_guid", body.sequence_guid)
        projects[pf.id] = pf
        return pf.meta()

    @app.get("/v1/projects")
    def list_projects() -> list[dict[str, Any]]:
        return [{**pf.meta(), "rev": pf.latest_rev()} for pf in projects.values()]

    @app.get("/v1/projects/{pid}/transcript")
    def get_transcript(pid: str, rev: int | None = None) -> dict[str, Any]:
        return transcript_or_404(project(pid), rev).model_dump(mode="json")

    @app.post("/v1/projects/{pid}/ops")
    def post_ops(pid: str, body: OpsRequest) -> dict[str, Any]:
        pf = project(pid)
        t = transcript_or_404(pf)
        # Concorrência otimista: a revisão é a chave primária no banco, então de duas edições
        # sobre a mesma base só uma grava; a outra recebe 409 (nunca 500) e recarrega.
        try:
            ops = parse_ops(body.ops)
            new_t, records = apply_ops(t, ops, base_rev=body.base_rev, author=body.author)
            pf.save_revision(new_t, records)
        except RevisionConflict as exc:
            raise HTTPException(409, {"reason": "revision", "message": str(exc), "current_rev": exc.actual}) from exc
        except LockedWordError as exc:
            raise HTTPException(409, {"reason": "locked", "message": str(exc), "word_ids": exc.word_ids}) from exc
        except (OpError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        bus.publish({"type": "revision", "project_id": pid, "rev": new_t.rev})
        return {"rev": new_t.rev}

    def blocks_for(pf: ProjectFile, t, preset_id: str, style_id: str, content_end_us: int | None = None):
        seq = pf.sequence()
        excluded = {sp.id for sp in t.speakers if not sp.include_in_captions}
        if excluded:
            # A transcrição/revisão continua completa. Só a saída visual é filtrada.
            t = t.model_copy(update={"words": tuple(w for w in t.words if w.speaker_id not in excluded)})
        reading = None
        if style_id == "projeto":
            layers, reading = project_style(pf)
        else:
            layers = STYLE_PRESETS.get(style_id)
        try:
            preset = get_preset(reading or "legenda_tradicional" if preset_id == "auto" else preset_id)
        except KeyError as exc:
            raise HTTPException(422, str(exc)) from exc
        if layers is None:
            raise HTTPException(422, f"estilo desconhecido: {style_id}")
        # Linhas por legenda vêm do estilo; o que não cabe na largura (medida com a fonte real)
        # vira um novo trecho em vez de encolher a letra.
        from ..render.typography import line_fit
        fitted = preset.model_copy(update={"max_lines": layers.base.max_lines})
        rng = pf.meta().get("range") or {}
        limit = rng["end_us"] - rng.get("start_us", 0) if rng.get("end_us") is not None else None
        if content_end_us is not None:  # fim do conteúdo da sequência agora (pode ter sido encurtada)
            now = content_end_us - rng.get("start_us", 0)
            if now <= 0:
                raise HTTPException(422, "a sequência agora termina antes do início do trecho legendado: "
                                         "nada a legendar (confira o In/Out ou legende de novo)")
            limit = now if limit is None else min(limit, now)
        fit = line_fit(layers.base, seq.width, seq.height) if layers.base.text_mode != "word" else None
        return build_blocks(t, fitted, fit=fit, limit_us=limit), fitted, layers

    def project_style(pf: ProjectFile) -> tuple[StyleLayers, str]:
        meta = ProjectStyle.model_validate(pf.meta().get("style") or {})
        base_layers = STYLE_PRESETS.get(meta.preset) or STYLE_PRESETS["karaoke_social"]
        try:
            spec = StyleSpec.model_validate({**base_layers.base.model_dump(), **meta.overrides})
        except ValueError as exc:
            raise HTTPException(422, f"estilo inválido: {exc}") from exc
        seq = pf.sequence()
        reading = meta.reading or ("redes_sociais" if seq.height > seq.width else "legenda_tradicional")
        return StyleLayers(id="projeto", name=base_layers.name, base=spec, words=meta.word_overrides), reading

    @app.get("/v1/projects/{pid}/captions")
    def captions(pid: str, preset: str = "legenda_tradicional", style: str = "classico",
                 content_end_us: int | None = Query(None, gt=0)) -> dict[str, Any]:
        pf = project(pid)
        t = transcript_or_404(pf)
        blocks, fitted, layers = blocks_for(pf, t, preset, style, content_end_us)
        from ..render.typography import needs_layout,layout_dict
        by_id={w.id:w for w in t.words}
        seq=pf.sequence()
        layouts={}
        for b in blocks:
            sp=t.speaker(b.speaker_id)
            spec=layers.resolve(b.speaker_id,sp.color if sp else None)
            if needs_layout(b,spec,layers):
                layouts[b.id]=layout_dict(b,by_id,spec,layers,seq.width,seq.height)
        return {"rev": t.rev, "preset": fitted.model_dump(), "blocks": [b.model_dump(mode="json") for b in blocks], "layouts":layouts}

    @app.get("/v1/projects/{pid}/review")
    def review(pid: str) -> dict[str, Any]:
        pf = project(pid)
        t = transcript_or_404(pf)
        meta = pf.meta()
        items = open_disputes(meta.get("disputes", []), t, set(meta.get("dismissed_disputes", [])))
        return {"rev": t.rev, "items": items}

    @app.post("/v1/projects/{pid}/review/{dispute_id}/dismiss")
    def dismiss(pid: str, dispute_id: str) -> dict[str, Any]:
        """A pessoa decide manter o texto do principal (ou o silêncio) e tira o item da fila."""
        pf = project(pid)
        with edit_lock(pid):
            meta = pf.meta()
            known = {d.get("id") or f"d{i}" for i, d in enumerate(meta.get("disputes", []))}
            if dispute_id not in known:
                raise HTTPException(404, "divergência não encontrada")
            dismissed = sorted(set(meta.get("dismissed_disputes", [])) | {dispute_id})
            pf.set_meta("dismissed_disputes", dismissed)
        bus.publish({"type": "review", "project_id": pid})
        return {"dismissed": dispute_id}

    @app.post("/v1/projects/{pid}/diarize", status_code=202)
    def rediarize(pid: str, body: DiarizeRequest):
        from ..cancel import Cancelled
        from ..engines.base import SpeakerTurn
        from ..pipeline.audio_prep import prepare_audio
        from ..pipeline.rediarize import reassign_speakers
        pf = project(pid)
        transcript_or_404(pf)
        with edit_lock(pid):
            if jobs.active(pid, "diarize") or jobs.active(pid, "transcribe"):
                raise HTTPException(409, "já há uma análise de vozes em andamento")
            meta = pf.meta()
            source = body.context_audio_path or meta.get("work_audio")
            if not source or not Path(source).is_file():
                raise HTTPException(422, "áudio para conferir as vozes não está disponível")
            profile = choose_profile(hardware or detect_hardware(), allow_noncommercial=allow_noncommercial)

            def run(job, progress):
                progress("speakers", .05)
                audio = prepare_audio(Path(source), workspace.parent / "cache")
                diarizer = (engines_factory.get_diarizer(profile) if hasattr(engines_factory, "get_diarizer")
                            else engines_factory(profile).diarizer)
                if diarizer is None:
                    raise ValueError("o modelo de identificação de vozes não está disponível")
                turns = diarizer.diarize(audio, num_speakers=body.num_speakers)
                if job._cancel.is_set():
                    raise Cancelled("speakers")
                if body.context_audio_path:
                    shift = (meta.get("range") or {}).get("start_us", 0) - body.context_start_us
                    turns = [SpeakerTurn(t.speaker, max(0, t.start_us-shift), t.end_us-shift)
                             for t in turns if t.end_us > shift]
                if not turns:
                    raise ValueError("nenhuma voz identificada; a atribuição anterior foi preservada")
                with edit_lock(pid):
                    current = transcript_or_404(pf)
                    updated = reassign_speakers(current, turns)
                    if job._cancel.is_set():
                        raise Cancelled("speakers")
                    pf.save_revision(updated)
                    pf.set_meta("speaker_count_requested", body.num_speakers)
                used = len({w.speaker_id for w in updated.words if w.speaker_id})
                warnings = [] if used > 1 or body.num_speakers == 1 else ["Só uma voz atribuída neste trecho. Confira a separação; aparecerem duas pessoas na imagem não garante duas vozes no trecho."]
                pf.set_meta("warnings", warnings)
                bus.publish({"type": "revision", "project_id": pid, "rev": updated.rev})
                return {"rev": updated.rev, "speakers_assigned": used, "warnings": warnings}

            return {"job_id": jobs.submit("diarize", pid, run).id}

    @app.post("/v1/projects/{pid}/exports")
    def export(pid: str, body: ExportRequest) -> dict[str, Any]:
        pf = project(pid)
        t = transcript_or_404(pf)
        seq = pf.sequence()
        rate = FrameRate(seq.fps_num, seq.fps_den)
        blocks, _fitted, layers = blocks_for(pf, t, body.preset, body.style, body.content_end_us)
        out_dir = workspace / "exports" / pid
        out_dir.mkdir(parents=True, exist_ok=True)
        # O nome leva tudo o que muda o conteúdo: revisão, preset, estilo (que ajusta a largura
        # da linha) e nomes de falante. Exportações diferentes nunca se sobrescrevem.
        names = "_nomes" if body.speaker_names and body.format in ("srt", "vtt") else ""
        stem = f"{pid}_r{t.rev:03d}_{body.preset}_{body.style}{names}"
        if body.format == "srt":
            content, ext = to_srt(blocks, t, rate, speaker_names=body.speaker_names), "srt"
        elif body.format == "vtt":
            content, ext = to_vtt(blocks, t, rate, speaker_names=body.speaker_names), "vtt"
        elif body.format == "json":
            content, ext = to_json(t, blocks), "json"
        else:
            content, ext = compile_ass(blocks, t, layers, width=seq.width, height=seq.height), "ass"
        path = out_dir / f"{stem}.{ext}"
        path.write_text(content, encoding="utf-8")
        return {"rev": t.rev, "path": str(path), "content": content}

    # --- fluxo integrado ao Premiere -------------------------------------------------
    @app.get("/v1/projects/{pid}")
    def get_project(pid: str) -> dict[str, Any]:
        pf = project(pid)
        meta = pf.meta()
        rev = pf.latest_rev()
        applied = meta.get("applied")
        return {
            **{k: v for k, v in meta.items() if k not in ("disputes",)},
            "rev": rev,
            "dirty_since_apply": bool(applied) and (applied["rev"] != rev or applied["style_key"] != style_key(pf)),
        }

    def style_key(pf: ProjectFile) -> str:
        import hashlib
        import json as _json

        return hashlib.sha1(_json.dumps(pf.meta().get("style") or {}, sort_keys=True).encode()).hexdigest()[:10]

    @app.get("/v1/projects/{pid}/style")
    def get_style(pid: str) -> dict[str, Any]:
        pf = project(pid)
        layers, reading = project_style(pf)
        stored = ProjectStyle.model_validate(pf.meta().get("style") or {})
        available={f.casefold() for f in list_fonts()}
        requested={layers.base.font_family}|{p.font_family for p in layers.words.values() if p.font_family}
        warnings=[f"Fonte '{f}' não encontrada na biblioteca local; a composição usa uma fonte substituta."
                  for f in sorted(requested) if available and f.casefold() not in available]
        return {**stored.model_dump(), "resolved": layers.base.model_dump(), "reading_resolved": reading, "key": style_key(pf),"font_warnings":warnings}

    @app.put("/v1/projects/{pid}/style")
    def put_style(pid: str, body: ProjectStyle) -> dict[str, Any]:
        pf = project(pid)
        if body.preset not in STYLE_PRESETS:
            raise HTTPException(422, f"estilo desconhecido: {body.preset}")
        if body.reading is not None:
            try:
                get_preset(body.reading)
            except KeyError as exc:
                raise HTTPException(422, str(exc)) from exc
        try:
            StyleSpec.model_validate({**STYLE_PRESETS[body.preset].base.model_dump(), **body.overrides})
        except ValueError as exc:
            raise HTTPException(422, f"estilo inválido: {exc}") from exc
        latest=pf.load()
        allowed={w.id for w in latest.words} if latest else set()
        allowed.update((pf.meta().get("style") or {}).get("word_overrides",{}))
        unknown=set(body.word_overrides)-allowed
        if unknown:
            raise HTTPException(422,"estilo de palavra referencia IDs que não pertencem a esta transcrição")
        pf.set_meta("style", body.model_dump())
        bus.publish({"type": "style", "project_id": pid})
        return get_style(pid)

    @app.put("/v1/projects/{pid}/applied")
    def put_applied(pid: str, body: AppliedState) -> dict[str, Any]:
        pf = project(pid)
        pf.set_meta("applied", body.model_dump())
        return {"ok": True}

    # ---- espaço em disco: clipes que saíram da timeline vão para descartados/ (7 dias) ----
    @app.post("/v1/projects/{pid}/discard")
    def discard_clips(pid: str, body: PathsBody) -> dict[str, Any]:
        project(pid)
        current = limpeza.in_use(projects.values())
        keep = [p for p in body.paths if str(Path(p).resolve()).lower() not in current]
        return {"discarded": limpeza.discard(workspace, keep)}

    @app.get("/v1/storage")
    def storage() -> dict[str, Any]:
        return limpeza.report(workspace, projects.values())

    @app.post("/v1/storage/clean")
    def storage_clean(body: PathsBody) -> dict[str, Any]:
        return limpeza.free_space(workspace, projects.values(), body.paths)

    @app.delete("/v1/projects/{pid}/applied")
    def delete_applied(pid: str) -> dict[str, Any]:
        project(pid).set_meta("applied", None)
        return {"ok": True}

    @app.get("/v1/projects/{pid}/audio")
    def audio_slice(pid: str, start_us: int = Query(0, ge=0), end_us: int = Query(..., gt=0), pad_us: int = Query(300_000, ge=0, le=3_000_000)) -> Response:
        """Trecho do áudio transcrito, em WAV, para ouvir uma palavra ou divergência na revisão."""
        pf = project(pid)
        work = pf.meta().get("work_audio")
        if not work or not Path(work).exists():
            raise HTTPException(404, "áudio desta transcrição não está disponível")
        with wave.open(work, "rb") as w:
            sr, n = w.getframerate(), w.getnframes()
            a = max(0, (start_us - pad_us) * sr // 1_000_000)
            b = min(n, (end_us + pad_us) * sr // 1_000_000)
            if b <= a:
                raise HTTPException(422, "trecho vazio")
            w.setpos(a)
            frames = w.readframes(b - a)
            params = w.getparams()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as out:
            out.setparams(params)
            out.writeframes(frames)
        return Response(buf.getvalue(), media_type="audio/wav")

    @app.get("/v1/projects/{pid}/preview.png")
    def preview_png(pid: str, t_us: int = Query(..., ge=0), width: int = Query(640, ge=160, le=1920), background: str | None = None) -> Response:
        """Quadro de prévia renderizado pela libass (a mesma da legenda animada), com o estilo do projeto."""
        import subprocess
        import tempfile

        from ..render.ffmpeg import find_ffmpeg, no_window
        from ..render.preview import preview_cmd, visible_time_us

        pf = project(pid)
        t = transcript_or_404(pf)
        blocks, _fitted, layers = blocks_for(pf, t, "auto", "projeto")
        seq = pf.sequence()
        ass_doc = compile_ass(blocks, t, layers, width=seq.width, height=seq.height)
        # No início exato de um bloco a entrada (fade/pop) ainda está em 0%: a prévia sairia sem texto.
        t_us = visible_time_us(blocks, t_us, layers.base, FrameRate(seq.fps_num, seq.fps_den))
        exe = find_ffmpeg()
        with tempfile.TemporaryDirectory() as tmp:
            ass_path = Path(tmp) / "p.ass"
            ass_path.write_text(ass_doc, encoding="utf-8")
            png = Path(tmp) / "p.png"
            cmd = preview_cmd(ass_path, png, t_us=t_us, frame_width=seq.width,
                              frame_height=seq.height, output_width=width, ffmpeg=exe,
                              background=background,
                              offset=(layers.base.offset_x_pct, layers.base.offset_y_pct))
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **no_window())
            if proc.returncode != 0 or not png.exists():
                raise HTTPException(500, f"prévia falhou: {proc.stderr[-300:]}")
            return Response(png.read_bytes(), media_type="image/png", headers={"X-DC-Preview-Time-Us": str(t_us)})

    @app.get("/v1/projects/{pid}/preview.mp4")
    def preview_movie(pid: str, t_us: int = Query(0, ge=0), background: str | None = None):
        """Movimento real da legenda; fundo fixo, sem gerar o vídeo inteiro da edição."""
        import hashlib
        import os
        import subprocess
        from ..render.ffmpeg import escape_filter_path, no_window, translate_filter

        pf = project(pid)
        t = transcript_or_404(pf)
        blocks, _fitted, layers = blocks_for(pf, t, "auto", "projeto")
        if not blocks:
            raise HTTPException(422, "nenhum falante incluído nas legendas")
        block = next((b for b in blocks if b.start_us <= t_us < b.end_us), blocks[0])
        seq = pf.sequence()
        width = 360 if seq.height > seq.width else 640
        height = max(2, round(width * seq.height / seq.width / 2) * 2)
        if height > 1920:
            width = max(2, round(width * 1920 / height / 2) * 2)
            height = 1920
        duration = min(7., max(.1, (block.end_us - block.start_us) / 1e6))
        ass_doc = compile_ass([block], t, layers, width=width, height=height, offset_us=block.start_us)
        bg = Path(background) if background else None
        fingerprint = "neutral"
        if bg and bg.is_file():
            stat = bg.stat()
            fingerprint = f"{bg.resolve()}:{stat.st_mtime_ns}:{stat.st_size}"
        offset = (layers.base.offset_x_pct, layers.base.offset_y_pct)
        key = hashlib.sha256((ass_doc + fingerprint + str(duration) + __version__ + repr(offset)).encode()).hexdigest()[:24]
        folder = workspace / "previews" / pid
        folder.mkdir(parents=True, exist_ok=True)
        movie = folder / f"{key}.mp4"
        with edit_lock("preview:" + pid):
            if not movie.is_file() or movie.stat().st_size == 0:
                ass = folder / f"{key}.ass"
                ass.write_text(ass_doc, encoding="utf-8")
                partial = folder / f"{key}.parcial.mp4"
                if bg and bg.is_file():
                    source = ["-loop", "1", "-framerate", "30", "-i", str(bg)]
                    pre = f"scale={width}:{height},"
                else:
                    source = ["-f", "lavfi", "-i", f"color=c=0x2f363d:s={width}x{height}:r=30:d={duration:.3f}"]
                    pre = ""
                move = translate_filter(width, height, *offset)
                if move:
                    layer = f"color=c=black@0.0:s={width}x{height}:r=30:d={duration:.3f},format=rgba"
                    video = ["-f", "lavfi", "-i", layer, "-filter_complex",
                             f"[0:v]{pre}format=rgba[bg];[1:v]ass={escape_filter_path(ass)}:alpha=1,{move}[fg];"
                             f"[bg][fg]overlay=0:0:shortest=1"]
                else:
                    video = ["-vf", f"{pre}ass={escape_filter_path(ass)}"]
                command = [find_ffmpeg(), "-v", "error", "-y", "-nostdin", *source,
                           *video, "-t", f"{duration:.3f}",
                           "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                           "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(partial)]
                try:
                    result = subprocess.run(command, capture_output=True, timeout=30, **no_window())
                    if result.returncode != 0 or not partial.is_file():
                        raise HTTPException(500, "prévia de movimento falhou: " + result.stderr.decode("utf-8", "replace")[-300:])
                    os.replace(partial, movie)
                finally:
                    partial.unlink(missing_ok=True)
        return FileResponse(movie, media_type="video/mp4")

    @app.post("/v1/projects/{pid}/audio-target")
    def audio_target(pid: str) -> dict[str, Any]:
        """Caminho onde o painel pede ao Premiere para gravar o áudio mixado da sequência."""
        project(pid)
        folder = workspace.parent / "audio_premiere"
        folder.mkdir(parents=True, exist_ok=True)
        return {"path": str(folder / f"{pid}.wav"), "presets": find_audio_presets()}

    @app.get("/v1/projects/{pid}/frame.jpg")
    def frame_jpg(pid: str, w: int = Query(960, ge=120, le=1920)) -> Response:
        """Quadro real da sequência exportado pelo Premiere (fundo da prévia e das amostras), reduzido."""
        import subprocess

        from ..render.ffmpeg import find_ffmpeg, no_window

        project(pid)
        src = workspace.parent / "audio_premiere" / f"{pid}_quadro.png"
        if not src.is_file():
            raise HTTPException(404, "quadro ainda não exportado")
        cache = workspace.parent / "cache" / f"quadro_{pid}_{w}_{src.stat().st_mtime_ns}.jpg"
        if not cache.is_file():
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix(".parcial.jpg")
            proc = subprocess.run([find_ffmpeg(), "-v", "error", "-y", "-nostdin", "-i", str(src),
                                   "-vf", f"scale={w}:-2", "-q:v", "3", str(tmp)], capture_output=True, **no_window())
            if proc.returncode != 0 or not tmp.is_file():
                raise HTTPException(500, "não foi possível reduzir o quadro")
            os.replace(tmp, cache)
        # A URL muda a cada quadro novo (parâmetro v): o navegador pode guardar. Com no-store o painel
        # baixava a imagem de novo a cada redesenho e o quadro piscava.
        return Response(cache.read_bytes(), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400, immutable"})

    @app.get("/v1/system/fonts")
    def fonts() -> dict[str, Any]:
        return {"fonts": list(list_fonts())}

    @app.post("/v1/projects/{pid}/render", status_code=202)
    def render(pid: str, body: RenderBody | None = None) -> dict[str, Any]:
        """Renderiza (incrementalmente) a legenda animada em blocos ProRes 4444 com alfa."""
        pf = project(pid)
        t = transcript_or_404(pf)
        blocks, _fitted, layers = blocks_for(pf, t, "auto", "projeto", body.content_end_us if body else None)
        seq = pf.sequence()
        offset = (pf.meta().get("range") or {}).get("start_us", 0)
        out_dir = workspace / "renders" / pid
        key = style_key(pf)

        def run(job, progress):
            import time as _time
            t0 = _time.perf_counter()
            clips = render_animated(blocks, t, layers, seq, out_dir, progress=progress, should_cancel=job._cancel.is_set,
                                    restore=lambda folder, name: limpeza.restore(folder, name, workspace))
            seconds = round(_time.perf_counter() - t0, 3)
            try:
                from ..render.ffmpeg import render_codec
                with open(workspace.parent / "tempos.jsonl", "a", encoding="utf-8") as log:
                    log.write(json.dumps({"kind": "render", "project": pid, "seconds": seconds, "codec": render_codec(),
                                          "frame": f"{seq.width}x{seq.height}", "clips": len(clips),
                                          "reused": sum(c.reused for c in clips),
                                          "media_s": round(sum(c.end_us - c.start_us for c in clips if not c.reused) / 1e6, 2)}) + "\n")
            except OSError:
                pass
            return {"rev": t.rev, "style_key": key, "clips": with_labels(clips_as_dicts(clips, offset), pf.meta().get("name") or "Legenda", seq),
                    "reused": sum(c.reused for c in clips), "seconds": seconds}

        return {"job_id": jobs.submit("render", pid, run).id}

    @app.post("/v1/jobs", status_code=202)
    def create_job(body: JobRequest) -> dict[str, Any]:
        pf = project(body.project_id)
        with edit_lock(pf.id):  # dois pedidos seguidos não podem passar os dois pela checagem
            return _create_job(pf, body)

    def _create_job(pf: ProjectFile, body: JobRequest) -> dict[str, Any]:
        if pf.latest_rev() is not None:
            raise HTTPException(409, "projeto já transcrito; use reprocessamento de trecho")
        if jobs.active(pf.id, "transcribe"):
            raise HTTPException(409, "este projeto já tem uma transcrição na fila ou em andamento")
        audio = Path(body.audio_path)
        if not audio.exists():
            raise HTTPException(422, f"áudio não encontrado: {audio}")
        hw = hardware or detect_hardware()
        profile = choose_profile(hw, allow_noncommercial=allow_noncommercial)

        def run(job, progress):
            import time as _time
            marks: list[tuple[str, float]] = [("modelos", _time.perf_counter())]
            was_ready = warm["models"] == "ready"
            engines = engines_factory(profile)

            def timed(stage: str, value: float) -> None:
                if stage != marks[-1][0]:
                    marks.append((stage, _time.perf_counter()))
                progress(stage, value)

            opts = TranscribeOptions(**body.options.model_dump())
            result = transcribe(audio, engines, opts, progress=timed, should_cancel=job._cancel.is_set)
            marks.append(("fim", _time.perf_counter()))
            timings = {a[0]: round(b[1] - a[1], 3) for a, b in zip(marks, marks[1:])}
            try:  # registro local para medir o tempo real no PC do editor
                with open(workspace.parent / "tempos.jsonl", "a", encoding="utf-8") as log:
                    log.write(json.dumps({"kind": "transcribe", "project": pf.id, "models_ready": was_ready,
                                          "audio_s": round(sum(r.duration_us for r in result.regions) / 1e6, 2),
                                          "timings": timings, "engines": result.engines_used}) + "\n")
            except OSError:
                pass
            pf.save_revision(result.transcript)
            pf.set_meta("warnings", result.warnings)
            pf.set_meta("speaker_count_requested", body.options.num_speakers)
            if result.audio is not None:
                pf.set_meta("work_audio", str(result.audio))
            disputes = [asdict(d) for d in result.disputes]
            pf.set_meta("disputes", disputes)
            bus.publish({"type": "revision", "project_id": pf.id, "rev": result.transcript.rev})
            return {
                "rev": result.transcript.rev,
                "disputes": len(disputes),
                "engines": result.engines_used,
                "profile": profile.id,
                "warnings": result.warnings,
                "timings": timings,
            }

        job = jobs.submit("transcribe", pf.id, run)
        return {"job_id": job.id}

    @app.get("/v1/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "trabalho não encontrado")
        return job.public()

    @app.delete("/v1/jobs/{job_id}")
    def cancel_job(job_id: str) -> dict[str, Any]:
        return {"cancelled": jobs.cancel(job_id)}

    @app.websocket("/v1/events")
    async def events(ws: WebSocket, token_q: str = Query("", alias="token")):
        host = (ws.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
        if host not in ALLOWED_HOSTS or not secrets.compare_digest(token_q.encode("utf-8"), token.encode("utf-8")):
            await ws.close(code=1008)
            return
        await ws.accept()
        q = bus.subscribe()
        try:
            await ws.send_json({"type": "hello", "version": __version__})
            while True:
                await ws.send_json(await q.get())
        except WebSocketDisconnect:
            pass
        finally:
            bus.unsubscribe(q)

    web = Path(__file__).resolve().parent.parent / "web"
    if web.is_dir():
        app.mount("/ui", StaticFiles(directory=web, html=True), name="ui")

        @app.get("/")
        def root() -> RedirectResponse:
            return RedirectResponse("/ui/")

    return app
