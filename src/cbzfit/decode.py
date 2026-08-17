# SPDX-License-Identifier: GPL-3.0-or-later

from pathlib import PurePosixPath

from PIL import Image

FORMAT_ALIASES = {
    "JPG": "JPEG",
    "JPEG": "JPEG",
    "PNG": "PNG",
    "WEBP": "WEBP",
}

EXTENSION_FORMATS = {
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".png": "PNG",
    ".webp": "WEBP",
}


class UnsupportedImageFormatError(ValueError):
    """Raised when an image format is missing, unsupported, or inconsistent."""


class UnsupportedImageContentError(ValueError):
    """Raised when supported image data uses an unsupported feature."""


def normalize_image_format(image_format: str) -> str:
    """Return the canonical name of a supported image format."""
    normalized_format = image_format.strip().upper()

    try:
        return FORMAT_ALIASES[normalized_format]
    except KeyError as error:
        raise UnsupportedImageFormatError(
            f"Unsupported image format: {image_format!r}."
        ) from error


def validate_source_image(
    image: Image.Image,
    filename: str,
) -> str:
    """Validate source image content and return its canonical format.

    The format detected by Pillow must be supported and must match the
    filename extension. Animated images are not supported.
    """
    detected_format = image.format

    if detected_format is None:
        raise UnsupportedImageFormatError(
            f"Image format could not be detected: {filename!r}."
        )

    try:
        canonical_format = normalize_image_format(detected_format)
    except UnsupportedImageFormatError as error:
        raise UnsupportedImageFormatError(
            f"Unsupported image content format for {filename!r}: "
            f"{detected_format!r}."
        ) from error

    extension = PurePosixPath(filename).suffix.lower()

    try:
        extension_format = EXTENSION_FORMATS[extension]
    except KeyError as error:
        raise UnsupportedImageFormatError(
            f"Unsupported image filename extension: {filename!r}."
        ) from error

    if canonical_format != extension_format:
        raise UnsupportedImageFormatError(
            f"Image content format {canonical_format!r} does not match the "
            f"filename extension for {filename!r}, which indicates "
            f"{extension_format!r}."
        )

    if getattr(image, "is_animated", False):
        raise UnsupportedImageContentError(
            f"Animated images are not supported: {filename!r}."
        )

    return canonical_format
