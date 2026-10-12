# Avatar Scene Indexer

A personal tool for indexing *Avatar: The Last Airbender* episodes and running semantic scene search for editing and scriptwriting.

Full plan: `C:\Users\brath\.claude\plans\twinkly-tinkering-treehouse.md`

## Setup

```
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
copy .env.example .env   # then add your ANTHROPIC_API_KEY

cd ../frontend
npm install
```

## Phase 1 — test the pipeline on a single episode (no UI)

```
cd backend
.venv/Scripts/python scripts/ingest_episode.py "D:\avatar\s01e01.mkv" --book 1 --episode 1 --title "The Boy in the Iceberg"
```

Check the results directly in SQLite (`backend/data/avatar.db`), in the `transcript`, `shots`, and `scenes` tables.

## Running (Phase 3+)

Backend API:
```
cd backend
.venv/Scripts/uvicorn app.main:app --reload
```

Indexing worker (processes sources added through the UI):
```
cd backend
.venv/Scripts/python -m app.pipeline.worker
```

Frontend:
```
cd frontend
npm run dev
```

Open http://localhost:5173
