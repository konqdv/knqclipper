"""FastAPI application factory and lifecycle."""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

mimetypes.add_type("application/manifest+json", ".webmanifest")  # PWA manifest served with the right type

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from clipper import __version__
from clipper.api import routes_clips, routes_learning, routes_publish, routes_streams, routes_system
from clipper.captions.styles import FONTS_DIR
from clipper.clips.service import render_job
from clipper.publish.service import publish_job
from clipper.core.appsettings import store
from clipper.core.config import Settings, get_settings
from clipper.core.credentials import CredentialStore, set_credentials
from clipper.core.events import bus
from clipper.core.jobs import LocalJobQueue, RedisJobQueue, register_handler, set_queue
from clipper.core.logging import setup_logging
from clipper.core.secrets import SecretBox
from clipper.db.session import close_db, init_db
from clipper.learning.service import learner
from clipper.llm.providers import make_provider
from clipper.storage.manager import StorageManager, set_storage
from clipper.stream.manager import StreamManager, set_manager

log = logging.getLogger(__name__)
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
UI_MISSING_HTML = """<!doctype html><html lang="es"><meta charset="utf-8"><title>Live Clipper</title>
<body style="background:#0b0d12;color:#e8eaf0;font-family:system-ui,sans-serif;max-width:640px;margin:10vh auto;padding:0 16px;line-height:1.5">
<h1 style="font-size:22px">El servidor funciona, pero falta la interfaz</h1>
<p>No se encontró <code>frontend/dist</code>. Compílala una vez y recarga esta página:</p>
<pre style="background:#141821;padding:12px;border-radius:10px">cd frontend
npm install
npm run build</pre>
<p>En Windows, el instalador <code>windows/Instalar-LiveClipper.bat</code> lo hace automáticamente.
La API sigue disponible en <a style="color:#a78bfa" href="/docs">/docs</a>.</p></body></html>"""


async def bootstrap(settings: Settings, *, run_workers: bool = True, resume: bool = True) -> dict:
    """Initialise every subsystem (used by the API and by standalone workers)."""
    setup_logging(settings.log_level, settings.data_dir)
    await init_db(settings.resolved_database_url)
    storage = StorageManager(settings.data_dir, settings.disk_min_free_gb, settings.source_retention_days)
    set_storage(storage)
    await store.load()
    creds = CredentialStore(SecretBox.from_settings(settings.secret_key, settings.data_dir))
    set_credentials(creds)
    await creds.apply(settings)
    rl = store.get("runtime_llm")
    if rl.get("provider"):
        settings.llm_provider = rl["provider"]
        settings.llm_model = rl.get("model") or settings.llm_model
        settings.llm_base_url = rl.get("base_url")
        settings.llm_vision = bool(rl.get("vision", settings.llm_vision))
    await learner.load()
    bus.bind_loop(asyncio.get_running_loop())
    queue = RedisJobQueue(settings.redis_url) if settings.queue_backend == "redis" else LocalJobQueue()
    if settings.queue_backend == "redis":
        await bus.attach_redis(settings.redis_url)
    set_queue(queue)
    register_handler("render", render_job)
    register_handler("training_analyze", routes_learning.training_analyze_job)
    register_handler("publish", publish_job)
    manager = StreamManager(settings)
    manager.llm = make_provider(settings)
    set_manager(manager)
    tasks = []
    if run_workers:
        await queue.start(["render", "training_analyze", "publish"], {"render": settings.render_concurrency, "training_analyze": 1, "publish": 2})
        n = await queue.recover()
        if n:
            log.info("recovered %d pending jobs", n)
    if resume:
        await manager.resume()
        tasks.append(asyncio.create_task(storage.run_periodic(), name="storage-cleanup"))
    return {"queue": queue, "manager": manager, "storage": storage, "tasks": tasks}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    ctx = await bootstrap(settings, run_workers=settings.run_local_render_workers or settings.queue_backend == "local")
    log.info("Universal AI Live Clipper %s ready on http://%s:%s", __version__, settings.host, settings.port)
    try:
        yield
    finally:
        await ctx["manager"].shutdown()
        await ctx["queue"].stop()
        for t in ctx["tasks"]:
            t.cancel()
        await bus.close()
        await close_db()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Universal AI Live Clipper", version=__version__, lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def auth(request: Request, call_next):
        token = get_settings().api_token
        path = request.url.path
        public = path.startswith("/api/webhooks/") or path.startswith("/api/oauth/") or not path.startswith(("/api", "/media"))
        if token and not public:
            sent = request.headers.get("authorization", "").removeprefix("Bearer ").strip() or request.query_params.get("token")
            if sent != token:
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)

    for r in (routes_streams.router, routes_clips.router, routes_learning.router, routes_publish.router, routes_system.router):
        app.include_router(r)
    app.mount("/media", StaticFiles(directory=settings.data_dir), name="media")
    app.mount("/fonts", StaticFiles(directory=FONTS_DIR), name="fonts")
    dist = FRONTEND_DIST.resolve()
    if (dist / "index.html").is_file():
        if (dist / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            f = (dist / path).resolve()
            # only serve files that really live inside frontend/dist (no "../" escapes)
            if path and f.is_file() and f.is_relative_to(dist):
                return FileResponse(f)
            return FileResponse(dist / "index.html")
    else:

        @app.get("/", include_in_schema=False)
        async def ui_missing():
            return HTMLResponse(UI_MISSING_HTML, status_code=503)

    return app
