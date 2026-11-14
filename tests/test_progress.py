# SPDX-License-Identifier: GPL-3.0-or-later

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
from tests.helpers import exact_message


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


    @pytest.mark.parametrize("phase", [
        ArchiveProgressPhase.TRANSFORMING, ArchiveProgressPhase.VERIFYING,
    ])
    @pytest.mark.parametrize(("completed", "total"), [
        (False, 1), (True, 1), (0, False), (0, True),
        (0.0, 1), (0, 1.0), ("0", 1), (0, "1"),
        (object(), 1), (0, object()),
    ])
    def test_invalid_count_types_are_rejected_in_determinate_phases(
        self, phase: ArchiveProgressPhase, completed: object, total: object,
    ) -> None:
        with pytest.raises(TypeError, match=exact_message("Progress counts must be integers.")):
            ArchiveProgress(
                phase, completed, total,  # type: ignore[arg-type]
            )

    @pytest.mark.parametrize("phase", list(ArchiveProgressPhase))
    @pytest.mark.parametrize(("completed", "total"), [(None, 1), (0, None)])
    def test_partial_counts_are_rejected_in_every_phase(
        self, phase: ArchiveProgressPhase, completed: int | None, total: int | None,
    ) -> None:
        with pytest.raises(ValueError, match=exact_message(
            "Completed and total progress counts must be provided together."
        )):
            ArchiveProgress(phase, completed, total)

    @pytest.mark.parametrize("phase", [
        ArchiveProgressPhase.TRANSFORMING, ArchiveProgressPhase.VERIFYING,
    ])
    @pytest.mark.parametrize(("completed", "total", "message"), [
        (-1, 1, "Completed progress must not be negative."),
        (0, 0, "Total progress must be a positive integer."),
        (0, -1, "Total progress must be a positive integer."),
        (2, 1, "Completed progress must not exceed total progress."),
    ])
    def test_invalid_count_ranges_are_rejected_in_determinate_phases(
        self, phase: ArchiveProgressPhase, completed: int, total: int, message: str,
    ) -> None:
        with pytest.raises(ValueError, match=exact_message(message)):
            ArchiveProgress(phase, completed, total)

    @pytest.mark.parametrize("phase", [
        ArchiveProgressPhase.TRANSFORMING, ArchiveProgressPhase.VERIFYING,
    ])
    @pytest.mark.parametrize(("completed", "total"), [
        (0, 1), (1, 1), (1, 2), (2, 2), (10**100 - 1, 10**100),
    ])
    def test_valid_count_boundaries_are_retained(
        self, phase: ArchiveProgressPhase, completed: int, total: int,
    ) -> None:
        progress = ArchiveProgress(phase, completed, total)
        assert progress.completed == completed
        assert progress.total == total

    @pytest.mark.parametrize("phase", ["transforming", None, False, 1, object()])
    def test_other_invalid_phase_types_are_rejected(self, phase: object) -> None:
        with pytest.raises(TypeError, match=exact_message(
            "Archive progress phase must be an ArchiveProgressPhase, not "
            f"{type(phase).__name__}."
        )):
            ArchiveProgress(phase=phase)  # type: ignore[arg-type]

    @pytest.mark.parametrize("member_name", [1, False, 1.0, object()])
    def test_other_invalid_member_name_types_are_rejected(
        self, member_name: object,
    ) -> None:
        with pytest.raises(TypeError, match=exact_message(
            "Progress member name must be a string."
        )):
            ArchiveProgress(
                ArchiveProgressPhase.VERIFYING, 1, 1,
                member_name=member_name,  # type: ignore[arg-type]
            )

    def test_publication_rejects_member_name_without_completed_work(self) -> None:
        with pytest.raises(ValueError, match=exact_message(
            "A progress member name requires completed work."
        )):
            ArchiveProgress(ArchiveProgressPhase.PUBLISHING, member_name="001.png")


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
        # Directly test private helpers to verify validation
        # and arithmetic boundaries without expanding the public API.
        assert progress_module._format_progress(  # pyright: ignore[reportPrivateUsage]
            progress) == expected

    @pytest.mark.parametrize("progress", [
        ArchiveProgress(ArchiveProgressPhase.TRANSFORMING, 0, 1),
        ArchiveProgress(ArchiveProgressPhase.VERIFYING, 0, 1),
        ArchiveProgress(ArchiveProgressPhase.VERIFYING),
        ArchiveProgress(ArchiveProgressPhase.PUBLISHING),
    ])
    @pytest.mark.parametrize("bar_width", [False, True, 1.0, "1", None, object()])
    def test_non_integer_bar_width_is_rejected_in_every_event_shape(
        self, progress: ArchiveProgress, bar_width: object,
    ) -> None:
        with pytest.raises(TypeError, match=exact_message(
            "Progress bar width must be an integer."
        )):
            # Directly test private helpers to verify validation
            # and arithmetic boundaries without expanding the public API
            progress_module._format_progress(  # pyright: ignore[reportPrivateUsage]

                progress, bar_width=bar_width,  # type: ignore[arg-type]
            )

    @pytest.mark.parametrize("progress", [
        ArchiveProgress(ArchiveProgressPhase.TRANSFORMING, 0, 1),
        ArchiveProgress(ArchiveProgressPhase.VERIFYING, 0, 1),
        ArchiveProgress(ArchiveProgressPhase.VERIFYING),
        ArchiveProgress(ArchiveProgressPhase.PUBLISHING),
    ])
    @pytest.mark.parametrize("bar_width", [0, -1])
    def test_non_positive_bar_width_is_rejected_in_every_event_shape(
        self, progress: ArchiveProgress, bar_width: int,
    ) -> None:
        with pytest.raises(ValueError, match=exact_message(
            "Progress bar width must be a positive integer."
        )):
            # Directly test private helpers to verify validation
            # and arithmetic boundaries without expanding the public API
            progress_module._format_progress(  # pyright: ignore[reportPrivateUsage]
            progress, bar_width=bar_width)

    @pytest.mark.parametrize(("phase", "expected"), [
        (ArchiveProgressPhase.TRANSFORMING, "Processing [█──] 33 % (1/3)"),
        (ArchiveProgressPhase.VERIFYING, "Verifying  [█──] 33 % (1/3)"),
    ])
    def test_custom_width_preserves_exact_rendering(
        self, phase: ArchiveProgressPhase, expected: str,
    ) -> None:
        # Directly test private helpers to verify validation
        # and arithmetic boundaries without expanding the public API
        assert progress_module._format_progress(  # pyright: ignore[reportPrivateUsage]

            ArchiveProgress(phase, 1, 3), bar_width=3,
        ) == expected

    @pytest.mark.parametrize(("progress", "expected"), [
        (ArchiveProgress(ArchiveProgressPhase.VERIFYING), "Verifying output archive..."),
        (ArchiveProgress(ArchiveProgressPhase.PUBLISHING), None),
    ])
    def test_minimum_valid_width_preserves_indeterminate_behavior(
        self, progress: ArchiveProgress, expected: str | None,
    ) -> None:
        # Directly test private helpers to verify validation
        # and arithmetic boundaries without expanding the public API
        assert progress_module._format_progress(  # pyright: ignore[reportPrivateUsage]
            progress, bar_width=1) == expected


    @pytest.mark.parametrize(
        ("completed", "total"),
        [(None, None), (1, None), (None, 1)],
    )
    def test_determinate_rendering_rejects_corrupted_missing_counts(
        self,
        completed: int | None,
        total: int | None,
    ) -> None:
        # Bypass constructor validation to exercise the defensive renderer guard.
        progress = object.__new__(ArchiveProgress)
        object.__setattr__(progress, "phase", ArchiveProgressPhase.TRANSFORMING)
        object.__setattr__(progress, "completed", completed)
        object.__setattr__(progress, "total", total)
        object.__setattr__(progress, "member_name", None)

        with pytest.raises(
            ValueError,
            match=exact_message(
                "Determinate progress requires completed and total counts."
            ),
        ):
            # Directly test private helpers to verify validation
            # and arithmetic boundaries without expanding the public API
            progress_module._format_progress(  # pyright: ignore[reportPrivateUsage]
            progress)


class TestCalculateProgressGeometry:
    @pytest.mark.parametrize(("completed", "total", "bar_width", "expected"), [
        (0, 1, 1, (0, 0)), (1, 1, 1, (100, 1)),
        (1, 3, 20, (33, 6)), (2, 3, 20, (66, 13)),
        (10**100 - 1, 10**100, 20, (99, 19)),
        (10**100, 10**100, 20, (100, 20)),
    ])
    def test_validated_inputs_use_exact_integer_arithmetic(
        self, completed: int, total: int, bar_width: int, expected: tuple[int, int],
    ) -> None:
        # Directly test private helpers to verify validation
        # and arithmetic boundaries without expanding the public API
        assert progress_module._calculate_progress_geometry(  # pyright: ignore[reportPrivateUsage]

            completed, total, bar_width,
        ) == expected


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
