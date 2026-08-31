# SPDX-License-Identifier: GPL-3.0-or-later

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TextIO

PROGRESS_BAR_WIDTH = 20
COMPLETED_PROGRESS_CHARACTER = "█"
REMAINING_PROGRESS_CHARACTER = "─"


class ArchiveProgressPhase(StrEnum):
    """Identify a phase of archive-file processing."""

    TRANSFORMING = "transforming"
    VERIFYING = "verifying"
    PUBLISHING = "publishing"


@dataclass(frozen=True)
class ArchiveProgress:
    """Describe valid progress through one archive-processing phase.

    Transformation always has a positive total and reports completed file
    members from zero through that total. CRC verification uses the same
    determinate shape. Structural verification is indeterminate. Publication
    is an indeterminate lifecycle event that signals entry into publication,
    not successful publication. A member name identifies the member most
    recently completed.
    """

    phase: ArchiveProgressPhase
    completed: int | None = None
    total: int | None = None
    member_name: str | None = None

    def __post_init__(self) -> None:
        """Validate counts, member details, and phase-specific event shape."""
        if not isinstance(self.phase, ArchiveProgressPhase):
            raise TypeError(
                "Archive progress phase must be an ArchiveProgressPhase, not "
                f"{type(self.phase).__name__}."
            )

        counts_are_partial = (self.completed is None) != (self.total is None)
        if counts_are_partial:
            raise ValueError(
                "Completed and total progress counts must be provided together."
            )

        is_determinate = self.completed is not None
        if self.phase is ArchiveProgressPhase.TRANSFORMING and not is_determinate:
            raise ValueError("Transformation progress requires member counts.")
        if self.phase is ArchiveProgressPhase.PUBLISHING and is_determinate:
            raise ValueError("Publication progress must not contain member counts.")

        if is_determinate:
            if not isinstance(self.completed, int) or not isinstance(self.total, int):
                raise TypeError("Progress counts must be integers.")
            if self.completed < 0:
                raise ValueError("Completed progress must not be negative.")
            if self.total <= 0:
                raise ValueError("Total progress must be a positive integer.")
            if self.completed > self.total:
                raise ValueError(
                    "Completed progress must not exceed total progress."
                )

        if self.member_name is not None:
            if not isinstance(self.member_name, str):
                raise TypeError("Progress member name must be a string.")
            if not self.member_name:
                raise ValueError("Progress member name must not be empty.")
            if self.completed is None or self.completed == 0:
                raise ValueError(
                    "A progress member name requires completed work."
                )


ProgressCallback = Callable[[ArchiveProgress], None]


def report_progress(
    callback: ProgressCallback | None,
    progress: ArchiveProgress,
) -> None:
    """Send one progress event when a callback is configured."""
    if callback is not None:
        callback(progress)


def _calculate_progress_geometry(
    completed: int,
    total: int,
    bar_width: int,
) -> tuple[int, int]:
    """Return floor-rounded percentage and completed bar cells."""
    if bar_width <= 0:
        raise ValueError("Progress bar width must be a positive integer.")
    return (
        completed * 100 // total,
        completed * bar_width // total,
    )


def _format_progress(
    progress: ArchiveProgress,
    *,
    bar_width: int = PROGRESS_BAR_WIDTH,
) -> str | None:
    """Format visible progress, or return None for silent publication."""
    if progress.phase is ArchiveProgressPhase.PUBLISHING:
        return None
    if progress.phase is ArchiveProgressPhase.VERIFYING and progress.completed is None:
        return "Verifying output archive..."

    label = (
        "Processing"
        if progress.phase is ArchiveProgressPhase.TRANSFORMING
        else "Verifying "
    )
    percentage, completed_cells = _calculate_progress_geometry(
        progress.completed,
        progress.total,
        bar_width,
    )
    bar = (
        COMPLETED_PROGRESS_CHARACTER * completed_cells
        + REMAINING_PROGRESS_CHARACTER * (bar_width - completed_cells)
    )
    return (
        f"{label} [{bar}] {percentage} % "
        f"({progress.completed}/{progress.total})"
    )


class TerminalProgressRenderer:
    """Render transient archive progress to a text stream."""

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream
        self._rendered_length = 0

    def report(self, progress: ArchiveProgress) -> None:
        """Render visible progress or silently clear for publication."""
        text = _format_progress(progress)
        if text is None:
            self.clear()
        else:
            self._render(text)

    def clear(self) -> None:
        """Erase the active progress line without leaving a newline."""
        if self._rendered_length == 0:
            return
        self._stream.write("\r" + " " * self._rendered_length + "\r")
        self._stream.flush()
        self._rendered_length = 0

    def _render(self, text: str) -> None:
        """Replace the active line and erase stale trailing characters."""
        # Progress text uses characters expected to occupy one terminal column.
        trailing_spaces = max(self._rendered_length - len(text), 0)
        self._stream.write("\r" + text + " " * trailing_spaces)
        self._stream.flush()
        self._rendered_length = len(text)
