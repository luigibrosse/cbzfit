# SPDX-License-Identifier: GPL-3.0-or-later


def fit_size_within(
    original_size: tuple[int, int],
    bounds: tuple[int, int],
    allow_upscale: bool = False,
) -> tuple[int, int]:
    """Fit a size within fixed bounds while preserving its aspect ratio."""
    original_width, original_height = original_size
    bound_width, bound_height = bounds

    if original_width <= 0 or original_height <= 0:
        raise ValueError("Original dimensions must be positive integers.")

    if bound_width <= 0 or bound_height <= 0:
        raise ValueError("Bound dimensions must be positive integers.")

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
    """
    Calculate the image size for the expected display orientation.

    portrait_screen_size must contain the display resolution as
    (width, height) in portrait orientation.

    When use_landscape_display is True, CBZFit assumes that the reader will
    display landscape images with the device turned to landscape orientation.
    CBZFit therefore swaps the display width and height when calculating the
    target size, but it does not rotate the image itself.
    """
    image_width, image_height = original_size
    screen_width, screen_height = portrait_screen_size

    if screen_width > screen_height:
        raise ValueError("Screen size must be provided in portrait orientation.")

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
