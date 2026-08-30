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
    ArchiveProcessingResult,
    ArchiveTransformationOptions,
    DestinationConflictMode,
    ImageProcessingOptions,
    OutputVerificationMode,
    SourceDestinationConflictError,
    process_archive_file,
)

MEBIBYTE = 1024**2


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


def format_file_size(size_bytes: int) -> str:
    """Format a non-negative byte count as MiB with one decimal place."""
    if size_bytes < 0:
        raise ValueError("File size must not be negative.")

    return f"{size_bytes / MEBIBYTE:.1f} MiB"


def calculate_size_change_percentage(
    source_size: int,
    destination_size: int,
) -> float | None:
    """Return the absolute percentage size change, or None for a zero source."""
    if source_size < 0 or destination_size < 0:
        raise ValueError("File sizes must not be negative.")
    if source_size == 0:
        return None

    return abs(destination_size - source_size) / source_size * 100


def format_size_change(
    source_size: int,
    destination_size: int,
) -> str:
    """Describe the direction and percentage of an archive-size change."""
    percentage = calculate_size_change_percentage(
        source_size,
        destination_size,
    )
    if source_size == destination_size:
        return "no change"
    if percentage is None:
        return "percentage unavailable"
    direction = "decrease" if destination_size < source_size else "increase"

    return f"{percentage:.1f} % {direction}"


def format_elapsed_time(elapsed_seconds: float) -> str:
    """Format elapsed seconds with one decimal place, clamped to zero."""
    return f"{max(elapsed_seconds, 0.0):.1f} s"


def print_processing_summary(
    destination: Path,
    result: ArchiveProcessingResult,
) -> None:
    """Print archive-content, elapsed-time, and actual file-size metrics."""
    transformation = result.transformation_result
    image_members = transformation.image_members
    transformed_images = transformation.transformed_images
    unchanged_images = transformation.unchanged_images
    copied_other_members = transformation.copied_other_members

    print(
        f"Output: {destination} completed in "
        f"{format_elapsed_time(result.elapsed_seconds)}"
    )
    print(
        f"└─{format_count('Image', image_members)}: "
        f"{image_members} total, "
        f"{transformed_images} transformed, "
        f"{unchanged_images} unchanged. "
        f"{format_count('Other member', copied_other_members)} copied: "
        f"{copied_other_members}"
    )
    print(
        f"└─Size: {format_file_size(result.source_file_size)} -> "
        f"{format_file_size(result.destination_file_size)}, "
        f"{format_size_change(result.source_file_size, result.destination_file_size)}"
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
