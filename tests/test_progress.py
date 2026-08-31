# SPDX-License-Identifier: GPL-3.0-or-later

import re
from io import StringIO
from unittest.mock import Mock

import pytest

import cbzfit.progress as progress_module
from cbzfit.progress import (
    ArchiveProgress,
    ArchiveProgressPhase,
    TerminalProgressRenderer,
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

    @pytest.mark.parametrize(
        "phase",
        [
            ArchiveProgressPhase.VERIFYING,
            ArchiveProgressPhase.PUBLISHING,
        ],
    )
    def test_supported_indeterminate_phase_fields_default_to_none(
        self,
        phase: ArchiveProgressPhase,
    ) -> None:
        progress = ArchiveProgress(phase=phase)
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
        [(0.0, 1), (0, 1.0)],
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

    def test_boolean_counts_follow_normal_integer_behavior(self) -> None:
        progress = ArchiveProgress(
            phase=ArchiveProgressPhase.TRANSFORMING,
            completed=False,
            total=True,
        )
        assert progress.completed is False
        assert progress.total is True

    def test_negative_completed_count_is_rejected(self) -> None:
        with pytest.raises(
            ValueError,
            match=exact_message("Completed progress must not be negative."),
        ):
            ArchiveProgress(
                phase=ArchiveProgressPhase.TRANSFORMING,
                completed=-1,
                total=1,
            )

    @pytest.mark.parametrize("total", [0, -1])
    def test_non_positive_total_is_rejected(self, total: int) -> None:
        with pytest.raises(
            ValueError,
            match=exact_message(
                "Total progress must be a positive integer."
            ),
        ):
            ArchiveProgress(
                phase=ArchiveProgressPhase.TRANSFORMING,
                completed=0,
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

    def test_transformation_requires_determinate_counts(self) -> None:
        with pytest.raises(
            ValueError,
            match=exact_message(
                "Transformation progress requires member counts."
            ),
        ):
            ArchiveProgress(phase=ArchiveProgressPhase.TRANSFORMING)

    def test_publication_rejects_determinate_counts(self) -> None:
        with pytest.raises(
            ValueError,
            match=exact_message(
                "Publication progress must not contain member counts."
            ),
        ):
            ArchiveProgress(
                phase=ArchiveProgressPhase.PUBLISHING,
                completed=0,
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

    @pytest.mark.parametrize(
        "progress",
        [
            ArchiveProgress(phase=ArchiveProgressPhase.VERIFYING),
            ArchiveProgress(
                phase=ArchiveProgressPhase.TRANSFORMING,
                completed=0,
                total=1,
            ),
        ],
    )
    def test_member_name_requires_completed_work(
        self,
        progress: ArchiveProgress,
    ) -> None:
        with pytest.raises(
            ValueError,
            match=exact_message(
                "A progress member name requires completed work."
            ),
        ):
            ArchiveProgress(
                phase=progress.phase,
                completed=progress.completed,
                total=progress.total,
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


class TestProgressFormatting:
    @pytest.mark.parametrize(
        ("progress", "expected"),
        [
            (
                ArchiveProgress(
                    phase=ArchiveProgressPhase.TRANSFORMING,
                    completed=0,
                    total=200,
                ),
                "Processing [────────────────────] 0 % (0/200)",
            ),
            (
                ArchiveProgress(
                    phase=ArchiveProgressPhase.TRANSFORMING,
                    completed=1,
                    total=3,
                    member_name="001.png",
                ),
                "Processing [██████──────────────] 33 % (1/3)",
            ),
            (
                ArchiveProgress(
                    phase=ArchiveProgressPhase.TRANSFORMING,
                    completed=142,
                    total=200,
                    member_name="142.png",
                ),
                "Processing [██████████████──────] 71 % (142/200)",
            ),
            (
                ArchiveProgress(
                    phase=ArchiveProgressPhase.VERIFYING,
                    completed=80,
                    total=200,
                    member_name="080.png",
                ),
                "Verifying  [████████────────────] 40 % (80/200)",
            ),
            (
                ArchiveProgress(
                    phase=ArchiveProgressPhase.TRANSFORMING,
                    completed=199,
                    total=200,
                    member_name="199.png",
                ),
                "Processing [███████████████████─] 99 % (199/200)",
            ),
            (
                ArchiveProgress(
                    phase=ArchiveProgressPhase.TRANSFORMING,
                    completed=200,
                    total=200,
                    member_name="200.png",
                ),
                "Processing [████████████████████] 100 % (200/200)",
            ),
            (
                ArchiveProgress(phase=ArchiveProgressPhase.VERIFYING),
                "Verifying output archive...",
            ),
            (
                ArchiveProgress(phase=ArchiveProgressPhase.PUBLISHING),
                None,
            ),
        ],
    )
    def test_progress_is_formatted(
        self,
        progress: ArchiveProgress,
        expected: str | None,
    ) -> None:
        assert progress_module._format_progress(progress) == expected

    def test_invalid_bar_width_is_rejected(self) -> None:
        progress = ArchiveProgress(
            phase=ArchiveProgressPhase.TRANSFORMING,
            completed=0,
            total=1,
        )
        with pytest.raises(
            ValueError,
            match=exact_message(
                "Progress bar width must be a positive integer."
            ),
        ):
            progress_module._format_progress(progress, bar_width=0)


class TestTerminalProgressRenderer:
    def test_progress_replaces_the_active_line(self) -> None:
        stream = StringIO()
        renderer = TerminalProgressRenderer(stream)
        renderer.report(
            ArchiveProgress(
                phase=ArchiveProgressPhase.TRANSFORMING,
                completed=0,
                total=200,
            )
        )
        renderer.report(
            ArchiveProgress(
                phase=ArchiveProgressPhase.VERIFYING,
                completed=80,
                total=200,
                member_name="080.png",
            )
        )
        assert stream.getvalue() == (
            "\rProcessing [────────────────────] 0 % (0/200)"
            "\rVerifying  [████████────────────] 40 % (80/200)"
        )

    def test_shorter_status_erases_stale_characters(self) -> None:
        stream = StringIO()
        renderer = TerminalProgressRenderer(stream)
        processing = "Processing [████████████████████] 100 % (200/200)"
        status = "Verifying output archive..."
        renderer.report(
            ArchiveProgress(
                phase=ArchiveProgressPhase.TRANSFORMING,
                completed=200,
                total=200,
                member_name="200.png",
            )
        )
        renderer.report(
            ArchiveProgress(phase=ArchiveProgressPhase.VERIFYING)
        )
        assert stream.getvalue() == (
            "\r" + processing
            + "\r" + status
            + " " * (len(processing) - len(status))
        )

    def test_publication_clears_without_visible_status(self) -> None:
        stream = StringIO()
        renderer = TerminalProgressRenderer(stream)
        line = "Verifying output archive..."
        renderer.report(
            ArchiveProgress(phase=ArchiveProgressPhase.VERIFYING)
        )
        renderer.report(
            ArchiveProgress(phase=ArchiveProgressPhase.PUBLISHING)
        )
        assert stream.getvalue() == (
            "\r" + line + "\r" + " " * len(line) + "\r"
        )
        assert "Publishing" not in stream.getvalue()

    def test_repeated_clear_is_safe(self) -> None:
        stream = StringIO()
        renderer = TerminalProgressRenderer(stream)
        renderer.clear()
        renderer.report(
            ArchiveProgress(phase=ArchiveProgressPhase.PUBLISHING)
        )
        assert stream.getvalue() == ""

    def test_render_and_clear_flush_the_stream(self) -> None:
        stream = Mock()
        renderer = TerminalProgressRenderer(stream)
        renderer.report(
            ArchiveProgress(
                phase=ArchiveProgressPhase.TRANSFORMING,
                completed=0,
                total=1,
            )
        )
        renderer.clear()
        assert stream.flush.call_count == 2
