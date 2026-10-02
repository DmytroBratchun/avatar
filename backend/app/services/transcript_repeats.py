"""Whisper sometimes gets stuck on music or silence and emits the same line over and
over - "Iroh!" 44 times, one per second. Those runs are noise in the transcript,
the transcript chunks and anything searched through them."""

from collections.abc import Callable, Sequence
from typing import TypeVar

# Five identical lines in a row practically never happens in real dialogue; three or
# four does (people repeat themselves, or a shout gets split), so those are left alone.
MIN_REPEATS = 5

T = TypeVar("T")


def repeated_runs(lines: Sequence[T], text_of: Callable[[T], str]) -> list[list[T]]:
    """Runs of MIN_REPEATS or more consecutive lines with the same text, in order."""
    runs: list[list[T]] = []
    current: list[T] = []
    for line in lines:
        if current and text_of(line).strip().lower() == text_of(current[0]).strip().lower():
            current.append(line)
            continue
        if len(current) >= MIN_REPEATS:
            runs.append(current)
        current = [line]
    if len(current) >= MIN_REPEATS:
        runs.append(current)
    return runs


def lines_to_drop(lines: Sequence[T], text_of: Callable[[T], str]) -> list[T]:
    """Every line of a repeated run except the first - the first is usually the real
    one, and what follows is the model stuck on it."""
    return [line for run in repeated_runs(lines, text_of) for line in run[1:]]
