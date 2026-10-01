import os
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import AUDIO_DIR, settings
from app.db.models import Source, TranscriptLine


def _register_cuda_dll_dirs() -> None:
    """ctranslate2 (faster-whisper's backend) looks for cuBLAS/cuDNN on PATH.
    pip's nvidia-cublas-cu12/nvidia-cudnn-cu12 wheels ship the DLLs inside the
    venv instead of installing them system-wide, so point Windows at them."""
    if sys.platform != "win32":
        return
    site_packages = Path(sys.executable).parent.parent / "Lib" / "site-packages"
    for pkg, subdir in (("cublas", "bin"), ("cudnn", "bin")):
        dll_dir = site_packages / "nvidia" / pkg / subdir
        if dll_dir.is_dir():
            os.add_dll_directory(str(dll_dir))


def _audio_path(source: Source) -> str:
    return str(AUDIO_DIR / f"source_{source.id}.wav")


def extract_audio(source: Source) -> str:
    audio_path = _audio_path(source)
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", source.file_path,
            "-vn", "-ac", "1", "-ar", "16000",
            audio_path,
        ],
        check=True,
        capture_output=True,
    )
    return audio_path


@lru_cache(maxsize=1)
def _whisper_model():
    _register_cuda_dll_dirs()
    from faster_whisper import WhisperModel

    return WhisperModel(
        settings.whisper_model_size,
        device=settings.whisper_device,
        compute_type=settings.whisper_compute_type,
    )


def run_transcription(db: Session, source: Source) -> None:
    # Lines are committed incrementally as whisper yields segments (see the loop
    # below) - a process restart mid-transcription leaves a real but PARTIAL set
    # of committed rows. "any rows exist" used to be treated as "done", silently
    # leaving an episode transcribed only up to wherever the process died. Check
    # the transcript actually reaches near the end of the audio instead.
    existing = list(db.execute(select(TranscriptLine).where(TranscriptLine.source_id == source.id)).scalars())
    if existing:
        last_end = max(line.end_time for line in existing)
        if source.duration_seconds is not None and last_end < source.duration_seconds - 30:
            for line in existing:
                db.delete(line)
            db.flush()
        else:
            return

    source.status = "transcribing"
    db.commit()

    audio_path = extract_audio(source)
    model = _whisper_model()
    segments, info = model.transcribe(audio_path, word_timestamps=False)

    if source.duration_seconds is None:
        source.duration_seconds = info.duration

    for i, segment in enumerate(segments):
        db.add(
            TranscriptLine(
                source_id=source.id,
                start_time=segment.start,
                end_time=segment.end,
                text=segment.text.strip(),
            )
        )
        if i % 50 == 0:
            source.current_stage_detail = f"transcribing: {segment.start:.0f}s / {info.duration:.0f}s"
            db.commit()

    db.flush()
