"""Runs one editor stage at a time per project on a background thread. Progress and
outcome live in project.json (Project.job), so the UI just polls the project."""

import datetime as dt
import logging
import threading

from app.editor import store
from app.editor.stages import STAGES
from app.editor.store import Job, Project

logger = logging.getLogger(__name__)

_running: set[str] = set()
_running_lock = threading.Lock()


class JobAlreadyRunning(Exception):
    pass


def reconcile(project: Project) -> Project:
    """A job still marked 'running' that this process isn't running was cut off by
    a backend restart - report that instead of showing it as running forever."""
    if project.job and project.job.status == "running" and project.id not in _running:

        def apply(p: Project) -> None:
            p.job.status = "error"
            p.job.error = "Interrupted by a backend restart - run the stage again."

        return store.update_project(project.id, apply)
    return project


def start(project_id: str, stage: str) -> Project:
    if stage not in STAGES:
        raise KeyError(stage)
    with _running_lock:
        if project_id in _running:
            raise JobAlreadyRunning(project_id)
        _running.add(project_id)

    def set_running(p: Project) -> None:
        p.job = Job(stage=stage, status="running", started_at=dt.datetime.now(dt.timezone.utc).isoformat())

    project = store.update_project(project_id, set_running)
    threading.Thread(target=_run, args=(project_id, stage), daemon=True).start()
    return project


def _run(project_id: str, stage: str) -> None:
    def report(detail: str) -> None:
        def apply(p: Project) -> None:
            if p.job:
                p.job.detail = detail

        store.update_project(project_id, apply)

    try:
        STAGES[stage](project_id, report)

        def done(p: Project) -> None:
            p.job = Job(stage=stage, status="done", started_at=p.job.started_at if p.job else "")

        store.update_project(project_id, done)
    except Exception as exc:
        logger.exception("Editor stage %s failed for project %s", stage, project_id)
        message = str(exc) or exc.__class__.__name__

        def failed(p: Project) -> None:
            p.job = Job(stage=stage, status="error", error=message, started_at=p.job.started_at if p.job else "")

        try:
            store.update_project(project_id, failed)
        except Exception:
            logger.exception("Could not record failure for project %s", project_id)
    finally:
        with _running_lock:
            _running.discard(project_id)
