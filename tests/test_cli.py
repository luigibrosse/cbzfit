# SPDX-License-Identifier: GPL-3.0-or-later

import argparse
import re
import sys
from pathlib import Path

import pytest

from cbzfit import __version__
from cbzfit.cli import build_parser, main, positive_integer
from cbzfit.process import (
    DestinationConflictMode,
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


class TestMain:

    def test_valid_arguments_return_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "cbzfit",
                *required_arguments(),
            ],
        )

        assert main() == 0
