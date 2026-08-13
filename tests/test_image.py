# SPDX-License-Identifier: GPL-3.0-or-later

import pytest

from cbzfit.image import calculate_display_fit, fit_size_within


def test_portrait_manga_page_fits_portrait_display() -> None:
    result = calculate_display_fit(
        original_size=(2297, 3247),
        portrait_screen_size=(1404, 1872),
    )

    assert result == (1324, 1872)


def test_landscape_spread_fits_landscape_display() -> None:
    result = calculate_display_fit(
        original_size=(4594, 3247),
        portrait_screen_size=(1404, 1872),
    )

    assert result == (1872, 1323)


def test_landscape_spread_can_use_portrait_display_bounds() -> None:
    result = calculate_display_fit(
        original_size=(4594, 3247),
        portrait_screen_size=(1404, 1872),
        use_landscape_display=False,
    )

    assert result == (1404, 992)


def test_small_image_is_not_upscaled_by_default() -> None:
    result = fit_size_within(
        original_size=(1000, 500),
        bounds=(1872, 1404),
    )

    assert result == (1000, 500)


def test_small_image_can_be_upscaled() -> None:
    result = fit_size_within(
        original_size=(1000, 500),
        bounds=(1872, 1404),
        allow_upscale=True,
    )

    assert result == (1872, 936)


def test_image_at_exact_bounds_is_unchanged() -> None:
    result = fit_size_within(
        original_size=(1404, 1872),
        bounds=(1404, 1872),
    )

    assert result == (1404, 1872)


def test_square_image_uses_portrait_display_bounds() -> None:
    result = calculate_display_fit(
        original_size=(2000, 2000),
        portrait_screen_size=(1404, 1872),
    )

    assert result == (1404, 1404)


def test_square_screen_dimensions_are_accepted() -> None:
    result = calculate_display_fit(
        original_size=(2000, 2000),
        portrait_screen_size=(1404, 1404),
    )

    assert result == (1404, 1404)


@pytest.mark.parametrize(
    "original_size",
    [
        (1, 1000),
        (1000, 1),
    ],
)
def test_calculated_dimensions_never_become_zero(
    original_size: tuple[int, int],
) -> None:
    target_width, target_height = fit_size_within(
        original_size=original_size,
        bounds=(1, 1),
    )

    assert target_width >= 1
    assert target_height >= 1


@pytest.mark.parametrize(
    "original_size",
    [
        (0, 100),
        (100, 0),
        (-1, 100),
        (100, -1),
    ],
)
def test_invalid_original_dimensions_are_rejected(
    original_size: tuple[int, int],
) -> None:
    with pytest.raises(
        ValueError,
        match="Original dimensions must be positive integers",
    ):
        fit_size_within(
            original_size=original_size,
            bounds=(1404, 1872),
        )


@pytest.mark.parametrize(
    "bounds",
    [
        (0, 100),
        (100, 0),
        (-1, 100),
        (100, -1),
    ],
)
def test_invalid_bound_dimensions_are_rejected(
    bounds: tuple[int, int],
) -> None:
    with pytest.raises(
        ValueError,
        match="Bound dimensions must be positive integers",
    ):
        fit_size_within(
            original_size=(2297, 3247),
            bounds=bounds,
        )


@pytest.mark.parametrize(
    "portrait_screen_size",
    [
        (0, 1404),
        (1404, 0),
        (-1, 1404),
        (1404, -1),
    ],
)
def test_invalid_screen_dimensions_are_rejected(
    portrait_screen_size: tuple[int, int],
) -> None:
    with pytest.raises(
        ValueError,
        match="Screen dimensions must be positive integers",
    ):
        calculate_display_fit(
            original_size=(2297, 3247),
            portrait_screen_size=portrait_screen_size,
        )


def test_landscape_screen_dimensions_are_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="Screen size must be provided in portrait orientation",
    ):
        calculate_display_fit(
            original_size=(2297, 3247),
            portrait_screen_size=(1872, 1404),
        )
