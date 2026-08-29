# SPDX-License-Identifier: GPL-3.0-or-later

import argparse
import re
import sys
from io import BytesIO
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
    build_parser,
    format_count,
    main,
    positive_integer,
    print_processing_summary,
)
from cbzfit.decode import (
    UnsupportedImageContentError,
    UnsupportedImageFormatError,
)
from cbzfit.process import (
    ArchiveTransformationOptions,
    ArchiveTransformationResult,
    DestinationConflictMode,
    ImageProcessingOptions,
    OutputVerificationMode,
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
            ]
        )

        assert arguments.landscape_display is False
        assert arguments.upscale is True
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


class TestPrintProcessingSummary:
    @pytest.mark.parametrize(
        ("result", "expected_summary"),
        [
            (
                ArchiveTransformationResult(
                    total_file_members=300,
                    image_members=200,
                    transformed_images=100,
                    unchanged_images=100,
                    copied_other_members=100,
                    input_uncompressed_size=1_000,
                    output_uncompressed_size=600,
                ),
                (
                    "Output: optimized.cbz\n"
                    "└─Images: 200 total, 100 transformed, "
                    "100 unchanged. Other members copied: 100\n"
                ),
            ),
            (
                ArchiveTransformationResult(
                    total_file_members=2,
                    image_members=1,
                    transformed_images=1,
                    unchanged_images=0,
                    copied_other_members=1,
                    input_uncompressed_size=100,
                    output_uncompressed_size=80,
                ),
                (
                    "Output: optimized.cbz\n"
                    "└─Image: 1 total, 1 transformed, "
                    "0 unchanged. Other member copied: 1\n"
                ),
            ),
            (
                ArchiveTransformationResult(
                    total_file_members=0,
                    image_members=0,
                    transformed_images=0,
                    unchanged_images=0,
                    copied_other_members=0,
                    input_uncompressed_size=0,
                    output_uncompressed_size=0,
                ),
                (
                    "Output: optimized.cbz\n"
                    "└─Images: 0 total, 0 transformed, "
                    "0 unchanged. Other members copied: 0\n"
                ),
            ),
        ],
    )
    def test_exact_two_line_summary_is_printed(
        self,
        result: ArchiveTransformationResult,
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
        result = ArchiveTransformationResult(
            total_file_members=1,
            image_members=1,
            transformed_images=0,
            unchanged_images=1,
            copied_other_members=0,
            input_uncompressed_size=100,
            output_uncompressed_size=100,
        )

        print_processing_summary(destination, result)

        assert capsys.readouterr().out.startswith(
            f"Output: {destination}\n"
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
        processing_result = ArchiveTransformationResult(
            total_file_members=3,
            image_members=2,
            transformed_images=1,
            unchanged_images=1,
            copied_other_members=1,
            input_uncompressed_size=1_000,
            output_uncompressed_size=600,
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
        }
        print_summary.assert_called_once_with(
            destination_path,
            processing_result,
        )

    @pytest.mark.parametrize(
        "error",
        [
            InvalidArchiveError("Archive validation failed"),
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

    def test_unexpected_processing_error_is_propagated(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        error = RuntimeError("Unexpected processing failure")
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

        with pytest.raises(RuntimeError) as exception_info:
            main()

        captured = capsys.readouterr()
        assert exception_info.value is error
        assert captured.out == ""
        assert captured.err == ""
        process_archive.assert_called_once()
        print_summary.assert_not_called()

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
        assert captured.out == (
            f"Output: {destination_path}\n"
            "└─Image: 1 total, 1 transformed, 0 unchanged. "
            "Other member copied: 1\n"
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
