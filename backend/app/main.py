import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin, editor, logs, media, universes as universes_api, scenes, search, settings as settings_api, sources, transcript, transcript_chunks, worker as worker_api
from app.db.session import init_db
from app.pipeline.local_worker import run_loop as run_local_worker_loop
from app.services.runtime_settings import get_effective_settings

app = FastAPI(title="Avatar Scene Indexer")

app.add_middleware(
    CORSMiddleware,
    # personal local tool - Vite auto-increments the port (5173, 5174, ...) whenever
    # another instance is already running, so match any localhost/127.0.0.1 port
    # rather than whack-a-mole a fixed origin list
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1):\d+$",
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sources.router)
app.include_router(scenes.router)
app.include_router(search.router)
app.include_router(media.router)
app.include_router(settings_api.router)
app.include_router(worker_api.router)
app.include_router(transcript.router)
app.include_router(transcript_chunks.router)
app.include_router(admin.router)
app.include_router(editor.router)
app.include_router(logs.router)
app.include_router(universes_api.router)


_local_worker_stop = threading.Event()


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    thread = threading.Thread(target=run_local_worker_loop, args=(_local_worker_stop,), daemon=True)
    thread.start()


@app.on_event("shutdown")
def on_shutdown() -> None:
    _local_worker_stop.set()


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/config")
def get_config() -> dict:
    effective = get_effective_settings()
    return {"caption_provider": effective["caption_provider"], "caption_model": effective["caption_model"]}
