# SPDX-License-Identifier: GPL-3.0-or-later

import argparse
from pathlib import Path

from cbzfit import __version__
from cbzfit.process import (
    ArchiveTransformationOptions,
    DestinationConflictMode,
    ImageProcessingOptions,
    OutputVerificationMode,
    process_archive_file,
)


def positive_integer(value: str) -> int:
    """Parse and return a positive integer for a command-line argument."""
    try:
        parsed_value = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            f"expected a positive integer, got {value!r}"
        ) from error

    if parsed_value <= 0:
        raise argparse.ArgumentTypeError(
            f"expected a positive integer, got {value!r}"
        )

    return parsed_value


def build_parser() -> argparse.ArgumentParser:
    """Build and return the CBZFit command-line parser."""
    parser = argparse.ArgumentParser(
        prog="cbzfit",
        description="Resize manga CBZ archives to fit a target display.",
    )

    parser.add_argument(
        "source",
        type=Path,
        metavar="SOURCE",
        help="source CBZ archive",
    )
    parser.add_argument(
        "destination",
        type=Path,
        metavar="DESTINATION",
        help="destination CBZ archive",
    )
    parser.add_argument(
        "--screen-width",
        type=positive_integer,
        required=True,
        metavar="PIXELS",
        help="target display width in portrait orientation",
    )
    parser.add_argument(
        "--screen-height",
        type=positive_integer,
        required=True,
        metavar="PIXELS",
        help="target display height in portrait orientation",
    )
    parser.add_argument(
        "--landscape-display",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "fit landscape images to the display in landscape orientation "
            "(default: enabled)"
        ),
    )
    parser.add_argument(
        "--upscale",
        action="store_true",
        help=(
            "allow images smaller than the target display to be enlarged "
            "(default: disabled)"
        ),
    )
    parser.add_argument(
        "--verify",
        type=OutputVerificationMode,
        choices=list(OutputVerificationMode),
        default=OutputVerificationMode.STRUCTURE,
        dest="verification_mode",
        metavar="MODE",
        help=(
            "output verification mode: none, structure, or crc "
            "(default: structure)"
        ),
    )
    parser.add_argument(
        "--conflict",
        type=DestinationConflictMode,
        choices=list(DestinationConflictMode),
        default=DestinationConflictMode.ERROR,
        dest="conflict_mode",
        metavar="MODE",
        help=(
            "existing destination policy: error or replace "
            "(default: error)"
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    return parser


def main() -> int:
    """Run the CBZFit command-line interface."""
    parser = build_parser()
    arguments = parser.parse_args()

    image_options = ImageProcessingOptions(
        portrait_screen_size=(
            arguments.screen_width,
            arguments.screen_height,
        ),
        use_landscape_display=arguments.landscape_display,
        allow_upscale=arguments.upscale,
    )
    transformation_options = ArchiveTransformationOptions(
        image_options=image_options,
    )

    process_archive_file(
        arguments.source,
        arguments.destination,
        options=transformation_options,
        verification_mode=arguments.verification_mode,
        conflict_mode=arguments.conflict_mode,
    )

    return 0