# SPDX-License-Identifier: GPL-3.0-or-later

import argparse
import re
import sys
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import Mock
from zipfile import ZipFile

import pytest
from PIL import Image

import cbzfit.cli as cli_module
from cbzfit import __version__
from cbzfit.archive import (
    InvalidArchiveError,
)
from cbzfit.cli import (
    MEBIBYTE,
    build_parser,
    calculate_size_change_percentage,
    format_count,
    format_elapsed_time,
    format_file_size,
    format_size_change,
    main,
    positive_integer,
    print_processing_summary,
)
from cbzfit.decode import (
    UnsupportedImageContentError,
    UnsupportedImageFormatError,
)
from cbzfit.image import (
    InvalidScreenDimensionError,
    InvalidScreenOrientationError,
)
from cbzfit.process import (
    ArchiveProcessingResult,
    ArchiveTransformationOptions,
    ArchiveTransformationResult,
    DestinationConflictMode,
    ImageProcessingOptions,
    OutputVerificationMode,
    SourceDestinationConflictError,
)
from cbzfit.progress import (
    ArchiveProgress,
    ArchiveProgressPhase,
)


def exact_message(message: str) -> str:
    """Return a regular expression that matches a complete error message."""
    return rf"^{re.escape(message)}$"


def required_arguments() -> list[str]:
    """Return the minimum valid archive-processing arguments."""
    return [
        "source.cbz",
        "destination.cbz",
        "--screen-width",
        "1404",
        "--screen-height",
        "1872",
    ]


def create_valid_cli_source_archive(archive_path: Path) -> None:
    """Create a minimal valid CBZ archive for CLI integration tests."""
    image = Image.new(
        mode="RGB",
        size=(10, 20),
        color="white",
    )
    image_stream = BytesIO()
    try:
        image.save(image_stream, format="PNG")
    finally:
        image.close()

    with ZipFile(archive_path, mode="w") as archive:
        archive.writestr("001.png", image_stream.getvalue())


def prepare_cli_error_case(
    case: str,
    tmp_path: Path,
) -> tuple[list[str], str]:
    """Prepare one real CLI failure and return its arguments and message."""
    source_path = tmp_path / "source.cbz"
    destination_path = tmp_path / "destination.cbz"

    if case == "missing-source":
        expected_message = (
            f"Source archive file does not exist: {source_path}."
        )
    elif case == "invalid-zip":
        source_path.write_bytes(b"not a ZIP archive")
        expected_message = (
            f"File is not a valid ZIP archive: {source_path}."
        )
    elif case == "existing-destination":
        create_valid_cli_source_archive(source_path)
        destination_path.write_bytes(b"existing destination")
        expected_message = (
            f"Destination archive already exists: {destination_path}."
        )
    elif case == "invalid-destination-parent":
        create_valid_cli_source_archive(source_path)
        destination_path = tmp_path / "missing" / "destination.cbz"
        expected_message = (
            "Destination directory does not exist: "
            f"{destination_path.parent}."
        )
    elif case == "unsupported-image":
        with ZipFile(source_path, mode="w") as archive:
            archive.writestr("001.jpg", b"not an image")
        expected_message = (
            "Image data could not be decoded: '001.jpg'."
        )
    else:
        raise AssertionError(f"Unsupported CLI error test case: {case!r}.")

    arguments = [
        str(source_path),
        str(destination_path),
        "--screen-width",
        "1404",
        "--screen-height",
        "1872",
    ]

    return arguments, expected_message


class TestPositiveInteger:

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("1", 1),
            ("1404", 1404),
            ("+1872", 1872),
        ],
    )
    def test_positive_integer_is_returned(
        self,
        value: str,
        expected: int,
    ) -> None:
        assert positive_integer(value) == expected

    @pytest.mark.parametrize(
        "value",
        [
            "0",
            "-1",
            "3.5",
            "3.0",
            "abc",
            "",
        ],
    )
    def test_non_positive_or_non_integer_value_is_rejected(
        self,
        value: str,
    ) -> None:
        expected_message = (
            f"expected a positive integer, got {value!r}"
        )

        with pytest.raises(
            argparse.ArgumentTypeError,
            match=exact_message(expected_message),
        ):
            positive_integer(value)


class TestBuildParser:

    def test_parser_metadata(self) -> None:
        parser = build_parser()

        assert parser.prog == "cbzfit"
        assert parser.description == (
            "Resize manga CBZ archives to fit a target display."
        )

    def test_required_arguments_and_defaults_are_parsed(self) -> None:
        parser = build_parser()

        arguments = parser.parse_args(required_arguments())

        assert arguments.source == Path("source.cbz")
        assert arguments.destination == Path("destination.cbz")
        assert arguments.screen_width == 1404
        assert arguments.screen_height == 1872
        assert arguments.landscape_display is True
        assert arguments.upscale is False
        assert arguments.no_progress is False
        assert (
            arguments.verification_mode
            is OutputVerificationMode.STRUCTURE
        )
        assert arguments.conflict_mode is DestinationConflictMode.ERROR

    def test_paths_with_spaces_and_unicode_are_parsed(self) -> None:
        parser = build_parser()

        arguments = parser.parse_args(
            [
                "Manga/進撃の巨人 Volume 01.cbz",
                "Output/進撃の巨人 optimized.cbz",
                "--screen-width",
                "1404",
                "--screen-height",
                "1872",
            ]
        )

        assert arguments.source == Path(
            "Manga/進撃の巨人 Volume 01.cbz"
        )
        assert arguments.destination == Path(
            "Output/進撃の巨人 optimized.cbz"
        )

    def test_custom_boolean_and_mode_options_are_parsed(self) -> None:
        parser = build_parser()

        arguments = parser.parse_args(
            [
                *required_arguments(),
                "--no-landscape-display",
                "--upscale",
                "--verify",
                "crc",
                "--conflict",
                "replace",
                "--no-progress",
            ]
        )

        assert arguments.landscape_display is False
        assert arguments.upscale is True
        assert arguments.no_progress is True
        assert arguments.verification_mode is OutputVerificationMode.CRC
        assert arguments.conflict_mode is DestinationConflictMode.REPLACE

    def test_landscape_display_can_be_enabled_explicitly(self) -> None:
        parser = build_parser()

        arguments = parser.parse_args(
            [
                *required_arguments(),
                "--landscape-display",
            ]
        )

        assert arguments.landscape_display is True

    @pytest.mark.parametrize(
        "verification_mode",
        list(OutputVerificationMode),
    )
    def test_all_verification_modes_are_parsed(
        self,
        verification_mode: OutputVerificationMode,
    ) -> None:
        parser = build_parser()

        arguments = parser.parse_args(
            [
                *required_arguments(),
                "--verify",
                verification_mode.value,
            ]
        )

        assert arguments.verification_mode is verification_mode

    @pytest.mark.parametrize(
        "conflict_mode",
        list(DestinationConflictMode),
    )
    def test_all_destination_conflict_modes_are_parsed(
        self,
        conflict_mode: DestinationConflictMode,
    ) -> None:
        parser = build_parser()

        arguments = parser.parse_args(
            [
                *required_arguments(),
                "--conflict",
                conflict_mode.value,
            ]
        )

        assert arguments.conflict_mode is conflict_mode

    @pytest.mark.parametrize(
        "arguments",
        [
            [],
            ["source.cbz"],
            ["source.cbz", "destination.cbz"],
            [
                "source.cbz",
                "destination.cbz",
                "--screen-width",
                "1404",
            ],
            [
                "source.cbz",
                "destination.cbz",
                "--screen-height",
                "1872",
            ],
        ],
    )
    def test_missing_required_arguments_are_rejected(
        self,
        arguments: list[str],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        parser = build_parser()

        with pytest.raises(SystemExit) as exception_info:
            parser.parse_args(arguments)

        assert exception_info.value.code == 2
        assert "the following arguments are required" in capsys.readouterr().err

    @pytest.mark.parametrize(
        ("option", "value"),
        [
            ("--screen-width", "0"),
            ("--screen-width", "3.5"),
            ("--screen-height", "-1"),
            ("--screen-height", "abc"),
        ],
    )
    def test_invalid_screen_dimension_is_rejected(
        self,
        option: str,
        value: str,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        parser = build_parser()
        arguments = required_arguments()
        option_index = arguments.index(option)
        arguments[option_index + 1] = value

        with pytest.raises(SystemExit) as exception_info:
            parser.parse_args(arguments)

        assert exception_info.value.code == 2
        assert (
            f"expected a positive integer, got {value!r}"
            in capsys.readouterr().err
        )

    def test_unsupported_verification_mode_is_rejected(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        parser = build_parser()
        unsupported_mode = "__unsupported_verification_mode__"

        assert unsupported_mode not in {
            mode.value
            for mode in OutputVerificationMode
        }

        with pytest.raises(SystemExit) as exception_info:
            parser.parse_args(
                [
                    *required_arguments(),
                    "--verify",
                    unsupported_mode,
                ]
            )

        assert exception_info.value.code == 2

        error_output = capsys.readouterr().err

        assert "--verify" in error_output
        assert unsupported_mode in error_output
        assert "invalid OutputVerificationMode value" in error_output

    def test_unsupported_conflict_mode_is_rejected(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        parser = build_parser()
        unsupported_mode = "__unsupported_conflict_mode__"

        assert unsupported_mode not in {
            mode.value
            for mode in DestinationConflictMode
        }

        with pytest.raises(SystemExit) as exception_info:
            parser.parse_args(
                [
                    *required_arguments(),
                    "--conflict",
                    unsupported_mode,
                ]
            )

        assert exception_info.value.code == 2

        error_output = capsys.readouterr().err

        assert "--conflict" in error_output
        assert unsupported_mode in error_output
        assert "invalid DestinationConflictMode value" in error_output

    def test_help_is_displayed(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        parser = build_parser()

        with pytest.raises(SystemExit) as exception_info:
            parser.parse_args(["--help"])

        assert exception_info.value.code == 0
        output = capsys.readouterr().out
        assert "usage: cbzfit" in output
        assert "SOURCE" in output
        assert "DESTINATION" in output
        assert "--screen-width PIXELS" in output
        assert "--screen-height PIXELS" in output
        assert "--landscape-display" in output
        assert "--no-landscape-display" in output
        assert "--upscale" in output
        assert "--verify MODE" in output
        assert "--conflict MODE" in output
        assert "--no-progress" in output
        assert "none, structure, or crc" in output
        assert "error or replace" in output
        assert "(default: enabled)" in output
        assert "(default: disabled)" in output
        assert "(default: structure)" in output
        assert "(default: error)" in output

    def test_version_is_displayed_without_processing_arguments(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        parser = build_parser()

        with pytest.raises(SystemExit) as exception_info:
            parser.parse_args(["--version"])

        assert exception_info.value.code == 0
        assert capsys.readouterr().out == f"cbzfit {__version__}\n"

    def test_unknown_option_is_rejected(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        parser = build_parser()
        unknown_option = "--unsupported-option"

        with pytest.raises(SystemExit) as exception_info:
            parser.parse_args(
                [
                    *required_arguments(),
                    unknown_option,
                ]
            )

        assert exception_info.value.code == 2

        error_output = capsys.readouterr().err

        assert "unrecognized arguments" in error_output
        assert unknown_option in error_output


class TestFormatCount:
    @pytest.mark.parametrize(
        ("singular", "count", "expected"),
        [
            ("Image", 0, "Images"),
            ("Image", 1, "Image"),
            ("Image", 2, "Images"),
            ("Other member", 0, "Other members"),
            ("Other member", 1, "Other member"),
            ("Other member", 2, "Other members"),
        ],
    )
    def test_count_is_formatted_with_the_expected_noun(
        self,
        singular: str,
        count: int,
        expected: str,
    ) -> None:
        assert format_count(singular, count) == expected

    @pytest.mark.parametrize("count", [-1, -2])
    def test_negative_count_is_rejected(
        self,
        count: int,
    ) -> None:
        with pytest.raises(
            ValueError,
            match=exact_message("Count must not be negative."),
        ):
            format_count("Image", count)


class TestFileSizeFormatting:
    @pytest.mark.parametrize(
        ("size_bytes", "expected"),
        [
            (0, "0.0 MiB"),
            (MEBIBYTE, "1.0 MiB"),
            (180 * MEBIBYTE, "180.0 MiB"),
            (int(95.8 * MEBIBYTE), "95.8 MiB"),
        ],
    )
    def test_byte_count_is_formatted_as_mebibytes(
        self,
        size_bytes: int,
        expected: str,
    ) -> None:
        assert format_file_size(size_bytes) == expected

    def test_negative_file_size_is_rejected(self) -> None:
        with pytest.raises(
            ValueError,
            match=exact_message("File size must not be negative."),
        ):
            format_file_size(-1)


class TestSizeChangeFormatting:
    @pytest.mark.parametrize(
        ("source_size", "destination_size", "expected"),
        [
            (180 * MEBIBYTE, int(95.8 * MEBIBYTE), 46.8),
            (int(95.8 * MEBIBYTE), int(100.2 * MEBIBYTE), 4.6),
            (MEBIBYTE, MEBIBYTE, 0.0),
            (0, MEBIBYTE, None),
        ],
    )
    def test_percentage_change_is_calculated(
        self,
        source_size: int,
        destination_size: int,
        expected: float | None,
    ) -> None:
        result = calculate_size_change_percentage(
            source_size,
            destination_size,
        )
        if expected is None:
            assert result is None
        else:
            assert result == pytest.approx(expected, abs=0.05)

    @pytest.mark.parametrize(
        ("source_size", "destination_size"),
        [(-1, 0), (0, -1)],
    )
    def test_negative_size_is_rejected(
        self,
        source_size: int,
        destination_size: int,
    ) -> None:
        with pytest.raises(
            ValueError,
            match=exact_message("File sizes must not be negative."),
        ):
            calculate_size_change_percentage(source_size, destination_size)

    @pytest.mark.parametrize(
        ("source_size", "destination_size", "expected"),
        [
            (180 * MEBIBYTE, int(95.8 * MEBIBYTE), "46.8 % decrease"),
            (int(95.8 * MEBIBYTE), int(100.2 * MEBIBYTE), "4.6 % increase"),
            (int(95.8 * MEBIBYTE), int(95.8 * MEBIBYTE), "no change"),
            (0, 1, "percentage unavailable"),
            (0, 0, "no change"),
        ],
    )
    def test_size_change_is_described(
        self,
        source_size: int,
        destination_size: int,
        expected: str,
    ) -> None:
        assert format_size_change(source_size, destination_size) == expected


class TestElapsedTimeFormatting:
    @pytest.mark.parametrize(
        ("elapsed_seconds", "expected"),
        [
            (10.54, "10.5 s"),
            (0.0, "0.0 s"),
            (-0.25, "0.0 s"),
        ],
    )
    def test_elapsed_time_is_formatted_and_clamped(
        self,
        elapsed_seconds: float,
        expected: str,
    ) -> None:
        assert format_elapsed_time(elapsed_seconds) == expected

    @pytest.mark.parametrize(
        ("elapsed_seconds", "expected"),
        [
            (10.44, "10.4 s"),
            (10.46, "10.5 s"),
        ],
    )
    def test_elapsed_time_is_rounded_to_one_decimal_place(
        self,
        elapsed_seconds: float,
        expected: str,
    ) -> None:
        assert format_elapsed_time(elapsed_seconds) == expected


class TestPrintProcessingSummary:
    @pytest.mark.parametrize(
        ("result", "expected_summary"),
        [
            (
                ArchiveProcessingResult(
                    transformation_result=ArchiveTransformationResult(
                        total_file_members=300,
                        image_members=200,
                        transformed_images=100,
                        unchanged_images=100,
                        copied_other_members=100,
                        input_uncompressed_size=1_000,
                        output_uncompressed_size=600,
                    ),
                    source_file_size=180 * MEBIBYTE,
                    destination_file_size=int(95.8 * MEBIBYTE),
                    elapsed_seconds=10.54,
                ),
                (
                    "Output: optimized.cbz completed in 10.5 s\n"
                    "└─Images: 200 total, 100 transformed, "
                    "100 unchanged. Other members copied: 100\n"
                    "└─Size: 180.0 MiB -> 95.8 MiB, 46.8 % decrease\n"
                ),
            ),
            (
                ArchiveProcessingResult(
                    transformation_result=ArchiveTransformationResult(
                        total_file_members=2,
                        image_members=1,
                        transformed_images=1,
                        unchanged_images=0,
                        copied_other_members=1,
                        input_uncompressed_size=100,
                        output_uncompressed_size=80,
                    ),
                    source_file_size=int(95.8 * MEBIBYTE),
                    destination_file_size=int(100.2 * MEBIBYTE),
                    elapsed_seconds=-1.0,
                ),
                (
                    "Output: optimized.cbz completed in 0.0 s\n"
                    "└─Image: 1 total, 1 transformed, "
                    "0 unchanged. Other member copied: 1\n"
                    "└─Size: 95.8 MiB -> 100.2 MiB, 4.6 % increase\n"
                ),
            ),
            (
                ArchiveProcessingResult(
                    transformation_result=ArchiveTransformationResult(
                        total_file_members=2,
                        image_members=2,
                        transformed_images=0,
                        unchanged_images=2,
                        copied_other_members=0,
                        input_uncompressed_size=100,
                        output_uncompressed_size=100,
                    ),
                    source_file_size=int(95.8 * MEBIBYTE),
                    destination_file_size=int(95.8 * MEBIBYTE),
                    elapsed_seconds=1.0,
                ),
                (
                    "Output: optimized.cbz completed in 1.0 s\n"
                    "└─Images: 2 total, 0 transformed, "
                    "2 unchanged. Other members copied: 0\n"
                    "└─Size: 95.8 MiB -> 95.8 MiB, no change\n"
                ),
            ),
            (
                ArchiveProcessingResult(
                    transformation_result=ArchiveTransformationResult(
                        total_file_members=1,
                        image_members=1,
                        transformed_images=0,
                        unchanged_images=1,
                        copied_other_members=0,
                        input_uncompressed_size=0,
                        output_uncompressed_size=1,
                    ),
                    source_file_size=0,
                    destination_file_size=MEBIBYTE,
                    elapsed_seconds=0.04,
                ),
                (
                    "Output: optimized.cbz completed in 0.0 s\n"
                    "└─Image: 1 total, 0 transformed, "
                    "1 unchanged. Other members copied: 0\n"
                    "└─Size: 0.0 MiB -> 1.0 MiB, percentage unavailable\n"
                ),
            ),
        ],
    )
    def test_exact_three_line_summary_is_printed(
        self,
        result: ArchiveProcessingResult,
        expected_summary: str,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        print_processing_summary(
            Path("optimized.cbz"),
            result,
        )
        captured = capsys.readouterr()
        assert captured.out == expected_summary
        assert captured.err == ""

    def test_destination_path_is_preserved_in_summary(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        destination = Path("Output") / "Manga Volume 01.cbz"
        result = ArchiveProcessingResult(
            transformation_result=ArchiveTransformationResult(
                total_file_members=1,
                image_members=1,
                transformed_images=0,
                unchanged_images=1,
                copied_other_members=0,
                input_uncompressed_size=100,
                output_uncompressed_size=100,
            ),
            source_file_size=MEBIBYTE,
            destination_file_size=MEBIBYTE,
            elapsed_seconds=0.5,
        )

        print_processing_summary(destination, result)

        assert capsys.readouterr().out.startswith(
            f"Output: {destination} completed in 0.5 s\n"
        )


class TestMain:
    @pytest.mark.parametrize(
        (
            "additional_arguments",
            "expected_image_options",
            "expected_verification_mode",
            "expected_conflict_mode",
        ),
        [
            (
                [],
                ImageProcessingOptions(
                    portrait_screen_size=(1404, 1872),
                ),
                OutputVerificationMode.STRUCTURE,
                DestinationConflictMode.ERROR,
            ),
            (
                [
                    "--no-landscape-display",
                    "--upscale",
                    "--verify",
                    "crc",
                    "--conflict",
                    "replace",
                ],
                ImageProcessingOptions(
                    portrait_screen_size=(1404, 1872),
                    use_landscape_display=False,
                    allow_upscale=True,
                ),
                OutputVerificationMode.CRC,
                DestinationConflictMode.REPLACE,
            ),
        ],
    )
    def test_processing_options_and_result_are_forwarded(
        self,
        monkeypatch: pytest.MonkeyPatch,
        additional_arguments: list[str],
        expected_image_options: ImageProcessingOptions,
        expected_verification_mode: OutputVerificationMode,
        expected_conflict_mode: DestinationConflictMode,
    ) -> None:
        source_path = Path("source.cbz")
        destination_path = Path("destination.cbz")
        transformation_result = ArchiveTransformationResult(
            total_file_members=3,
            image_members=2,
            transformed_images=1,
            unchanged_images=1,
            copied_other_members=1,
            input_uncompressed_size=1_000,
            output_uncompressed_size=600,
        )
        processing_result = ArchiveProcessingResult(
            transformation_result=transformation_result,
            source_file_size=1_200,
            destination_file_size=800,
            elapsed_seconds=0.5,
        )
        process_archive = Mock(return_value=processing_result)
        print_summary = Mock()
        monkeypatch.setattr(
            cli_module,
            "process_archive_file",
            process_archive,
        )
        monkeypatch.setattr(
            cli_module,
            "print_processing_summary",
            print_summary,
        )
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "cbzfit",
                *required_arguments(),
                *additional_arguments,
            ],
        )

        result = main()

        assert result == 0
        process_archive.assert_called_once()
        assert process_archive.call_args.args == (
            source_path,
            destination_path,
        )
        assert process_archive.call_args.kwargs == {
            "options": ArchiveTransformationOptions(
                image_options=expected_image_options,
            ),
            "verification_mode": expected_verification_mode,
            "conflict_mode": expected_conflict_mode,
            "progress_callback": None,
        }
        print_summary.assert_called_once_with(
            destination_path,
            processing_result,
        )

    @pytest.mark.parametrize(
        "error",
        [
            InvalidArchiveError("Archive validation failed"),
            InvalidScreenDimensionError("Screen dimensions are invalid"),
            InvalidScreenOrientationError("Screen orientation is invalid"),
            SourceDestinationConflictError("Archive paths conflict"),
            UnsupportedImageContentError("Image content is unsupported"),
            UnsupportedImageFormatError("Image format is unsupported"),
            FileNotFoundError("Source archive is missing"),
            FileExistsError("Destination archive already exists"),
            IsADirectoryError("Archive path is a directory"),
            NotADirectoryError("Archive parent is not a directory"),
            PermissionError("Archive access was denied"),
            OSError("Archive filesystem operation failed"),
        ],
    )
    def test_expected_processing_error_is_reported_without_traceback(
        self,
        error: Exception,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        process_archive = Mock(side_effect=error)
        print_summary = Mock()
        monkeypatch.setattr(
            cli_module,
            "process_archive_file",
            process_archive,
        )
        monkeypatch.setattr(
            cli_module,
            "print_processing_summary",
            print_summary,
        )
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "cbzfit",
                *required_arguments(),
            ],
        )

        with pytest.raises(SystemExit) as exception_info:
            main()

        captured = capsys.readouterr()
        assert exception_info.value.code == 1
        assert captured.out == ""
        assert captured.err == f"cbzfit: error: {error}\n"
        assert "Traceback" not in captured.err
        process_archive.assert_called_once()
        print_summary.assert_not_called()

    @pytest.mark.parametrize(
        ("option", "value"),
        [
            ("--screen-width", "0"),
            ("--screen-height", "-1"),
        ],
    )
    def test_non_positive_cli_dimension_is_rejected_before_processing(
        self,
        option: str,
        value: str,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        process_archive = Mock()
        print_summary = Mock()
        arguments = required_arguments()
        arguments[arguments.index(option) + 1] = value
        monkeypatch.setattr(
            cli_module,
            "process_archive_file",
            process_archive,
        )
        monkeypatch.setattr(
            cli_module,
            "print_processing_summary",
            print_summary,
        )
        monkeypatch.setattr(
            sys,
            "argv",
            ["cbzfit", *arguments],
        )

        with pytest.raises(SystemExit) as exception_info:
            main()

        captured = capsys.readouterr()
        assert exception_info.value.code == 2
        assert captured.out == ""
        assert (
            f"expected a positive integer, got {value!r}"
            in captured.err
        )
        assert "Traceback" not in captured.err
        process_archive.assert_not_called()
        print_summary.assert_not_called()

    def test_landscape_screen_orientation_is_reported_before_processing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        process_archive = Mock()
        print_summary = Mock()
        monkeypatch.setattr(
            cli_module,
            "process_archive_file",
            process_archive,
        )
        monkeypatch.setattr(
            cli_module,
            "print_processing_summary",
            print_summary,
        )
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "cbzfit",
                "missing-source.cbz",
                "destination.cbz",
                "--screen-width",
                "1872",
                "--screen-height",
                "1404",
            ],
        )

        with pytest.raises(SystemExit) as exception_info:
            main()

        captured = capsys.readouterr()
        assert exception_info.value.code == 1
        assert captured.out == ""
        assert captured.err == (
            "cbzfit: error: Screen size must be provided in "
            "portrait orientation.\n"
        )
        assert "Traceback" not in captured.err
        process_archive.assert_not_called()
        print_summary.assert_not_called()

    @pytest.mark.parametrize(
        "error",
        [
            ValueError("Unexpected validation failure"),
            RuntimeError("Unexpected processing failure"),
        ],
    )
    def test_unexpected_processing_error_is_propagated(
        self,
        error: Exception,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        process_archive = Mock(side_effect=error)
        print_summary = Mock()
        monkeypatch.setattr(
            cli_module,
            "process_archive_file",
            process_archive,
        )
        monkeypatch.setattr(
            cli_module,
            "print_processing_summary",
            print_summary,
        )
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "cbzfit",
                *required_arguments(),
            ],
        )

        with pytest.raises(type(error)) as exception_info:
            main()

        captured = capsys.readouterr()

        assert exception_info.value is error
        assert captured.out == ""
        assert captured.err == ""
        process_archive.assert_called_once()
        print_summary.assert_not_called()

class TestCliErrorHandlingIntegration:
    @pytest.mark.parametrize(
        "case",
        [
            "missing-source",
            "invalid-zip",
            "existing-destination",
            "invalid-destination-parent",
            "unsupported-image",
        ],
    )
    def test_expected_application_error_is_handled_without_traceback(
        self,
        case: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        arguments, expected_message = prepare_cli_error_case(
            case,
            tmp_path,
        )
        monkeypatch.setattr(
            sys,
            "argv",
            ["cbzfit", *arguments],
        )

        with pytest.raises(SystemExit) as exception_info:
            main()

        captured = capsys.readouterr()
        assert exception_info.value.code == 1
        assert captured.out == ""
        assert captured.err == f"cbzfit: error: {expected_message}\n"
        assert "Traceback" not in captured.err
        assert list(tmp_path.rglob(".*.tmp")) == []

    def test_equivalent_paths_are_reported_without_summary(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        archive_path = tmp_path / "source.cbz"
        archive_path.write_bytes(b"source")
        print_summary = Mock()
        monkeypatch.setattr(
            cli_module,
            "print_processing_summary",
            print_summary,
        )
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "cbzfit",
                str(archive_path),
                str(tmp_path / "." / "source.cbz"),
                "--screen-width",
                "1404",
                "--screen-height",
                "1872",
            ],
        )

        with pytest.raises(SystemExit) as exception_info:
            main()

        captured = capsys.readouterr()
        assert exception_info.value.code == 1
        assert captured.out == ""
        assert captured.err == (
            "cbzfit: error: Source and destination archive paths must "
            "be different unless destination replacement is enabled.\n"
        )
        assert "Traceback" not in captured.err
        print_summary.assert_not_called()

class TestCliProcessingIntegration:
    def test_archive_is_processed_end_to_end(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        source_path = tmp_path / "source.cbz"
        destination_path = tmp_path / "destination.cbz"
        source_image = Image.new(
            mode="RGB",
            size=(20, 40),
            color="white",
        )
        image_stream = BytesIO()
        try:
            source_image.save(image_stream, format="PNG")
        finally:
            source_image.close()
        source_image_data = image_stream.getvalue()

        with ZipFile(source_path, mode="w") as source_archive:
            source_archive.writestr("001.png", source_image_data)
            source_archive.writestr("ComicInfo.xml", b"<ComicInfo />")

        source_archive_data = source_path.read_bytes()
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "cbzfit",
                str(source_path),
                str(destination_path),
                "--screen-width",
                "10",
                "--screen-height",
                "20",
                "--verify",
                "crc",
            ],
        )

        result = main()

        captured = capsys.readouterr()
        assert result == 0
        summary_lines = captured.out.splitlines()
        assert len(summary_lines) == 3
        assert re.fullmatch(
            rf"Output: {re.escape(str(destination_path))} completed in \d+\.\d+ s",
            summary_lines[0],
        )
        assert summary_lines[1] == (
            "└─Image: 1 total, 1 transformed, 0 unchanged. "
            "Other member copied: 1"
        )
        source_size = len(source_archive_data)
        destination_size = destination_path.stat().st_size
        assert summary_lines[2] == (
            f"└─Size: {format_file_size(source_size)} -> "
            f"{format_file_size(destination_size)}, "
            f"{format_size_change(source_size, destination_size)}"
        )
        assert captured.err == ""
        assert source_path.read_bytes() == source_archive_data
        assert destination_path.is_file()
        assert list(tmp_path.glob(f".{destination_path.name}.*.tmp")) == []
        with ZipFile(destination_path, mode="r") as destination_archive:
            assert destination_archive.namelist() == [
                "001.png",
                "ComicInfo.xml",
            ]
            assert destination_archive.testzip() is None
            assert destination_archive.read("ComicInfo.xml") == b"<ComicInfo />"
            with Image.open(
                BytesIO(destination_archive.read("001.png"))
            ) as output_image:
                output_image.load()
                assert output_image.format == "PNG"
                assert output_image.size == (10, 20)


class CapturedErrorStream(StringIO):
    """Capture stderr with a configurable interactive-terminal state."""

    def __init__(self, *, is_tty: bool) -> None:
        super().__init__()
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


class TestCliProgressIntegration:
    def test_interactive_terminal_forwards_renderer_and_preserves_stdout(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        processing_result = ArchiveProcessingResult(
            transformation_result=ArchiveTransformationResult(
                total_file_members=1,
                image_members=1,
                transformed_images=0,
                unchanged_images=1,
                copied_other_members=0,
                input_uncompressed_size=100,
                output_uncompressed_size=100,
            ),
            source_file_size=MEBIBYTE,
            destination_file_size=MEBIBYTE,
            elapsed_seconds=0.5,
        )
        terminal = CapturedErrorStream(is_tty=True)

        def process_with_progress(
            source: Path,
            destination: Path,
            **kwargs: object,
        ) -> ArchiveProcessingResult:
            callback = kwargs["progress_callback"]
            assert callable(callback)
            callback(
                ArchiveProgress(
                    phase=ArchiveProgressPhase.TRANSFORMING,
                    completed=0,
                    total=1,
                )
            )
            callback(
                ArchiveProgress(
                    phase=ArchiveProgressPhase.TRANSFORMING,
                    completed=1,
                    total=1,
                    member_name="001.png",
                )
            )
            callback(
                ArchiveProgress(phase=ArchiveProgressPhase.VERIFYING)
            )
            callback(
                ArchiveProgress(phase=ArchiveProgressPhase.PUBLISHING)
            )
            return processing_result

        monkeypatch.setattr(cli_module.sys, "stderr", terminal)
        monkeypatch.setattr(
            cli_module,
            "process_archive_file",
            process_with_progress,
        )
        monkeypatch.setattr(sys, "argv", ["cbzfit", *required_arguments()])

        assert main() == 0

        output = capsys.readouterr().out
        assert output.startswith(
            "Output: destination.cbz completed in 0.5 s\n"
        )
        progress_output = terminal.getvalue()
        assert (
            "Processing [────────────────────] 0 % (0/1)"
            in progress_output
        )
        assert (
            "Processing [████████████████████] 100 % (1/1)"
            in progress_output
        )
        assert "Verifying output archive..." in progress_output
        assert "Publishing" not in progress_output
        final_line = "Verifying output archive..."
        assert progress_output.endswith(
            "\r" + " " * len(final_line) + "\r"
        )
        assert "\n" not in progress_output

    def test_real_crc_progress_transitions_and_clears(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        source_path = tmp_path / "source.cbz"
        destination_path = tmp_path / "destination.cbz"
        create_valid_cli_source_archive(source_path)
        with ZipFile(source_path, mode="a") as archive:
            archive.writestr("ComicInfo.xml", b"<ComicInfo />")
        terminal = CapturedErrorStream(is_tty=True)
        monkeypatch.setattr(cli_module.sys, "stderr", terminal)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "cbzfit",
                str(source_path),
                str(destination_path),
                "--screen-width",
                "10",
                "--screen-height",
                "20",
                "--verify",
                "crc",
            ],
        )

        assert main() == 0

        summary_lines = capsys.readouterr().out.splitlines()
        assert len(summary_lines) == 3
        assert re.fullmatch(
            rf"Output: {re.escape(str(destination_path))} completed in \d+\.\d+ s",
            summary_lines[0],
        )
        assert summary_lines[1] == (
            "└─Image: 1 total, 0 transformed, 1 unchanged. "
            "Other member copied: 1"
        )
        assert summary_lines[2].startswith("└─Size: ")

        progress_output = terminal.getvalue()
        assert "Verifying output archive..." in progress_output
        assert "Verifying  [────────────────────] 0 % (0/2)" in progress_output
        assert "Verifying  [██████████──────────] 50 % (1/2)" in progress_output
        final_line = "Verifying  [████████████████████] 100 % (2/2)"
        assert final_line in progress_output
        assert progress_output.endswith(
            "\r" + " " * len(final_line) + "\r"
        )
        assert "\n" not in progress_output
        assert destination_path.is_file()

    def test_real_none_mode_renders_only_transformation_and_clears(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        source_path = tmp_path / "source.cbz"
        destination_path = tmp_path / "destination.cbz"
        create_valid_cli_source_archive(source_path)
        terminal = CapturedErrorStream(is_tty=True)
        monkeypatch.setattr(cli_module.sys, "stderr", terminal)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "cbzfit",
                str(source_path),
                str(destination_path),
                "--screen-width",
                "10",
                "--screen-height",
                "20",
                "--verify",
                "none",
            ],
        )

        assert main() == 0

        summary_lines = capsys.readouterr().out.splitlines()
        assert len(summary_lines) == 3
        assert re.fullmatch(
            rf"Output: {re.escape(str(destination_path))} completed in \d+\.\d+ s",
            summary_lines[0],
        )
        assert summary_lines[1] == (
            "└─Image: 1 total, 0 transformed, 1 unchanged. "
            "Other members copied: 0"
        )
        assert summary_lines[2].startswith("└─Size: ")

        progress_output = terminal.getvalue()
        assert "Processing [────────────────────] 0 % (0/1)" in progress_output
        final_line = "Processing [████████████████████] 100 % (1/1)"
        assert final_line in progress_output
        assert "Verifying" not in progress_output
        assert progress_output.endswith(
            "\r" + " " * len(final_line) + "\r"
        )
        assert "\n" not in progress_output
        assert destination_path.is_file()

    def test_real_structure_mode_renders_status_and_clears(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        source_path = tmp_path / "source.cbz"
        destination_path = tmp_path / "destination.cbz"
        create_valid_cli_source_archive(source_path)
        terminal = CapturedErrorStream(is_tty=True)
        monkeypatch.setattr(cli_module.sys, "stderr", terminal)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "cbzfit",
                str(source_path),
                str(destination_path),
                "--screen-width",
                "10",
                "--screen-height",
                "20",
                "--verify",
                "structure",
            ],
        )

        assert main() == 0

        summary_lines = capsys.readouterr().out.splitlines()
        assert len(summary_lines) == 3
        assert re.fullmatch(
            rf"Output: {re.escape(str(destination_path))} completed in \d+\.\d+ s",
            summary_lines[0],
        )
        assert summary_lines[1] == (
            "└─Image: 1 total, 0 transformed, 1 unchanged. "
            "Other members copied: 0"
        )
        assert summary_lines[2].startswith("└─Size: ")

        progress_output = terminal.getvalue()
        assert "Processing [████████████████████] 100 % (1/1)" in progress_output
        final_line = "Verifying output archive..."
        assert final_line in progress_output
        assert "Verifying  [" not in progress_output
        assert progress_output.endswith(
            "\r" + " " * len(final_line) + "\r"
        )
        assert "\n" not in progress_output
        assert destination_path.is_file()

    @pytest.mark.parametrize(
        ("is_tty", "no_progress"),
        [
            (False, False),
            (True, True),
        ],
    )
    def test_progress_is_suppressed_when_disabled_or_not_interactive(
        self,
        is_tty: bool,
        no_progress: bool,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        terminal = CapturedErrorStream(is_tty=is_tty)
        additional_arguments = ["--no-progress"] if no_progress else []
        process_archive = Mock(
            return_value=ArchiveProcessingResult(
                transformation_result=ArchiveTransformationResult(
                    total_file_members=1,
                    image_members=1,
                    transformed_images=0,
                    unchanged_images=1,
                    copied_other_members=0,
                    input_uncompressed_size=100,
                    output_uncompressed_size=100,
                ),
                source_file_size=MEBIBYTE,
                destination_file_size=MEBIBYTE,
                elapsed_seconds=0.5,
            )
        )
        monkeypatch.setattr(cli_module.sys, "stderr", terminal)
        monkeypatch.setattr(
            cli_module,
            "process_archive_file",
            process_archive,
        )
        monkeypatch.setattr(
            cli_module,
            "print_processing_summary",
            Mock(),
        )
        monkeypatch.setattr(
            sys,
            "argv",
            ["cbzfit", *required_arguments(), *additional_arguments],
        )

        assert main() == 0
        assert process_archive.call_args.kwargs["progress_callback"] is None
        assert terminal.getvalue() == ""

    def test_active_progress_is_cleared_before_expected_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        terminal = CapturedErrorStream(is_tty=True)
        error = InvalidArchiveError("Verification failed")

        def fail_with_progress(
            source: Path,
            destination: Path,
            **kwargs: object,
        ) -> ArchiveProcessingResult:
            callback = kwargs["progress_callback"]
            assert callable(callback)
            callback(
                ArchiveProgress(
                    phase=ArchiveProgressPhase.VERIFYING,
                    completed=1,
                    total=2,
                    member_name="001.png",
                )
            )
            raise error

        monkeypatch.setattr(cli_module.sys, "stderr", terminal)
        monkeypatch.setattr(
            cli_module,
            "process_archive_file",
            fail_with_progress,
        )
        monkeypatch.setattr(sys, "argv", ["cbzfit", *required_arguments()])

        with pytest.raises(SystemExit) as exception_info:
            main()

        assert exception_info.value.code == 1
        output = terminal.getvalue()
        assert (
            "Verifying  [██████████──────────] 50 % (1/2)"
            in output
        )
        assert output.endswith("\rcbzfit: error: Verification failed\n")

    def test_active_progress_is_cleared_before_unexpected_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        terminal = CapturedErrorStream(is_tty=True)
        error = RuntimeError("Unexpected failure")

        def fail_with_progress(
            source: Path,
            destination: Path,
            **kwargs: object,
        ) -> ArchiveProcessingResult:
            callback = kwargs["progress_callback"]
            assert callable(callback)
            callback(
                ArchiveProgress(
                    phase=ArchiveProgressPhase.TRANSFORMING,
                    completed=0,
                    total=1,
                )
            )
            raise error

        monkeypatch.setattr(cli_module.sys, "stderr", terminal)
        monkeypatch.setattr(
            cli_module,
            "process_archive_file",
            fail_with_progress,
        )
        monkeypatch.setattr(sys, "argv", ["cbzfit", *required_arguments()])

        with pytest.raises(RuntimeError) as exception_info:
            main()

        assert exception_info.value is error
        line = "Processing [────────────────────] 0 % (0/1)"
        assert terminal.getvalue().endswith(
            "\r" + " " * len(line) + "\r"
        )
