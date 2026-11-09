"""Training Library, Feedback Learning and Social Performance endpoints."""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from clipper.core.credentials import get_credentials
from clipper.core.config import get_settings
from clipper.core.events import bus
from clipper.core.jobs import PermanentJobError, get_queue
from clipper.db.models import Clip, MetricSnapshot, ModelVersion, Publication, TrainingExample
from clipper.db.session import session_scope
from clipper.learning.service import learner
from clipper.social import youtube_analytics as yta
from clipper.social.insights import METRIC_FIELDS, PLATFORMS, build_insights, parse_csv
from clipper.storage.manager import get_storage
from clipper.stream.manager import get_manager

router = APIRouter(prefix="/api", tags=["learning"])

LABELS = ["excellent", "good", "normal", "bad", "unpublishable"]
TAGS = ["gracioso", "buen_gancho", "historia_interesante", "reaccion", "polemica", "revelacion", "buen_contexto",
        "aburrido", "mal_cortado", "demasiado_largo", "falta_contexto"]


# --- training library ------------------------------------------------------------------------
def _example_out(e: TrainingExample) -> dict[str, Any]:
    return {"id": e.id, "title": e.title, "creator": e.creator, "platform": e.platform, "label": e.label, "tags": e.tags,
            "status": e.status, "duration": e.duration, "transcript": e.transcript, "analysis": e.analysis, "error": e.error,
            "video_url": get_storage().to_media_url(e.file_path), "created_at": e.created_at}


@router.get("/training")
async def list_training() -> dict[str, Any]:
    async with session_scope() as s:
        rows = (await s.execute(select(TrainingExample).order_by(desc(TrainingExample.created_at)))).scalars().all()
        return {"labels": LABELS, "tags": TAGS, "items": [_example_out(e) for e in rows]}


@router.post("/training", status_code=201)
async def upload_training(file: UploadFile = File(...), label: str = Form("normal"), tags: str = Form(""),
                          title: str = Form(""), creator: str = Form(""), platform: str = Form("")) -> dict[str, Any]:
    if label not in LABELS:
        raise HTTPException(422, f"label must be one of {LABELS}")
    ext = Path(file.filename or "clip.mp4").suffix.lower() or ".mp4"
    if ext not in (".mp4", ".mov", ".webm", ".mkv", ".m4v"):
        raise HTTPException(422, "formato de vídeo no soportado")
    dest = get_storage().training_path(f"{uuid.uuid4().hex}{ext}")
    with dest.open("wb") as fh:
        while chunk := await file.read(1 << 20):
            fh.write(chunk)
    async with session_scope() as s:
        e = TrainingExample(title=title or Path(file.filename or "clip").stem, creator=creator or None, platform=platform or None,
                            file_path=str(dest), label=label, tags=[t for t in tags.split(",") if t], status="pending")
        s.add(e)
        await s.flush()
        eid = e.id
    await get_queue().enqueue("training_analyze", {"example_id": eid})
    async with session_scope() as s:
        return _example_out(await s.get(TrainingExample, eid))


class TrainingPatch(BaseModel):
    label: str | None = None
    tags: list[str] | None = None
    title: str | None = None
    creator: str | None = None


@router.patch("/training/{eid}")
async def patch_training(eid: int, body: TrainingPatch) -> dict[str, Any]:
    async with session_scope() as s:
        e = await s.get(TrainingExample, eid)
        if e is None:
            raise HTTPException(404)
        if body.label is not None:
            if body.label not in LABELS:
                raise HTTPException(422, "invalid label")
            e.label = body.label
        if body.tags is not None:
            e.tags = body.tags
        if body.title is not None:
            e.title = body.title
        if body.creator is not None:
            e.creator = body.creator
        return _example_out(e)


@router.delete("/training/{eid}", status_code=204)
async def delete_training(eid: int) -> None:
    async with session_scope() as s:
        e = await s.get(TrainingExample, eid)
        if e:
            Path(e.file_path).unlink(missing_ok=True)
            await s.delete(e)


async def training_analyze_job(payload: dict[str, Any]) -> None:
    from clipper.training.analyze import analyze_example

    eid = int(payload["example_id"])
    async with session_scope() as s:
        e = await s.get(TrainingExample, eid)
        if e is None:
            raise PermanentJobError("example deleted")
        e.status = "analyzing"
        path = e.file_path
    bus.publish("training.analyzing", example_id=eid)
    try:
        res = await asyncio.to_thread(analyze_example, path, get_manager().asr_engine(), get_settings().resolved_models_dir)
    except Exception as exc:
        async with session_scope() as s:
            e = await s.get(TrainingExample, eid)
            if e:
                e.status, e.error = "failed", str(exc)[-800:]
        raise PermanentJobError(str(exc)) from exc
    async with session_scope() as s:
        e = await s.get(TrainingExample, eid)
        if e:
            e.status, e.error = "ready", None
            e.duration, e.transcript, e.analysis, e.features = res["duration"], res["transcript"], res["analysis"], res["features"]
    bus.publish("training.ready", example_id=eid)


# --- learning ------------------------------------------------------------------------------------
class TrainIn(BaseModel):
    kind: str = "editorial"
    auto_promote: bool | None = None


@router.get("/learning/models")
async def models() -> dict[str, Any]:
    async with session_scope() as s:
        rows = (await s.execute(select(ModelVersion).order_by(desc(ModelVersion.created_at)).limit(50))).scalars().all()
        items = [{"id": r.id, "kind": r.kind, "version": r.version, "status": r.status, "n_samples": r.n_samples,
                  "metrics": r.metrics, "notes": r.notes, "created_at": r.created_at,
                  "top_features": (r.params or {}).get("top_features", [])[:8]} for r in rows]
    return {"items": items, "production": learner.version, "blend_alpha": learner.blend_alpha()}


@router.post("/learning/train")
async def train(body: TrainIn) -> dict[str, Any]:
    return await learner.train(body.kind, body.auto_promote)


@router.post("/learning/models/{mid}/promote")
async def promote(mid: int) -> dict[str, Any]:
    try:
        await learner.promote(mid)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True, "production": learner.version}


@router.get("/learning/patterns")
async def patterns() -> dict[str, Any]:
    return await learner.patterns()


# --- social performance ------------------------------------------------------------------------
class PublicationIn(BaseModel):
    clip_id: int
    platform: str
    url: str | None = None
    posted_at: float | None = None


class MetricsIn(BaseModel):
    views: int | None = None
    avg_retention: float | None = Field(default=None, ge=0, le=100)
    completion_rate: float | None = Field(default=None, ge=0, le=100)
    watch_time_s: float | None = None
    likes: int | None = None
    shares: int | None = None
    comments: int | None = None
    saves: int | None = None
    followers_gained: int | None = None


async def _pub_out(s, p: Publication) -> dict[str, Any]:
    snaps = (await s.execute(select(MetricSnapshot).where(MetricSnapshot.publication_id == p.id)
                             .order_by(desc(MetricSnapshot.captured_at)))).scalars().all()
    clip = await s.get(Clip, p.clip_id)
    latest = snaps[0] if snaps else None
    return {"id": p.id, "clip_id": p.clip_id, "clip_title": clip.title if clip else None, "platform": p.platform, "url": p.url,
            "posted_at": p.posted_at, "created_at": p.created_at, "status": getattr(p, "status", "registered"), "error": getattr(p, "error", None),
            "latest": {f: getattr(latest, f) for f in METRIC_FIELDS} | {"captured_at": latest.captured_at, "source": latest.source} if latest else None,
            "history": len(snaps)}


@router.get("/social/publications")
async def list_publications(clip_id: int | None = None) -> list[dict[str, Any]]:
    async with session_scope() as s:
        q = select(Publication).order_by(desc(Publication.created_at))
        if clip_id:
            q = q.where(Publication.clip_id == clip_id)
        return [await _pub_out(s, p) for p in (await s.execute(q)).scalars().all()]


@router.post("/social/publications", status_code=201)
async def add_publication(body: PublicationIn) -> dict[str, Any]:
    if body.platform not in PLATFORMS:
        raise HTTPException(422, f"platform must be one of {PLATFORMS}")
    async with session_scope() as s:
        if await s.get(Clip, body.clip_id) is None:
            raise HTTPException(404, "clip not found")
        p = Publication(clip_id=body.clip_id, platform=body.platform, url=body.url, posted_at=body.posted_at or time.time())
        s.add(p)
        await s.flush()
        return await _pub_out(s, p)


@router.delete("/social/publications/{pid}", status_code=204)
async def delete_publication(pid: int) -> None:
    async with session_scope() as s:
        p = await s.get(Publication, pid)
        if p:
            await s.delete(p)


@router.post("/social/publications/{pid}/metrics", status_code=201)
async def add_metrics(pid: int, body: MetricsIn) -> dict[str, Any]:
    async with session_scope() as s:
        p = await s.get(Publication, pid)
        if p is None:
            raise HTTPException(404)
        s.add(MetricSnapshot(publication_id=pid, source="manual", **body.model_dump()))
        await s.flush()
        return await _pub_out(s, p)


class CsvIn(BaseModel):
    csv: str


@router.post("/social/import")
async def import_csv(body: CsvIn) -> dict[str, Any]:
    try:
        rows = parse_csv(body.csv)
    except (ValueError, KeyError) as exc:
        raise HTTPException(422, f"CSV inválido: {exc}") from exc
    created = 0
    async with session_scope() as s:
        for r in rows:
            if r["platform"] not in PLATFORMS or await s.get(Clip, r["clip_id"]) is None:
                continue
            p = (await s.execute(select(Publication).where(Publication.clip_id == r["clip_id"], Publication.platform == r["platform"]))).scalars().first()
            if p is None:
                p = Publication(clip_id=r["clip_id"], platform=r["platform"], url=r.get("url"), posted_at=time.time())
                s.add(p)
                await s.flush()
            s.add(MetricSnapshot(publication_id=p.id, source="csv", **{f: r.get(f) for f in METRIC_FIELDS}))
            created += 1
    return {"imported": created, "rows": len(rows)}


@router.post("/social/publications/{pid}/sync")
async def sync_youtube(pid: int) -> dict[str, Any]:
    settings = get_settings()
    creds = get_credentials()
    refresh = await creds.get("google", "refresh_token")
    if not (settings.google_client_id and settings.google_client_secret and refresh):
        raise HTTPException(400, "Conecta tu cuenta de Google (OAuth) en Configuración para leer YouTube Analytics")
    async with session_scope() as s:
        p = await s.get(Publication, pid)
        if p is None:
            raise HTTPException(404)
        if p.platform != "shorts":
            raise HTTPException(400, "Solo YouTube Shorts tiene API oficial conectada; TikTok/Reels: importación manual o CSV")
        vid = yta.video_id_from_url(p.url)
    if not vid:
        raise HTTPException(400, "La publicación necesita la URL del Short")
    tok = await yta.refresh_access_token(settings.google_client_id, settings.google_client_secret, refresh)
    data = await yta.video_metrics(tok["access_token"], vid)
    async with session_scope() as s:
        s.add(MetricSnapshot(publication_id=pid, source="youtube_api", **data))
        p = await s.get(Publication, pid)
        return await _pub_out(s, p)


@router.get("/social/insights")
async def insights() -> dict[str, Any]:
    return await build_insights()


re  # noqa: B018
