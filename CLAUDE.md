# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Personal, local-only tool that indexes TV episodes (Avatar: The Last Airbender) into searchable scenes, so footage can be found by meaning for video editing / scriptwriting. FastAPI + SQLite backend in `backend/`, React + Vite frontend in `frontend/`. Windows machine with a CUDA GPU (PyTorch must be the CUDA build - `torch==2.14.0+cu130` from the cu130 index - or every embedding silently runs on the CPU, ~100x slower); `ffmpeg` and (for the default caption provider) the `claude` CLI must be on PATH.

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

- `LOCAL_STAGES` (free, GPU only): transcribe (faster-whisper; runs of 5+ identical lines - Whisper stuck on music/silence - are dropped, see `services/transcript_repeats.py`) -> detect shots (PySceneDetect) -> group shots into scenes -> extract keyframes (ffmpeg) -> build transcript chunks.
- `STAGES` = `LOCAL_STAGES` + caption (LLM) + embed.

Two separate workers poll the DB every 5s, split strictly by whether they cost money:

- **Local worker** (`pipeline/local_worker.py`) runs as a daemon thread inside the FastAPI process (started in `app/main.py`). It runs `LOCAL_STAGES` on sources with status `paused`, leaving them at `ready_for_captioning`, and drains the free reindex queues (text, visual, transcript-chunk embeddings).
- **Paid worker** (`pipeline/worker.py`) is a separate process. It runs the full `STAGES` on any source whose status is not in `TERMINAL_STATUSES` (`indexed`, `failed`, `paused`, `ready_for_captioning`), and drains the episode-summary queue.

Anything that needs an LLM call belongs on the paid worker; anything free belongs on the local worker.

The "queues" are not a separate table: they are the `Source.status` string plus boolean `*_requested` flags on `Source`. API endpoints in `api/sources.py` only flip these; workers do the work and clear them. Long reindex loops re-read their flag on every scene so a Cancel from the UI takes effect mid-run.

Note that `POST /api/sources` and `/scan-folder` create sources as `pending`, which only the paid worker picks up, and nothing in the code currently sets `paused` - so newly added sources wait for the paid worker to run all stages.

Each worker writes a heartbeat JSON to `backend/data/` every 10s; `api/worker.py` reports a worker as stopped when the heartbeat is older than 30s.

### Universes

A universe (`services/universes.py`) is a group of shows and films set in the same fictional world. Everything a prompt needs to know about a particular world is data on the universe - name, one-paragraph description, member shows, the word for a season, a closed list of regions, what each region's scenery looks like - so the prompts stay generic and a new world is one new entry, not rewritten prompts. Entries live in `backend/data/universes.json` (created from built-in defaults on first use); a show no universe claims gets a bare universe of its own, so nothing from another world leaks into its prompts. Don't hard-code show names, nations or world knowledge into prompts - read them from the universe.

### Fan-wiki transcripts

A universe can name a fan wiki (`transcripts_wiki`, a MediaWiki API URL) whose "Transcript" namespace holds an episode transcript per page: spoken lines with speaker names, and written descriptions of what happens on screen between them. They have no timings. `services/wiki_transcripts.py` downloads them (cached in `backend/data/wiki_transcripts/`), finds each episode's page by rare-word overlap with our Whisper transcript (no titles needed), and aligns the two word by word with difflib: a wiki line gets the time of the Whisper words it matched, a Whisper line gets the speaker its words belong to, and a description gets the gap between the lines around it. `scripts/import_wiki_transcripts.py` applies this: fills `TranscriptLine.speaker`, removes runs of 3+ Whisper lines that share no word with the page (invented over music; runs containing lines spoken elsewhere in the show, like the opening narration, are spared), and stores every wiki line and description with its matched time range in `wiki_entries`. Speakers are passed to the caption and quote prompts.

`services/wiki_descriptions.py` turns those entries into a text per scene (`Scene.wiki_description`): the descriptions whose time range overlaps the scene, plus the wiki's lines spoken in it. A scene with dialogue but no description gets no text and is not indexed - a bare line says nothing about what is on screen and crowds out described scenes. Descriptions that share one gap between two lines are spread over it in page order, in proportion to their length. `scripts/index_wiki_descriptions.py` embeds these into `scene_wiki_vec` (same text model as captions). This is a free text index covering ~70% of scenes of every episode with a wiki page, next to the paid caption index that covers only captioned episodes; both are searched by `/api/search` (text/hybrid) and by the editor's footage search, where a candidate carries `text_score`, `wiki_score` and `visual_score`. `scripts/compare_wiki_index.py` measures it against the other indexes.

`services/quote_bank.py` + `scripts/import_quote_bank.py` build the quote bank (`quote_bank` table): quotes from curated collections - the Wikiquote pages listed on the universe (`quotes_wiki`, `quote_pages`) or a plain text file - are located in the wiki transcript lines of the episode the collection files them under, which gives each a time range; exchanges longer than 45s are cut into several clips. `/api/quote-bank` filters by wording, speaker and shows; the editor's Quotes tab has a Quote bank card that adds a clip through the same endpoint as a hand-picked one. The editor's quotes stage also shows the bank (clips up to 40s) to its first Claude call: a quote picked from it by number skips the episode-reading step. Proposed quotes are kept short - a sentence or two, dropped if the cut clip is over `MAX_QUOTE_SECONDS` (12s) - so from a banked quote, which is usually a whole exchange, Claude copies only the words to play (`bank_part`) and those are located in the audio. With the project's experimental `quote_cuts` setting on (a checkbox on the Quotes tab), a proposed quote may be cut together from up to three pieces of one moment (`Quote.parts`): they play back to back with a hard cut, the renderer treating each piece as its own clip and audio splice; setting a quote's range by hand makes it one continuous clip again.

### Captioning providers

`stages/caption.py` has three providers, chosen at call time: `claude_code` (default - shells out to headless `claude -p` with a JSON schema, billed to the Claude subscription), `api` (Anthropic SDK with a forced tool call, metered), and `local` (Ollama; uses a deliberately narrower prompt/schema because small VLMs invent character names and plot). `stages/episode_summary.py` reuses the same provider switch.

The caption schema is built per universe (`build_caption_schema`). Tags exist only for exact filtering, so there are two kinds: `character_tags`, and location as two fields - `region` (an enum of the universe's regions, stored in `Scene.region`) and `place_tags` (optional named place at city level, stored in `Scene.location_tags`). Free-form mood/action tags are no longer requested (`mood_action_tags` stays in the table for old captions). The "known names" hint sent with each call is the 20 most used characters and places of that universe; places are taken only from scenes captioned with the region prompt, because older location tags mix levels of detail.

`Scene.model_version` is written as `provider:model:caption-<hash>`, where the hash is derived from the prompt + schema - editing either changes it automatically, which is how stale-prompt scenes are found.

### Settings

`app/config.py` reads `backend/.env` (static: models, Whisper device, scene-grouping thresholds). `services/runtime_settings.py` layers `backend/data/settings.json` on top for caption provider / model / API key, editable from the UI's settings modal with no restart. Code that needs those three values must go through `current_caption_*()` / `current_anthropic_api_key()`, not `settings.*` directly.

### Storage and search

Everything is one SQLite file, `backend/data/avatar.db` (git-ignored, along with keyframes and audio under `backend/data/`). ORM models are in `db/models.py`; vectors live in `sqlite-vec` virtual tables created by raw SQL in `db/session.py` and accessed through `vectorstore/sqlite_vec_client.py`:

- `scene_text_vec` FLOAT[1024] - `Scene.context_description` embedded with Qwen3-Embedding-0.6B
- `scene_visual_vec` FLOAT[1152] - first keyframe embedded with SigLIP2
- `scene_wiki_vec` FLOAT[1024] - `Scene.wiki_description` (fan-wiki account of the scene), same text model
- `transcript_chunk_vec` FLOAT[1024] - `episode_summary + chunk text`

The dimensions are hard-coded to those models; changing `text_embedding_model` / `visual_embedding_model` means recreating the vec tables and re-embedding. Some comments and docstrings still say "BGE" / "CLIP" from earlier models.

`/api/search` (`api/search.py`) is hybrid: the query is embedded with the text model and with SigLIP's text tower (text-to-image), each KNN is run over its table, and results are merged with reciprocal rank fusion. Tag/book/episode filters are resolved to candidate scene ids first and passed into the KNN. `/api/transcript-chunks` is a separate, coarser RAG-style retrieval over ~60s sliding transcript windows, independent of scene boundaries.

`Scene` and `TranscriptChunk` denormalize `show_title` / `book` / `episode_number` from `Source` for cheap filtering; `PATCH /api/sources/{id}` keeps scenes in sync.

### Schema changes

There are no migrations (alembic is in requirements but unused). `init_db()` calls `create_all`, which creates missing tables but never alters existing ones - a column added to an existing model must also be added with `_ensure_column(...)` in `init_db()` (`db/session.py`), as `scenes.region` is.

### Video editor (in progress)

`backend/app/editor/` + `api/editor.py` + the Editor tab turn a narration audio file into a video built from indexed footage. It is a separate module that only reads the index - it never writes to `avatar.db`. A project belongs to one universe and takes footage only from that universe's shows (all of them, or the ones picked for the project). State lives on disk: one folder per project under a user-chosen root (`editor_projects_root` in `backend/data/settings.json`) holding `project.json`, `transcript.json` (word timings) and the audio.

Stages (`editor/stages.py`) run one at a time per project on a background thread (`editor/jobs.py`); progress is written to `project.json` and the UI polls it. Implemented so far: `transcribe` (word timestamps, script proper nouns passed to Whisper as hotwords) and `beats` (one headless Claude call via `editor/llm.py`; every editor LLM call uses the model from `editor_llm_model()` - Sonnet unless `editor_llm_model` in `settings.json` says otherwise). Beats tile the timeline with no gaps and are split into slots of at most 5s, one clip each. `queries` (one Claude call per 40 beats) writes two queries per slot - a narrative one for the caption index and a no-names frame description for the visual index; `select` (`editor/footage.py`, free) searches both, scores hits as z-scores against a random sample of each index so the two are comparable, and assigns clips in narration order without reusing a scene. Where a clip is cut from the episode is shot-aware (`_clip_window`): the window must contain the moment the scene was found by, and among the positions that do - centred, or starting/ending on a nearby cut - the one with no sliver of a shot (< 0.8s, which reads as a flicker) wins. Candidates carry their flicker count and lose score for it, and a clip that would still end just off a cut trades up to 1s with the next clip in the beat (`end_on_a_cut`), so slots within a beat are not always equal; each run starts again from the even split unless the beat has a locked clip. Each slot keeps 8 candidates; a slot the user picks or re-searches is locked and survives re-runs. Candidates store file path + times, not just scene ids, because re-indexing recreates scenes. The timeline is a list of segments, not the audio file's clock, because quote clips with their own audio will be inserted later. 

`render` (`editor/render.py`) outputs 1920x1080 H.264 at 24000/1001 fps. Each slot is encoded to its own file in `<project>/segments/`, named by a hash of everything that affects its pixels, then the segments are concatenated without re-encoding and the narration is muxed in - so a re-render only encodes what changed (bump `ENCODE_VERSION` after changing the filter graph). Segment lengths are computed from absolute frame positions so rounding never drifts against the audio. Two layouts: `simple` (clip fills the screen) and `framed` (clip in a window over a blurred, darkened background, with a frame PNG from `<projects root>/_frames/` overlaid on top; the window is detected from the PNG's transparent opening). Some source files are 4:3 inside a 16:9 file with baked-in black bars - `source_crop()` detects this once per file (cached in `backend/data/editor_crop_cache.json`). 

`background` (`editor/background.py`) picks one calm scenery shot per beat for the framed layout: one Claude call writes a no-names scenery description per beat, then a visual search runs only over scenes whose indexed keyframe looks like empty scenery (top 6% by a zero-shot scenery-vs-characters score computed from the vectors already in the index; near-black keyframes are skipped). Only the single shot around that keyframe is used, and each beat gets several shots (enough to cover its length with spare). At render time `editor/bgtrack.py` lays them end to end at normal speed with ~0.8s cross-fades into one continuous 480x270 track on the video clock (quotes included), built from small cached pieces - the part of each shot that plays alone and each cross-fade - then concatenated; clips seek into that track and blur/dim it when compositing. Without chosen scenery the background falls back to a blurred copy of the foreground clip.

`quotes` (`editor/quotes.py`) proposes lines of dialogue to drop into the narration, with no vector search: Claude reads the narration and the list of episodes and says what line it wants, after which sentence, and in which 1-3 episodes it expects to find it; Claude then reads the full transcripts of those episodes (a few per call - an episode is only ~3k tokens) and picks exact `TranscriptLine` ids, or nothing; the fragment is re-transcribed with word timestamps to find the exact cut, since the index only has line-level timings. Reading whole episodes beats retrieving one-minute chunks: the model sees who is talking and matches meaning rather than wording. It relies on the model knowing the show; for worlds it doesn't know, episode summaries would have to be added to the first step. Quotes are proposals until the user approves them. An approved quote plays with its own audio between two beats and lengthens the video; beat and slot times stay on the narration clock, and the renderer splices quote segments and audio in at beat boundaries (cut on the same frame for video and audio). Once beats exist, a quote's position snaps to the nearest beat boundary; the beats stage splits beats at quote positions. Any episode with a transcript can be quoted.

`test-data/` (git-ignored) holds a sample narration, script and frame image for developing this.

### Frontend

`src/api/client.ts` is the single API layer: hand-written TypeScript types mirroring the Pydantic schemas in `backend/app/schemas/scenes.py`, with the backend URL hard-coded to `http://localhost:8000` (no Vite proxy). Changing a response schema means updating both. Pages use TanStack Query; routes are declared in `src/App.tsx`.
