# TODO

## Multi-show support (Korra, other series)
- [ ] Add `show_title` field to `Source` (nullable, backfill existing rows as "Avatar: The Last Airbender")
- [ ] Un-hardcode `_episode_primer()` in `caption.py` - currently always says "Avatar: The Last Airbender" regardless of source
- [ ] Scope `/api/scenes/facets` (character/location/mood autocomplete) by show, or at least allow filtering
- [ ] Search UI: multi-select "which show(s) to include" (not just a single filter) - checkbox list populated from distinct `show_title` values, passed as a list to `/api/search` (same pattern as the character multi-select)

## Model provenance / re-caption by model
- [x] Already have `Scene.model_version` (e.g. `"claude_code:claude-opus-5:caption-prompt-v1"`) written on every caption - this is the data needed to know which model produced which description
- [ ] Not yet exposed: add `model_version` to `SceneOut`/`SceneDetailOut` so it's visible in the UI (e.g. small label on Scene Detail)
- [ ] Add a filter/facet by `model_version` (which model, which prompt version) so a bad batch is findable
- [ ] Add a bulk "re-caption these" action - given a filter (e.g. `model_version` prefix, or a show/episode range), reset matching scenes' `caption_status` back to `pending` so the worker re-runs just those instead of a whole episode

## Comics support (screenshots instead of video)
- [ ] Add `media_type` to `Source` (`video` | `comic`)
- [ ] New lightweight ingestion path for comics: skip transcribe/detect_shots/group_scenes entirely, treat each page/panel image directly as a "scene" (page/panel index instead of start_time/end_time)
- [ ] Adjust captioning prompt for comics - no dialogue/audio, but speech bubble text is readable directly from the image
- [ ] `Scene.start_time`/`end_time` are non-nullable floats - decide how comics fit this (nullable? separate page/panel index field?)

## Infra / reliability
- [ ] Switch SQLite to WAL mode - already hit lock contention a couple times (backend startup blocked while a worker was mid-write)
- [ ] Parallelize captioning calls (currently strictly sequential, one scene at a time - real speedup available)
- [ ] Consider Batches API for captioning (50% discount) once prompt is stable and runs are unattended/overnight

## Data quality
- [ ] Deeper tag de-dup pass - the one-off `normalize_tags.py` script only merges casing/plural variants; semantic duplicates remain (e.g. "Fire Nation ship" vs "Fire Navy shipwreck" - same place, different wording, needs embedding-similarity or LLM-based merge)
- [x] Whisper repeat hallucinations ("Yip-yip!" x30, "Iroh!" x44 ...) - 4022 lines in 105 episodes removed 2026-10-02 with `scripts/collapse_transcript_repeats.py` (backup: `backend/data/avatar.before-repeat-cleanup.db`); new transcriptions use `condition_on_previous_text=False` and drop runs of 5+ identical lines
- [ ] Episode titles parsed from filenames have a cosmetic double-dash artifact ("Avatar The Last Airbender - - The Boy in the Iceberg") from the `SxxEyy` strip in `episode_parser.py`

## Cleanup
- [ ] Transcript chunks are no longer used by anything in the UI (removed 2026-10-02; quotes now read whole episodes). The table, vectors, `/api/transcript-chunks`, the chunking stage and the chunk reindex queue are still in the backend - remove them or keep for a future universe.
- [ ] `transcript_fts` (FTS5 virtual table) is dead code - created in `db/session.py` but never populated or queried; transcript search ended up using plain `LIKE` instead. Remove it or actually wire it up.

## Remaining indexing work
- [ ] Run captioning on the remaining ~37 episodes sitting at `ready_for_captioning` (local stages already done, free) - pick provider/model (Claude Code subscription vs API key + sonnet-5) per cost tolerance

## Future project: audio-to-video generator
- [ ] Separate app/service that takes a narration audio track, segments it, calls this project's `/api/search` per segment to find matching scenes, builds an EDL, renders via ffmpeg. Consumes the search API as a client - doesn't duplicate indexing/vector search logic. See chat history for full design discussion.
