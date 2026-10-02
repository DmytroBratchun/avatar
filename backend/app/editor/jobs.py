"""Runs editor stages for a project on a background thread, one at a time, with a
queue: a stage requested while another is running waits its turn. Progress, the
queue and the outcome live in project.json (Project.job / Project.queue), so the
UI just polls the project."""

import datetime as dt
import logging
import re
import threading

from app.editor import store
from app.editor.stages import STAGES
from app.editor.store import Job, Project

logger = logging.getLogger(__name__)

# projects that have a worker thread right now
_running: set[str] = set()
_running_lock = threading.Lock()

# "finding footage 12/151", "transcribing: 30s / 158s" -> a fraction for the progress bar
_COUNT = re.compile(r"(\d+(?:\.\d+)?)s?\s*/\s*(\d+(?:\.\d+)?)s?\b")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def reconcile(project: Project) -> Project:
    """A job still marked 'running' that this process isn't running was cut off by
    a backend restart - report that instead of showing it as running forever. What
    was queued behind it is dropped too: it was waiting on a stage that never finished."""
    if project.job and project.job.status == "running" and project.id not in _running:

        def apply(p: Project) -> None:
            p.job.status = "error"
            p.job.error = "Interrupted by a backend restart - run the stage again."
            p.queue = []

        return store.update_project(project.id, apply)
    return project


def is_busy(project_id: str) -> bool:
    return project_id in _running


def start(project_id: str, stage: str) -> Project:
    """Starts the stage, or queues it behind whatever the project is running."""
    if stage not in STAGES:
        raise KeyError(stage)
    with _running_lock:
        if project_id in _running:

            def enqueue(p: Project) -> None:
                running = p.job.stage if p.job and p.job.status == "running" else None
                if stage != running and stage not in p.queue:
                    p.queue.append(stage)

            return store.update_project(project_id, enqueue)
        _running.add(project_id)

    def set_running(p: Project) -> None:
        p.job = Job(stage=stage, status="running", started_at=_now())

    project = store.update_project(project_id, set_running)
    threading.Thread(target=_run, args=(project_id, stage), daemon=True).start()
    return project


def unqueue(project_id: str, stage: str) -> Project:
    def apply(p: Project) -> None:
        p.queue = [s for s in p.queue if s != stage]

    return store.update_project(project_id, apply)


def _run(project_id: str, stage: str | None) -> None:
    def report(detail: str) -> None:
        match = _COUNT.search(detail)
        progress = None
        if match and float(match.group(2)) > 0:
            progress = min(1.0, float(match.group(1)) / float(match.group(2)))

        def apply(p: Project) -> None:
            if p.job:
                p.job.detail = detail
                p.job.progress = progress

        store.update_project(project_id, apply)

    while stage is not None:
        current = stage
        try:
            STAGES[current](project_id, report)
        except Exception as exc:
            logger.exception("Editor stage %s failed for project %s", current, project_id)
            message = str(exc) or exc.__class__.__name__

            def failed(p: Project) -> None:
                p.job = Job(stage=current, status="error", error=message, started_at=p.job.started_at if p.job else "")
                # later stages were queued on the assumption this one would finish
                p.queue = []

            with _running_lock:
                try:
                    store.update_project(project_id, failed)
                except Exception:
                    logger.exception("Could not record failure for project %s", project_id)
                _running.discard(project_id)
            return

        # Finished: take the next queued stage, or stop. Done under the lock that
        # start() uses, so a stage queued at this very moment is never left behind.
        with _running_lock:
            next_stage: list[str | None] = [None]

            def advance(p: Project) -> None:
                if p.queue:
                    next_stage[0] = p.queue.pop(0)
                    p.job = Job(stage=next_stage[0], status="running", started_at=_now())
                else:
                    p.job = Job(stage=current, status="done", started_at=p.job.started_at if p.job else "")

            try:
                store.update_project(project_id, advance)
            except Exception:
                logger.exception("Could not advance the queue for project %s", project_id)
                next_stage[0] = None
            stage = next_stage[0]
            if stage is None:
                _running.discard(project_id)
