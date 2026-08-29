# SPDX-License-Identifier: GPL-3.0-or-later

import argparse
from pathlib import Path

from cbzfit import __version__
from cbzfit.archive import (
    InvalidArchiveError,
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
    ArchiveTransformationOptions,
    ArchiveTransformationResult,
    DestinationConflictMode,
    ImageProcessingOptions,
    OutputVerificationMode,
    SourceDestinationConflictError,
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


def format_count(
    singular: str,
    count: int,
) -> str:
    """Return a singular or plural noun based on a non-negative count."""
    if count < 0:
        raise ValueError("Count must not be negative.")
        
    return singular if count == 1 else f"{singular}s"


def print_processing_summary(
    destination: Path,
    result: ArchiveTransformationResult,
) -> None:
    """Print a concise archive-processing summary."""
    image_members = result.image_members
    transformed_images = result.transformed_images
    unchanged_images = result.unchanged_images
    copied_other_members = result.copied_other_members

    print(f"Output: {destination}")
    print(
        f"└─{format_count('Image', image_members)}: "
        f"{image_members} total, "
        f"{transformed_images} transformed, "
        f"{unchanged_images} unchanged. "
        f"{format_count('Other member', copied_other_members)} copied: "
        f"{copied_other_members}"
    )


def main() -> int:
    """Run the CBZFit command-line interface."""
    parser = build_parser()
    arguments = parser.parse_args()

    try:
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
        result = process_archive_file(
            arguments.source,
            arguments.destination,
            options=transformation_options,
            verification_mode=arguments.verification_mode,
            conflict_mode=arguments.conflict_mode,
        )
    except (
        InvalidArchiveError,
        InvalidScreenDimensionError,
        InvalidScreenOrientationError,
        SourceDestinationConflictError,
        UnsupportedImageContentError,
        UnsupportedImageFormatError,
        OSError,
    ) as error:
        parser.exit(
            status=1,
            message=f"{parser.prog}: error: {error}\n",
        )

    print_processing_summary(
        arguments.destination,
        result,
    )

    return 0
