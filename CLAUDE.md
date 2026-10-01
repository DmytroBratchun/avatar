# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Personal, local-only tool that indexes TV episodes (Avatar: The Last Airbender) into searchable scenes, so footage can be found by meaning for video editing / scriptwriting. FastAPI + SQLite backend in `backend/`, React + Vite frontend in `frontend/`. Windows machine with a CUDA GPU; `ffmpeg` and (for the default caption provider) the `claude` CLI must be on PATH.

`TODO.md` holds the backlog and known data-quality issues; `ideas.md` (Russian) describes the larger planned workflow this feeds into (voice-over, automatic video assembly). `README.md` is partly outdated - prefer the commands below.

## Commands

Run from the repo root in Git Bash:

```
./start.sh    # backend on :8000 (with embedded free worker) + Vite dev server on :5173
./stop.sh     # stops both by matching process command lines (see stop.ps1)
```

Logs: `backend/data/uvicorn.log`, `backend/data/local_worker.log`, `backend/data/worker.log`, `.run/frontend.log`. `start.sh` runs uvicorn without `--reload`, so backend changes need `./stop.sh && ./start.sh`. For auto-reload, run it by hand from `backend/`:

```
.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000 --reload
```

Paid captioning worker (never started by `start.sh`; can also be started/stopped from the Pipeline page via `/api/worker/start|stop`):

```
cd backend && .venv/Scripts/python.exe -m app.pipeline.worker
```

**Do not start this worker unless the user asks** - it makes LLM calls for every un-captioned scene of every pending source and spends real money / subscription quota.

Frontend (from `frontend/`):

```
npm run dev
npm run build    # tsc -b && vite build - this is the type check
npm run lint     # oxlint
```

One-off scripts (from `backend/`): `scripts/ingest_episode.py <video> --book N --episode N --title "..."` runs the full pipeline on one file without the UI; `scripts/normalize_tags.py` merges casing/plural tag variants.

There are no tests, and no Python linter/formatter is configured.

## Architecture

### Pipeline and the two workers

Stages live in `backend/app/pipeline/stages/`, each a `run_*(db, source)` function that is idempotent (skips if its output already exists). `pipeline/runner.py` composes them:

- `LOCAL_STAGES` (free, GPU only): transcribe (faster-whisper) -> detect shots (PySceneDetect) -> group shots into scenes -> extract keyframes (ffmpeg) -> build transcript chunks.
- `STAGES` = `LOCAL_STAGES` + caption (LLM) + embed.

Two separate workers poll the DB every 5s, split strictly by whether they cost money:

- **Local worker** (`pipeline/local_worker.py`) runs as a daemon thread inside the FastAPI process (started in `app/main.py`). It runs `LOCAL_STAGES` on sources with status `paused`, leaving them at `ready_for_captioning`, and drains the free reindex queues (text, visual, transcript-chunk embeddings).
- **Paid worker** (`pipeline/worker.py`) is a separate process. It runs the full `STAGES` on any source whose status is not in `TERMINAL_STATUSES` (`indexed`, `failed`, `paused`, `ready_for_captioning`), and drains the episode-summary queue.

Anything that needs an LLM call belongs on the paid worker; anything free belongs on the local worker.

The "queues" are not a separate table: they are the `Source.status` string plus boolean `*_requested` flags on `Source`. API endpoints in `api/sources.py` only flip these; workers do the work and clear them. Long reindex loops re-read their flag on every scene so a Cancel from the UI takes effect mid-run.

Note that `POST /api/sources` and `/scan-folder` create sources as `pending`, which only the paid worker picks up, and nothing in the code currently sets `paused` - so newly added sources wait for the paid worker to run all stages.

Each worker writes a heartbeat JSON to `backend/data/` every 10s; `api/worker.py` reports a worker as stopped when the heartbeat is older than 30s.

### Captioning providers

`stages/caption.py` has three providers, chosen at call time: `claude_code` (default - shells out to headless `claude -p` with a JSON schema, billed to the Claude subscription), `api` (Anthropic SDK with a forced tool call, metered), and `local` (Ollama; uses a deliberately narrower prompt/schema because small VLMs invent character names and plot). `stages/episode_summary.py` reuses the same provider switch.

`Scene.model_version` is written as `provider:model:caption-<hash>`, where the hash is derived from the prompt + schema - editing either changes it automatically, which is how stale-prompt scenes are found.

### Settings

`app/config.py` reads `backend/.env` (static: models, Whisper device, scene-grouping thresholds). `services/runtime_settings.py` layers `backend/data/settings.json` on top for caption provider / model / API key, editable from the UI's settings modal with no restart. Code that needs those three values must go through `current_caption_*()` / `current_anthropic_api_key()`, not `settings.*` directly.

### Storage and search

Everything is one SQLite file, `backend/data/avatar.db` (git-ignored, along with keyframes and audio under `backend/data/`). ORM models are in `db/models.py`; vectors live in `sqlite-vec` virtual tables created by raw SQL in `db/session.py` and accessed through `vectorstore/sqlite_vec_client.py`:

- `scene_text_vec` FLOAT[1024] - `Scene.context_description` embedded with Qwen3-Embedding-0.6B
- `scene_visual_vec` FLOAT[1152] - first keyframe embedded with SigLIP2
- `transcript_chunk_vec` FLOAT[1024] - `episode_summary + chunk text`

The dimensions are hard-coded to those models; changing `text_embedding_model` / `visual_embedding_model` means recreating the vec tables and re-embedding. Some comments and docstrings still say "BGE" / "CLIP" from earlier models.

`/api/search` (`api/search.py`) is hybrid: the query is embedded with the text model and with SigLIP's text tower (text-to-image), each KNN is run over its table, and results are merged with reciprocal rank fusion. Tag/book/episode filters are resolved to candidate scene ids first and passed into the KNN. `/api/transcript-chunks` is a separate, coarser RAG-style retrieval over ~60s sliding transcript windows, independent of scene boundaries.

`Scene` and `TranscriptChunk` denormalize `show_title` / `book` / `episode_number` from `Source` for cheap filtering; `PATCH /api/sources/{id}` keeps scenes in sync.

### Schema changes

There are no migrations (alembic is in requirements but unused). `init_db()` only calls `create_all`, which creates missing tables but never alters existing ones - adding a column to a model requires a manual `ALTER TABLE` on the existing DB.

### Frontend

`src/api/client.ts` is the single API layer: hand-written TypeScript types mirroring the Pydantic schemas in `backend/app/schemas/scenes.py`, with the backend URL hard-coded to `http://localhost:8000` (no Vite proxy). Changing a response schema means updating both. Pages use TanStack Query; routes are declared in `src/App.tsx`.
