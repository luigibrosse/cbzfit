# SPDX-License-Identifier: GPL-3.0-or-later

from dataclasses import dataclass
from io import BytesIO
from typing import BinaryIO

from PIL import Image

from cbzfit.decode import (
    UnsupportedImageFormatError,
    normalize_image_format,
)
from cbzfit.image import PreparedImage, prepare_image_for_format

FORMAT_EXTENSIONS = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
}

DEFAULT_JPEG_QUALITY = 85
DEFAULT_PNG_COMPRESSION_LEVEL = 9
DEFAULT_WEBP_QUALITY = 85
DEFAULT_WEBP_METHOD = 4


@dataclass(frozen=True)
class EncoderOptions:
    """Store image encoder settings.

    When PNG optimization is enabled, Pillow uses maximum compression effort,
    so png_compress_level has no effect.
    """

    jpeg_quality: int = DEFAULT_JPEG_QUALITY
    jpeg_optimize: bool = True
    jpeg_progressive: bool = False

    png_compress_level: int = DEFAULT_PNG_COMPRESSION_LEVEL
    png_optimize: bool = True

    webp_quality: int = DEFAULT_WEBP_QUALITY
    webp_method: int = DEFAULT_WEBP_METHOD
    webp_lossless: bool = False

    preserve_icc_profile: bool = True

    def __post_init__(self) -> None:
        """Validate encoder settings."""
        if not 0 <= self.jpeg_quality <= 95:
            raise ValueError(
                "JPEG quality must be between 0 and 95."
            )

        if not 0 <= self.png_compress_level <= 9:
            raise ValueError(
                "PNG compression level must be between 0 and 9."
            )

        if not 0 <= self.webp_quality <= 100:
            raise ValueError(
                "WebP quality must be between 0 and 100."
            )

        if not 0 <= self.webp_method <= 6:
            raise ValueError(
                "WebP method must be between 0 and 6."
            )


@dataclass(frozen=True)
class EncodedImage:
    """Contain encoded image data and its canonical format."""

    data: bytes
    format: str

    @property
    def extension(self) -> str:
        """Return the preferred filename extension for the image format."""
        return FORMAT_EXTENSIONS[self.format]

    @property
    def size(self) -> int:
        """Return the encoded image size in bytes."""
        return len(self.data)


def get_encoding_metadata(
    prepared_image: PreparedImage,
) -> dict[str, bytes | None]:
    """Return metadata to include when encoding a prepared image."""
    return {
        "icc_profile": prepared_image.icc_profile,
    }


def save_jpeg(
    image: Image.Image,
    destination: BinaryIO,
    options: EncoderOptions,
) -> None:
    """Prepare and encode an image as JPEG."""
    prepared_image = prepare_image_for_format(
        image,
        output_format="JPEG",
        preserve_icc_profile=options.preserve_icc_profile,
    )
    metadata = get_encoding_metadata(prepared_image)

    prepared_image.image.save(
        destination,
        format="JPEG",
        quality=options.jpeg_quality,
        optimize=options.jpeg_optimize,
        progressive=options.jpeg_progressive,
        **metadata,
    )


def save_png(
    image: Image.Image,
    destination: BinaryIO,
    options: EncoderOptions,
) -> None:
    """Prepare and encode an image as PNG."""
    prepared_image = prepare_image_for_format(
        image,
        output_format="PNG",
        preserve_icc_profile=options.preserve_icc_profile,
    )
    metadata = get_encoding_metadata(prepared_image)

    prepared_image.image.save(
        destination,
        format="PNG",
        compress_level=options.png_compress_level,
        optimize=options.png_optimize,
        **metadata,
    )


def save_webp(
    image: Image.Image,
    destination: BinaryIO,
    options: EncoderOptions,
) -> None:
    """Prepare and encode an image as WebP."""
    prepared_image = prepare_image_for_format(
        image,
        output_format="WEBP",
        preserve_icc_profile=options.preserve_icc_profile,
    )
    metadata = get_encoding_metadata(prepared_image)

    prepared_image.image.save(
        destination,
        format="WEBP",
        quality=options.webp_quality,
        method=options.webp_method,
        lossless=options.webp_lossless,
        **metadata,
    )


def encode_image(
    image: Image.Image,
    output_format: str,
    *,
    options: EncoderOptions | None = None,
) -> EncodedImage:
    """Encode an image in memory using the selected output format."""
    normalized_format = normalize_image_format(
        output_format,
    )
    encoder_options = options or EncoderOptions()
    output = BytesIO()

    if normalized_format == "JPEG":
        save_jpeg(
            image,
            output,
            encoder_options,
        )
    elif normalized_format == "PNG":
        save_png(
            image,
            output,
            encoder_options,
        )
    elif normalized_format == "WEBP":
        save_webp(
            image,
            output,
            encoder_options,
        )
    else:
        raise UnsupportedImageFormatError(
            f"Unsupported image format: {output_format!r}."
        )

    return EncodedImage(
        data=output.getvalue(),
        format=normalized_format,
    )
