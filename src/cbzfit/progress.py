# SPDX-License-Identifier: GPL-3.0-or-later

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


class ArchiveProgressPhase(StrEnum):
    """Identify a phase of archive-file processing."""

    TRANSFORMING = "transforming"
    VERIFYING = "verifying"
    PUBLISHING = "publishing"


@dataclass(frozen=True)
class ArchiveProgress:
    """Describe progress through one archive-processing phase.

    Transformation and CRC-verification events provide completed and total
    file-member counts. Structural verification and publication use
    indeterminate events without counts. A member name identifies the file
    member most recently completed. A publication event signals entry into the
    publication phase, not successful publication.
    """

    phase: ArchiveProgressPhase
    completed: int | None = None
    total: int | None = None
    member_name: str | None = None

    def __post_init__(self) -> None:
        """Validate progress-event consistency."""
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
        if self.completed is not None and self.total is not None:
            if (
                not isinstance(self.completed, int)
                or isinstance(self.completed, bool)
                or not isinstance(self.total, int)
                or isinstance(self.total, bool)
            ):
                raise TypeError("Progress counts must be integers.")
            if self.completed < 0 or self.total < 0:
                raise ValueError("Progress counts must not be negative.")
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
