"""Voice-over: the project's script read by a synthetic voice, through Lumean
(app/services/lumean.py), to be used as the narration.

Lumean cuts the script into fragments and voices each one. Every fragment is kept
here with its text and its audio, so it can be listened to on its own and voiced
again with other wording - a different stress, an emotion tag, a rejected sentence
reworded - without redoing the rest. Lumean then joins the fragments again.

State lives in <project>/voiceover.json and the audio in <project>/voiceover/. The
`voiceover` stage only brings that state in line with the order: it sends the
script when there is no order yet, waits while fragments are being voiced, and
downloads what changed. Asking for a fragment again is a quick API call followed
by the same stage.

The finished audio becomes the project's narration only when asked (or by itself
when the project has none): a new narration discards the transcript and everything
built on it.
"""

import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

from pydantic import BaseModel, Field

from app.editor import store
from app.services.lumean import Lumean, LumeanError
from app.services.runtime_settings import editor_default_voice

STATE_FILE = "voiceover.json"
FOLDER = "voiceover"
SERVICE = "elevenlabs"
POLL_SECONDS = 4
MAX_WAIT_SECONDS = 2 * 60 * 60
JOIN_WAIT_SECONDS = 180  # how long to wait for the fragments to be joined again
WORKING = {"created", "pending", "in_progress"}
DEAD = {"failed", "compensated", "cancelled"}
# stability on ElevenLabs v3 models is one of three steps
V3_STABILITY = (0.0, 0.5, 1.0)


class VoiceSettings(BaseModel):
    voice_id: str = ""
    voice_name: str = ""
    # a voice from ElevenLabs' public library is named together with its owner
    public_owner_id: str = ""
    model_id: str = "eleven_v4"
    language_code: str = "en"  # empty = let the model work the language out
    stability: float = 0.5
    similarity_boost: float = 0.75
    style: float = 0.0
    speed: float = 1.0
    # cut the script at empty lines first, so a fragment never spans two paragraphs
    # (each paragraph is then billed at least the service's minimum)
    by_paragraph: bool = False


class Fragment(BaseModel):
    """One chunk of the script as it currently stands: its latest voicing."""

    index: int
    root_id: str  # the chunk as first voiced
    item_id: str  # the version described here
    status: str  # pending | processing | completed | policy_flagged | failed
    text: str = ""
    file: str | None = None  # under the voiceover folder
    duration_ms: int | None = None
    versions: int = 1


class Voiceover(BaseModel):
    settings: VoiceSettings = Field(default_factory=VoiceSettings)
    template_id: str | None = None
    template_config: dict | None = None  # what the template was last saved with
    order_id: str | None = None
    order_status: str | None = None
    order_text: str | None = None  # the script as it was sent
    fragments: list[Fragment] = Field(default_factory=list)
    final_file: str | None = None  # the joined audio, under the voiceover folder
    final_version: str | None = None
    service_files: list[str] = Field(default_factory=list)  # subtitles, timings
    tokens_spent: int | None = None
    used_as_narration: str | None = None  # the final_version the project's audio was taken from
    updated_at: str = ""


def default_voice() -> VoiceSettings:
    """The voice a project starts with: the saved default, if there is one."""
    try:
        return VoiceSettings.model_validate(editor_default_voice())
    except ValueError:
        return VoiceSettings()


def load(project_id: str) -> Voiceover:
    path = store.project_dir(project_id) / STATE_FILE
    if not path.is_file():
        return Voiceover(settings=default_voice())
    return Voiceover.model_validate_json(path.read_text(encoding="utf-8"))


def save(project_id: str, state: Voiceover) -> None:
    state.updated_at = store.now()
    path = store.project_dir(project_id) / STATE_FILE
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def folder(project_id: str) -> Path:
    path = store.project_dir(project_id) / FOLDER
    path.mkdir(exist_ok=True)
    return path


def template_config(settings: VoiceSettings) -> dict:
    """The Lumean template config for these settings (ElevenLabs)."""
    stability = min(1.0, max(0.0, settings.stability))
    if settings.model_id.startswith("eleven_v3"):
        stability = min(V3_STABILITY, key=lambda step: abs(step - stability))
    tts = {
        "mode": "mode_v1",
        "model_id": settings.model_id,
        "voice_id": settings.voice_id,
        # each fragment is voiced knowing the text around it, so the intonation carries across
        "prev_next_text": True,
        # without this flag similarity, style and speaker boost are silently ignored
        "advanced_voice_settings": True,
        "voice_settings": {
            "stability": stability,
            "similarity_boost": min(1.0, max(0.0, settings.similarity_boost)),
            "style": min(1.0, max(0.0, settings.style)),
            "use_speaker_boost": True,
            "speed": min(1.2, max(0.7, settings.speed)),
        },
    }
    if settings.language_code.strip():
        tts["language_code"] = settings.language_code.strip()
    if settings.public_owner_id:
        tts["public_owner_id"] = settings.public_owner_id
    config: dict = {"tts_settings": tts}
    if settings.by_paragraph:
        config["generation_mode"] = "paragraph"
    return config


def ensure_template(client: Lumean, state: Voiceover, title: str) -> str:
    """The id of this project's Lumean template, created or updated to match the
    settings. The voice can only be given to an order through a template."""
    if not state.settings.voice_id:
        raise RuntimeError("Choose a voice first")
    config = template_config(state.settings)
    name = f"Avatar editor: {title}"
    if state.template_id and state.template_config == config:
        return state.template_id
    if state.template_id:
        try:
            client.update_template(state.template_id, SERVICE, name, config)
            state.template_config = config
            return state.template_id
        except LumeanError as exc:
            if exc.status != 404:
                raise
            # deleted on Lumean's side: make a new one
    state.template_id = client.create_template(SERVICE, name, config)["id"]
    state.template_config = config
    return state.template_id


def own_voices() -> list[dict]:
    """The voices of the account's existing Lumean templates, with the settings each
    template uses - voices the user has already chosen and tuned there."""
    voices = []
    for template in Lumean().templates(SERVICE):
        tts = (template.get("config") or {}).get("tts_settings") or {}
        if not tts.get("voice_id"):
            continue
        tuned = tts.get("voice_settings") or {}
        defaults = VoiceSettings()
        voices.append(
            VoiceSettings(
                voice_id=tts["voice_id"],
                voice_name=template.get("name") or tts["voice_id"],
                public_owner_id=tts.get("public_owner_id") or "",
                model_id=tts.get("model_id") or defaults.model_id,
                language_code=tts.get("language_code") or "",
                stability=tuned.get("stability") if tuned.get("stability") is not None else defaults.stability,
                similarity_boost=tuned.get("similarity_boost") if tuned.get("similarity_boost") is not None else defaults.similarity_boost,
                style=tuned.get("style") if tuned.get("style") is not None else defaults.style,
                speed=tuned.get("speed") if tuned.get("speed") is not None else defaults.speed,
            ).model_dump()
        )
    return voices


def estimate(project_id: str) -> dict:
    """What voicing the script would cost and how it would be cut - free."""
    project = store.load_project(project_id)
    text = (project.script_text or "").strip()
    if not text:
        raise RuntimeError("The project has no script to voice")
    state = load(project_id)
    client = Lumean()
    template_id = ensure_template(client, state, project.title)
    save(project_id, state)
    preview = client.preview(template_id, text)
    summary = preview.get("summary") or {}
    try:
        remaining = client.tokens_remaining()
    except LumeanError:
        remaining = None
    return {
        "ready": preview.get("status") == "ready",
        "characters": len(text),
        "fragments": (preview.get("page") or {}).get("total") or len(preview.get("chunks") or []) or None,
        "summary": summary,
        "blocked_fragments": summary.get("blocked_chunks") or [],
        "tokens_remaining": remaining,
    }


def _flatten(items: list[dict]) -> dict[str, dict]:
    """Every item by id, including the retries nested inside others."""
    found: dict[str, dict] = {}
    todo = list(items)
    while todo:
        item = todo.pop()
        if not isinstance(item, dict) or item.get("id") in found:
            continue
        found[item["id"]] = item
        todo.extend(item.get("retries") or [])
        if item.get("parent_item"):
            todo.append(item["parent_item"])
    return found


def _versions(items: dict[str, dict]) -> list[tuple[dict, list[dict]]]:
    """(chunk, its voicings oldest first) for every chunk of the order, in order. A
    retry or regeneration points at the item it redoes; the chain leads to the chunk."""

    def root(item: dict) -> dict | None:
        seen = set()
        while item.get("item_type") != "chunk":
            parent = items.get(item.get("parent_item_id") or "")
            if parent is None or parent["id"] in seen:
                return None
            seen.add(parent["id"])
            item = parent
        return item

    chains: dict[str, list[dict]] = {}
    for item in items.values():
        if item.get("item_type") not in ("chunk", "retry", "regeneration"):
            continue
        chunk = root(item)
        if chunk is not None:
            chains.setdefault(chunk["id"], []).append(item)
    chunks = sorted((items[i] for i in chains), key=lambda c: c.get("chunk_index") or 0)
    return [(c, sorted(chains[c["id"]], key=lambda i: (i.get("created_at") or "", i.get("attempt_number") or 0))) for c in chunks]


def _current(versions: list[dict]) -> dict:
    """The voicing that counts: the latest, unless it is a failed attempt at a
    fragment that had been voiced fine before."""
    latest = versions[-1]
    if latest.get("status") in ("failed", "policy_flagged"):
        done = [v for v in versions if v.get("status") == "completed"]
        if done and latest.get("item_type") == "regeneration":
            return done[-1]
    return latest


def _file_path(result_file) -> str | None:
    if isinstance(result_file, str):
        return result_file or None
    if isinstance(result_file, dict):
        return result_file.get("path") or result_file.get("file") or None
    return None


def _order_items(client: Lumean, order: dict) -> dict[str, dict]:
    items = order.get("items")
    if items is None:
        items = client.order_items(order["id"])
    return _flatten(items)


def _last_stitch(items: dict[str, dict]) -> dict | None:
    """The latest finished joining of the fragments into one file."""
    done = [i for i in items.values() if i.get("item_type") == "stitch" and i.get("status") == "completed"]
    return max(done, key=lambda i: i.get("created_at") or "", default=None)


def _joined(items: dict[str, dict]) -> bool:
    """Whether the joined file includes every fragment's latest voicing. After a
    fragment is voiced again Lumean joins them anew, as a separate step that only
    appears in the order a moment after the fragment is done - while the order
    itself keeps saying "completed" throughout."""
    voiced = [
        i.get("created_at") or ""
        for i in items.values()
        if i.get("item_type") in ("chunk", "retry", "regeneration") and i.get("status") == "completed"
    ]
    stitch = _last_stitch(items)
    return not voiced or (stitch is not None and (stitch.get("created_at") or "") >= max(voiced))


def _wait(client: Lumean, order_id: str, report) -> dict:
    """Polls the order until nothing in it is being voiced or joined."""
    deadline = time.monotonic() + MAX_WAIT_SECONDS
    joining_since: float | None = None
    while True:
        order = client.order(order_id)
        items = _order_items(client, order)
        voicing = [i for i in items.values() if i.get("status") in ("pending", "processing")]
        if order.get("status") not in WORKING and not voicing:
            if order.get("status") not in ("completed", "result_delivered") or _joined(items):
                return order
            # every fragment is voiced but the joined file is not rebuilt yet
            joining_since = joining_since or time.monotonic()
            if time.monotonic() - joining_since > JOIN_WAIT_SECONDS:
                return order
            report("joining the fragments")
            time.sleep(POLL_SECONDS)
            continue
        total = order.get("total_chunks") or 0
        if total:
            report(f"voicing fragments {order.get('completed_chunks') or 0}/{total}")
        else:
            report("waiting for Lumean to start")
        if time.monotonic() > deadline:
            raise RuntimeError("Lumean is still working on the order - run the voice-over again later to pick it up")
        time.sleep(POLL_SECONDS)


def _sync(client: Lumean, project_id: str, state: Voiceover, order: dict, report) -> None:
    """Brings the fragments, their audio and the joined file in line with the order."""
    out = folder(project_id)
    (out / "fragments").mkdir(exist_ok=True)
    known = {f.item_id: f for f in state.fragments}
    fragments: list[Fragment] = []
    chains = _versions(_order_items(client, order))
    for n, (chunk, versions) in enumerate(chains):
        report(f"fetching fragments {n + 1}/{len(chains)}")
        item = _current(versions)
        before = known.get(item["id"])
        text = before.text if before and before.text else ""
        if not text:
            try:
                text = client.item_text(order["id"], item["id"])
            except LumeanError as exc:
                if exc.status not in (404, 422):  # the text is deleted with the order's files
                    raise
        file = before.file if before and before.file and (out / before.file).is_file() else None
        source = _file_path(item.get("result_file"))
        if file is None and source and item.get("status") == "completed":
            file = f"fragments/{(chunk.get('chunk_index') or 0) + 1:03d}_{item['id'][:8]}{Path(source).suffix or '.mp3'}"
            client.download(source, out / file)
        fragments.append(
            Fragment(
                index=chunk.get("chunk_index") or 0,
                root_id=chunk["id"],
                item_id=item["id"],
                status=item.get("status") or "pending",
                text=text,
                file=file,
                duration_ms=item.get("duration_ms"),
                versions=len(versions),
            )
        )
    state.fragments = fragments
    state.order_status = order.get("status")
    state.tokens_spent = order.get("tokens_spent") or order.get("total_tokens")

    result = order.get("result") or {}
    files = result.get("files") or []
    if order.get("status") in ("completed", "result_delivered") and files:
        # the joined file keeps its path when it is rebuilt, and the order its date:
        # what tells one version from the next is which joining produced it
        stitch = _last_stitch(_order_items(client, order)) or {}
        version = hashlib.sha1(f"{files[0]}|{stitch.get('id') or order.get('updated_at')}".encode()).hexdigest()[:10]
        if state.final_version != version or not state.final_file or not (out / state.final_file).is_file():
            report("fetching the joined audio")
            # a name of its own each time: the previous file may be playing in the browser
            name = f"narration_{version}{Path(files[0]).suffix or '.mp3'}"
            client.download(files[0], out / name)
            state.final_file, state.final_version = name, version
            (out / "service").mkdir(exist_ok=True)
            state.service_files = []
            for path in result.get("service_files") or []:
                try:
                    client.download(path, out / "service" / Path(path).name)
                    state.service_files.append(f"service/{Path(path).name}")
                except LumeanError:
                    continue  # subtitles are a bonus
            for stale in set(out.glob("narration_*")) - {out / name}:
                try:
                    stale.unlink()
                except OSError:
                    pass

    kept = {f.file for f in fragments if f.file}
    for stale in (out / "fragments").iterdir():
        if f"fragments/{stale.name}" not in kept:
            try:
                stale.unlink()
            except OSError:
                pass


def run_voiceover(project_id: str, report) -> None:
    """The stage. Sends the script when nothing has been ordered yet, then waits for
    the order and fetches its audio. Safe to run again at any time: with an order
    in place it costs nothing."""
    project = store.load_project(project_id)
    state = load(project_id)
    client = Lumean()

    if not state.order_id:
        text = (project.script_text or "").strip()
        if not text:
            raise RuntimeError("The project has no script to voice")
        report("preparing the voice")
        ensure_template(client, state, project.title)
        save(project_id, state)
        report("sending the script")
        order = client.create_order(state.template_id, text, name=project.title)
        state.order_id, state.order_text, state.order_status = order["id"], text, order.get("status")
        state.fragments, state.final_file, state.final_version, state.service_files = [], None, None, []
        save(project_id, state)

    order = _wait(client, state.order_id, report)
    _sync(client, project_id, state, order, report)
    save(project_id, state)

    status = order.get("status")
    if status in DEAD:
        raise RuntimeError(f"Lumean could not voice the script (order {status}) - generate the voice-over again")
    broken = [f for f in state.fragments if f.status in ("failed", "policy_flagged")]
    if broken:
        flagged = sum(1 for f in broken if f.status == "policy_flagged")
        raise RuntimeError(
            f"{len(broken)} of {len(state.fragments)} fragments were not voiced"
            + (f" ({flagged} rejected by the content policy - reword them)" if flagged else "")
            + " - see the Voice tab"
        )
    if state.final_file and not project.audio_file:
        use_as_narration(project_id)


def start_new(project_id: str) -> None:
    """Forgets the current order, so that the next run sends the script again."""
    state = load(project_id)
    state.order_id = state.order_status = state.order_text = None
    state.fragments, state.final_file, state.final_version, state.service_files = [], None, None, []
    state.tokens_spent = None
    save(project_id, state)


def redo_fragment(project_id: str, index: int, text: str | None) -> None:
    """Asks Lumean to voice one fragment again. A fragment that failed is retried
    (free within what was paid; other wording if the content policy rejected it); a
    finished one is regenerated with the given text, which is paid for again."""
    state = load(project_id)
    if not state.order_id:
        raise RuntimeError("There is no voice-over yet")
    fragment = next((f for f in state.fragments if f.index == index), None)
    if fragment is None:
        raise RuntimeError("No such fragment")
    text = (text or "").strip()
    client = Lumean()
    if fragment.status == "completed":
        client.regenerate_item(state.order_id, fragment.item_id, text or fragment.text)
    elif fragment.status in ("failed", "policy_flagged"):
        client.retry_item(state.order_id, fragment.item_id, text if text and text != fragment.text else None)
    else:
        raise RuntimeError("This fragment is still being voiced")
    fragment.status = "pending"
    if text:
        fragment.text = text
    fragment.item_id = ""  # a new version is on its way; the stage picks it up
    fragment.file = None
    save(project_id, state)


def retry_failed(project_id: str) -> dict:
    """Queues every technically failed fragment again in one call. Returns Lumean's
    plan: what was queued and what was skipped, with the reason."""
    state = load(project_id)
    if not state.order_id:
        raise RuntimeError("There is no voice-over yet")
    return Lumean().retry_failed(state.order_id)


def _duration(path: Path) -> float | None:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    try:
        return round(float(result.stdout.strip()), 3)
    except ValueError:
        return None


def use_as_narration(project_id: str) -> store.Project:
    """Makes the joined voice-over the project's narration. Like uploading new
    audio, this discards the transcript and the beats and quotes built on it."""
    state = load(project_id)
    source = folder(project_id) / (state.final_file or "")
    if not state.final_file or not source.is_file():
        raise RuntimeError("The voice-over is not finished yet")
    root = store.project_dir(project_id)
    # a new name each time: the old narration may be open in the browser
    target = root / f"voiceover_{state.final_version}{source.suffix}"
    shutil.copyfile(source, target)
    duration = _duration(target)
    if duration is None:
        target.unlink(missing_ok=True)
        raise RuntimeError("The voice-over file is not readable audio")
    previous = store.load_project(project_id).audio_file
    if previous and previous != target.name:
        try:
            (root / previous).unlink(missing_ok=True)
        except OSError:
            pass
    (root / store.TRANSCRIPT_FILE).unlink(missing_ok=True)

    def apply(p: store.Project) -> None:
        p.audio_file = target.name
        p.audio_duration = duration
        p.word_count = 0
        p.timeline = []
        p.beats = []
        p.quotes = []

    project = store.update_project(project_id, apply)
    state.used_as_narration = state.final_version
    save(project_id, state)
    return project
