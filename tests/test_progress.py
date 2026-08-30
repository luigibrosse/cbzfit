# SPDX-License-Identifier: GPL-3.0-or-later
import re
from unittest.mock import Mock

import pytest

from cbzfit.progress import (
    ArchiveProgress,
    ArchiveProgressPhase,
    report_progress,
)


def exact_message(message: str) -> str:
    """Return a regular expression that matches a complete error message."""
    return rf"^{re.escape(message)}$"


class TestArchiveProgressPhase:
    def test_supported_phases_have_expected_values(self) -> None:
        assert ArchiveProgressPhase.TRANSFORMING == "transforming"
        assert ArchiveProgressPhase.VERIFYING == "verifying"
        assert ArchiveProgressPhase.PUBLISHING == "publishing"


class TestArchiveProgress:
    def test_determinate_progress_fields_are_retained(self) -> None:
        progress = ArchiveProgress(
            phase=ArchiveProgressPhase.TRANSFORMING,
            completed=2,
            total=5,
            member_name="Chapter 01/002.png",
        )
        assert progress.phase is ArchiveProgressPhase.TRANSFORMING
        assert progress.completed == 2
        assert progress.total == 5
        assert progress.member_name == "Chapter 01/002.png"

    def test_indeterminate_progress_fields_default_to_none(self) -> None:
        progress = ArchiveProgress(phase=ArchiveProgressPhase.VERIFYING)
        assert progress.completed is None
        assert progress.total is None
        assert progress.member_name is None

    def test_invalid_phase_type_is_rejected(self) -> None:
        with pytest.raises(
            TypeError,
            match=exact_message(
                "Archive progress phase must be an ArchiveProgressPhase, not str."
            ),
        ):
            ArchiveProgress(phase="transforming")  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("completed", "total"),
        [(None, 1), (0, None)],
    )
    def test_partial_counts_are_rejected(
        self,
        completed: int | None,
        total: int | None,
    ) -> None:
        with pytest.raises(
            ValueError,
            match=exact_message(
                "Completed and total progress counts must be provided together."
            ),
        ):
            ArchiveProgress(
                phase=ArchiveProgressPhase.TRANSFORMING,
                completed=completed,
                total=total,
            )

    @pytest.mark.parametrize(
        ("completed", "total"),
        [(False, 1), (0, True), (0.0, 1), (0, 1.0)],
    )
    def test_non_integer_counts_are_rejected(
        self,
        completed: object,
        total: object,
    ) -> None:
        with pytest.raises(
            TypeError,
            match=exact_message("Progress counts must be integers."),
        ):
            ArchiveProgress(
                phase=ArchiveProgressPhase.TRANSFORMING,
                completed=completed,  # type: ignore[arg-type]
                total=total,  # type: ignore[arg-type]
            )

    @pytest.mark.parametrize(
        ("completed", "total"),
        [(-1, 1), (0, -1)],
    )
    def test_negative_counts_are_rejected(
        self,
        completed: int,
        total: int,
    ) -> None:
        with pytest.raises(
            ValueError,
            match=exact_message("Progress counts must not be negative."),
        ):
            ArchiveProgress(
                phase=ArchiveProgressPhase.TRANSFORMING,
                completed=completed,
                total=total,
            )

    def test_completed_count_above_total_is_rejected(self) -> None:
        with pytest.raises(
            ValueError,
            match=exact_message(
                "Completed progress must not exceed total progress."
            ),
        ):
            ArchiveProgress(
                phase=ArchiveProgressPhase.TRANSFORMING,
                completed=2,
                total=1,
            )

    def test_non_string_member_name_is_rejected(self) -> None:
        with pytest.raises(
            TypeError,
            match=exact_message("Progress member name must be a string."),
        ):
            ArchiveProgress(
                phase=ArchiveProgressPhase.TRANSFORMING,
                completed=1,
                total=1,
                member_name=1,  # type: ignore[arg-type]
            )

    def test_empty_member_name_is_rejected(self) -> None:
        with pytest.raises(
            ValueError,
            match=exact_message("Progress member name must not be empty."),
        ):
            ArchiveProgress(
                phase=ArchiveProgressPhase.TRANSFORMING,
                completed=1,
                total=1,
                member_name="",
            )

    @pytest.mark.parametrize("completed", [None, 0])
    def test_member_name_requires_completed_work(
        self,
        completed: int | None,
    ) -> None:
        total = None if completed is None else 1
        with pytest.raises(
            ValueError,
            match=exact_message(
                "A progress member name requires completed work."
            ),
        ):
            ArchiveProgress(
                phase=ArchiveProgressPhase.TRANSFORMING,
                completed=completed,
                total=total,
                member_name="001.png",
            )


class TestReportProgress:
    def test_configured_callback_receives_progress(self) -> None:
        callback = Mock()
        progress = ArchiveProgress(phase=ArchiveProgressPhase.PUBLISHING)
        report_progress(callback, progress)
        callback.assert_called_once_with(progress)

    def test_missing_callback_is_ignored(self) -> None:
        report_progress(
            None,
            ArchiveProgress(phase=ArchiveProgressPhase.VERIFYING),
        )

    def test_callback_error_is_propagated(self) -> None:
        error = RuntimeError("Progress consumer failed")
        callback = Mock(side_effect=error)
        progress = ArchiveProgress(phase=ArchiveProgressPhase.PUBLISHING)
        with pytest.raises(RuntimeError) as exception_info:
            report_progress(callback, progress)
        assert exception_info.value is error
