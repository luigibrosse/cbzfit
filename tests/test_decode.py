# SPDX-License-Identifier: GPL-3.0-or-later

import re
from io import BytesIO
from unittest.mock import Mock

import pytest
from PIL import Image

from cbzfit.decode import (
    UnsupportedImageContentError,
    UnsupportedImageFormatError,
    normalize_image_format,
    validate_source_image,
)


def exact_message(message: str) -> str:
    """Return a regular expression that matches a complete error message."""
    return rf"^{re.escape(message)}$"


def create_encoded_image(
    image_format: str,
    *,
    mode: str = "RGB",
) -> BytesIO:
    """Create an in-memory image encoded in the selected format."""
    image = Image.new(
        mode=mode,
        size=(10, 10),
        color="white",
    )
    image_stream = BytesIO()
    image.save(
        image_stream,
        format=image_format,
    )
    image_stream.seek(0)

    return image_stream


@pytest.mark.parametrize(
    ("image_format", "expected_format"),
    [
        ("JPG", "JPEG"),
        ("jpg", "JPEG"),
        ("JPEG", "JPEG"),
        ("jpeg", "JPEG"),
        (" jpeg ", "JPEG"),
        ("PNG", "PNG"),
        ("png", "PNG"),
        ("WEBP", "WEBP"),
        ("webp", "WEBP"),
    ],
)
def test_image_formats_are_normalized(
    image_format: str,
    expected_format: str,
) -> None:
    assert normalize_image_format(image_format) == expected_format


@pytest.mark.parametrize(
    "image_format",
    [
        "",
        "GIF",
        "BMP",
        "TIFF",
        "AVIF",
    ],
)
def test_unsupported_image_formats_are_rejected(
    image_format: str,
) -> None:
    expected_message = f"Unsupported image format: {image_format!r}."

    with pytest.raises(
        UnsupportedImageFormatError,
        match=exact_message(expected_message),
    ):
        normalize_image_format(image_format)


@pytest.mark.parametrize(
    ("image_format", "filename", "expected_format"),
    [
        ("JPEG", "001.jpg", "JPEG"),
        ("JPEG", "001.jpeg", "JPEG"),
        ("JPEG", "001.JPG", "JPEG"),
        ("JPEG", "Chapter 01/001.JpEg", "JPEG"),
        ("PNG", "002.png", "PNG"),
        ("PNG", "Chapter 01/002.PNG", "PNG"),
        ("WEBP", "003.webp", "WEBP"),
        ("WEBP", "Chapter 01/003.WebP", "WEBP"),
    ],
)
def test_source_image_format_is_validated(
    image_format: str,
    filename: str,
    expected_format: str,
) -> None:
    image = Mock(spec=Image.Image)
    image.format = image_format
    image.is_animated = False

    result = validate_source_image(
        image=image,
        filename=filename,
    )

    assert result == expected_format


def test_real_jpeg_image_is_validated() -> None:
    image_stream = create_encoded_image("JPEG")

    with Image.open(image_stream) as image:
        result = validate_source_image(
            image=image,
            filename="001.jpg",
        )

    assert result == "JPEG"


def test_real_png_image_is_validated() -> None:
    image_stream = create_encoded_image("PNG")

    with Image.open(image_stream) as image:
        result = validate_source_image(
            image=image,
            filename="001.png",
        )

    assert result == "PNG"


def test_real_webp_image_is_validated() -> None:
    image_stream = create_encoded_image("WEBP")

    with Image.open(image_stream) as image:
        result = validate_source_image(
            image=image,
            filename="001.webp",
        )

    assert result == "WEBP"


def test_missing_detected_format_is_rejected() -> None:
    filename = "001.jpg"
    image = Mock(spec=Image.Image)
    image.format = None
    image.is_animated = False
    expected_message = (
        f"Image format could not be detected: {filename!r}."
    )

    with pytest.raises(
        UnsupportedImageFormatError,
        match=exact_message(expected_message),
    ):
        validate_source_image(
            image=image,
            filename=filename,
        )


def test_unsupported_content_format_is_rejected() -> None:
    filename = "001.gif"
    detected_format = "GIF"
    image = Mock(spec=Image.Image)
    image.format = detected_format
    image.is_animated = False
    expected_message = (
        f"Unsupported image content format for {filename!r}: "
        f"{detected_format!r}."
    )

    with pytest.raises(
        UnsupportedImageFormatError,
        match=exact_message(expected_message),
    ):
        validate_source_image(
            image=image,
            filename=filename,
        )


def test_supported_extension_can_contain_unsupported_content() -> None:
    filename = "001.jpg"
    detected_format = "GIF"
    image = Mock(spec=Image.Image)
    image.format = detected_format
    image.is_animated = False

    expected_message = (
        f"Unsupported image content format for {filename!r}: "
        f"{detected_format!r}."
    )

    with pytest.raises(
        UnsupportedImageFormatError,
        match=exact_message(expected_message),
    ):
        validate_source_image(
            image=image,
            filename=filename,
        )


@pytest.mark.parametrize(
    "filename",
    [
        "001",
        "001.gif",
        "001.bmp",
        "001.tiff",
    ],
)
def test_unsupported_filename_extensions_are_rejected(
    filename: str,
) -> None:
    image = Mock(spec=Image.Image)
    image.format = "JPEG"
    image.is_animated = False
    expected_message = (
        f"Unsupported image filename extension: {filename!r}."
    )

    with pytest.raises(
        UnsupportedImageFormatError,
        match=exact_message(expected_message),
    ):
        validate_source_image(
            image=image,
            filename=filename,
        )


@pytest.mark.parametrize(
    ("detected_format", "filename", "extension_format"),
    [
        ("JPEG", "001.png", "PNG"),
        ("JPEG", "001.webp", "WEBP"),
        ("PNG", "001.jpg", "JPEG"),
        ("PNG", "001.webp", "WEBP"),
        ("WEBP", "001.jpg", "JPEG"),
        ("WEBP", "001.png", "PNG"),
    ],
)
def test_content_and_filename_format_mismatches_are_rejected(
    detected_format: str,
    filename: str,
    extension_format: str,
) -> None:
    image = Mock(spec=Image.Image)
    image.format = detected_format
    image.is_animated = False
    expected_message = (
        f"Image content format {detected_format!r} does not match the "
        f"filename extension for {filename!r}, which indicates "
        f"{extension_format!r}."
    )

    with pytest.raises(
        UnsupportedImageFormatError,
        match=exact_message(expected_message),
    ):
        validate_source_image(
            image=image,
            filename=filename,
        )


@pytest.mark.parametrize(
    ("detected_format", "filename"),
    [
        ("PNG", "001.png"),
        ("WEBP", "001.webp"),
    ],
)
def test_animated_images_are_rejected(
    detected_format: str,
    filename: str,
) -> None:
    image = Mock(spec=Image.Image)
    image.format = detected_format
    image.is_animated = True
    expected_message = (
        f"Animated images are not supported: {filename!r}."
    )

    with pytest.raises(
        UnsupportedImageContentError,
        match=exact_message(expected_message),
    ):
        validate_source_image(
            image=image,
            filename=filename,
        )
