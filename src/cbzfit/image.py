# SPDX-License-Identifier: GPL-3.0-or-later

from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageCms

from cbzfit.decode import (
    UnsupportedImageFormatError,
    normalize_image_format,
)


@dataclass(frozen=True)
class PreparedImage:
    """Contain a format-compatible image and optional ICC profile."""

    image: Image.Image
    icc_profile: bytes | None = None


def fit_size_within(
    original_size: tuple[int, int],
    bounds: tuple[int, int],
    allow_upscale: bool = False,
) -> tuple[int, int]:
    """Fit a size within fixed bounds while preserving its aspect ratio."""
    original_width, original_height = original_size
    bound_width, bound_height = bounds

    if original_width <= 0 or original_height <= 0:
        raise ValueError(
            "Original dimensions must be positive integers."
        )

    if bound_width <= 0 or bound_height <= 0:
        raise ValueError(
            "Bound dimensions must be positive integers."
        )

    width_scale = bound_width / original_width
    height_scale = bound_height / original_height
    scale = min(width_scale, height_scale)

    if not allow_upscale:
        scale = min(scale, 1.0)

    target_width = max(1, round(original_width * scale))
    target_height = max(1, round(original_height * scale))

    return target_width, target_height


def calculate_display_fit(
    original_size: tuple[int, int],
    portrait_screen_size: tuple[int, int],
    use_landscape_display: bool = True,
    allow_upscale: bool = False,
) -> tuple[int, int]:
    """Calculate the image size for the expected display orientation.

    portrait_screen_size must contain the display resolution as
    (width, height) in portrait orientation.

    When use_landscape_display is True, CBZFit assumes that the reader will
    display landscape images with the device turned to landscape orientation.
    CBZFit therefore swaps the display width and height when calculating the
    target size, but it does not rotate the image itself.
    """
    image_width, image_height = original_size
    screen_width, screen_height = portrait_screen_size

    if screen_width <= 0 or screen_height <= 0:
        raise ValueError(
            "Screen dimensions must be positive integers."
        )

    if screen_width > screen_height:
        raise ValueError(
            "Screen size must be provided in portrait orientation."
        )

    image_is_landscape = image_width > image_height

    if image_is_landscape and use_landscape_display:
        display_bounds = (screen_height, screen_width)
    else:
        display_bounds = (screen_width, screen_height)

    return fit_size_within(
        original_size=original_size,
        bounds=display_bounds,
        allow_upscale=allow_upscale,
    )


def resize_for_display(
    image: Image.Image,
    portrait_screen_size: tuple[int, int],
    use_landscape_display: bool = True,
    allow_upscale: bool = False,
    resample: Image.Resampling = Image.Resampling.LANCZOS,
) -> Image.Image:
    """Resize an image to the size calculated by calculate_display_fit().

    The original image is returned unchanged when resizing is not required.
    Otherwise, a new image is returned using the selected resampling filter.
    """
    target_size = calculate_display_fit(
        original_size=image.size,
        portrait_screen_size=portrait_screen_size,
        use_landscape_display=use_landscape_display,
        allow_upscale=allow_upscale,
    )

    if target_size == image.size:
        return image

    return image.resize(
        target_size,
        resample=resample,
    )


def get_embedded_icc_profile(
    image: Image.Image,
) -> bytes | None:
    """Return the embedded ICC profile when present."""
    icc_profile = image.info.get("icc_profile")

    if isinstance(icc_profile, bytes):
        return icc_profile

    return None


def convert_cmyk_to_rgb(
    image: Image.Image,
    *,
    preserve_icc_profile: bool,
) -> PreparedImage:
    """Convert a CMYK image to RGB, using ICC profiles when available."""
    source_icc_profile = get_embedded_icc_profile(image)

    if source_icc_profile is None:
        return PreparedImage(
            image=image.convert("RGB"),
        )

    try:
        source_profile = ImageCms.ImageCmsProfile(
            BytesIO(source_icc_profile)
        )

        target_profile = ImageCms.ImageCmsProfile(
            ImageCms.createProfile("sRGB")
        )

        converted_image = ImageCms.profileToProfile(
            image,
            source_profile,
            target_profile,
            outputMode="RGB",
        )
    except (ImageCms.PyCMSError, OSError, ValueError):
        return PreparedImage(
            image=image.convert("RGB"),
        )

    if preserve_icc_profile:
        return PreparedImage(
            image=converted_image,
            icc_profile=target_profile.tobytes(),
        )

    return PreparedImage(
        image=converted_image,
    )


def flatten_transparency_on_white(
    image: Image.Image,
) -> Image.Image:
    """Convert transparency to an opaque white background."""
    rgba_image = image.convert("RGBA")

    background = Image.new(
        mode="RGB",
        size=rgba_image.size,
        color="white",
    )

    background.paste(
        rgba_image,
        mask=rgba_image.getchannel("A"),
    )

    return background


def prepare_image_for_format(
    image: Image.Image,
    output_format: str,
    *,
    preserve_icc_profile: bool,
) -> PreparedImage:
    """Return an image compatible with an output image format."""
    normalized_format = normalize_image_format(
        output_format,
    )

    source_icc_profile = get_embedded_icc_profile(image)

    working_image = image
    prepared_icc_profile: bytes | None = None

    if working_image.mode == "CMYK":
        prepared_image = convert_cmyk_to_rgb(
            working_image,
            preserve_icc_profile=preserve_icc_profile,
        )

        working_image = prepared_image.image
        prepared_icc_profile = prepared_image.icc_profile

    if normalized_format == "JPEG":
        if working_image.mode in {"L", "RGB"}:
            compatible_image = working_image
        elif working_image.has_transparency_data:
            compatible_image = flatten_transparency_on_white(
                working_image,
            )
        else:
            compatible_image = working_image.convert("RGB")

    elif normalized_format == "PNG":
        if working_image.mode in {
            "1",
            "L",
            "LA",
            "P",
            "RGB",
            "RGBA",
            "I",
            "I;16",
        }:
            compatible_image = working_image
        elif working_image.has_transparency_data:
            compatible_image = working_image.convert("RGBA")
        else:
            compatible_image = working_image.convert("RGB")

    elif normalized_format == "WEBP":
        if working_image.mode in {"RGB", "RGBA"}:
            compatible_image = working_image
        elif working_image.has_transparency_data:
            compatible_image = working_image.convert("RGBA")
        else:
            compatible_image = working_image.convert("RGB")

    else:
        raise UnsupportedImageFormatError(
            f"Unsupported image format: {output_format!r}."
        )

    if prepared_icc_profile is not None:
        return PreparedImage(
            image=compatible_image,
            icc_profile=prepared_icc_profile,
        )

    mode_was_preserved = (
        compatible_image.mode == image.mode
    )

    if (
        preserve_icc_profile
        and mode_was_preserved
        and source_icc_profile is not None
    ):
        return PreparedImage(
            image=compatible_image,
            icc_profile=source_icc_profile,
        )

    return PreparedImage(
        image=compatible_image,
    )
