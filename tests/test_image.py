# SPDX-License-Identifier: GPL-3.0-or-later

import re
from unittest.mock import Mock

import pytest
from PIL import Image, ImageCms

from cbzfit.decode import UnsupportedImageFormatError
from cbzfit.image import (
    InvalidScreenDimensionError,
    InvalidScreenOrientationError,
    PreparedImage,
    calculate_display_fit,
    convert_cmyk_to_rgb,
    fit_size_within,
    flatten_transparency_on_white,
    get_embedded_icc_profile,
    prepare_image_for_format,
    resize_for_display,
)


def exact_message(message: str) -> str:
    """Return a regular expression that matches a complete error message."""
    return rf"^{re.escape(message)}$"


class TestInvalidScreenDimensionError:
    def test_error_is_a_value_error(self) -> None:
        assert issubclass(InvalidScreenDimensionError, ValueError)


class TestInvalidScreenOrientationError:
    def test_error_is_a_value_error(self) -> None:
        assert issubclass(InvalidScreenOrientationError, ValueError)

class TestFitSizeWithin:

    def test_small_image_is_not_upscaled_by_default(self) -> None:
        result = fit_size_within(
            original_size=(1000, 500),
            bounds=(1872, 1404),
        )

        assert result == (1000, 500)


    def test_small_image_can_be_upscaled(self) -> None:
        result = fit_size_within(
            original_size=(1000, 500),
            bounds=(1872, 1404),
            allow_upscale=True,
        )

        assert result == (1872, 936)


    def test_image_at_exact_bounds_is_unchanged(self) -> None:
        result = fit_size_within(
            original_size=(1404, 1872),
            bounds=(1404, 1872),
        )

        assert result == (1404, 1872)


    @pytest.mark.parametrize(
        "original_size",
        [
            (1, 1000),
            (1000, 1),
        ],
    )
    def test_calculated_dimensions_never_become_zero(
        self,
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
        self,
        original_size: tuple[int, int],
    ) -> None:
        expected_message = (
            "Original dimensions must be positive integers."
        )

        with pytest.raises(
            ValueError,
            match=exact_message(expected_message),
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
        self,
        bounds: tuple[int, int],
    ) -> None:
        expected_message = (
            "Bound dimensions must be positive integers."
        )

        with pytest.raises(
            ValueError,
            match=exact_message(expected_message),
        ):
            fit_size_within(
                original_size=(2297, 3247),
                bounds=bounds,
            )

class TestCalculateDisplayFit:

    def test_portrait_manga_page_fits_portrait_display(self) -> None:
        result = calculate_display_fit(
            original_size=(2297, 3247),
            portrait_screen_size=(1404, 1872),
        )

        assert result == (1324, 1872)


    def test_landscape_spread_fits_landscape_display(self) -> None:
        result = calculate_display_fit(
            original_size=(4594, 3247),
            portrait_screen_size=(1404, 1872),
        )

        assert result == (1872, 1323)


    def test_landscape_spread_can_use_portrait_display_bounds(self) -> None:
        result = calculate_display_fit(
            original_size=(4594, 3247),
            portrait_screen_size=(1404, 1872),
            use_landscape_display=False,
        )

        assert result == (1404, 992)


    def test_square_image_uses_portrait_display_bounds(self) -> None:
        result = calculate_display_fit(
            original_size=(2000, 2000),
            portrait_screen_size=(1404, 1872),
        )

        assert result == (1404, 1404)


    def test_square_screen_dimensions_are_accepted(self) -> None:
        result = calculate_display_fit(
            original_size=(2000, 2000),
            portrait_screen_size=(1404, 1404),
        )

        assert result == (1404, 1404)


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
        self,
        portrait_screen_size: tuple[int, int],
    ) -> None:
        expected_message = (
            "Screen dimensions must be positive integers."
        )

        with pytest.raises(
            InvalidScreenDimensionError,
            match=exact_message(expected_message),
        ) as exception_info:
            calculate_display_fit(
                original_size=(2297, 3247),
                portrait_screen_size=portrait_screen_size,
            )

        assert isinstance(exception_info.value, ValueError)


    def test_landscape_screen_dimensions_are_rejected(self) -> None:
        expected_message = (
            "Screen size must be provided in portrait orientation."
        )

        with pytest.raises(
            InvalidScreenOrientationError,
            match=exact_message(expected_message),
        ) as exception_info:
            calculate_display_fit(
                original_size=(2297, 3247),
                portrait_screen_size=(1872, 1404),
            )

        assert isinstance(exception_info.value, ValueError)

class TestResizeForDisplay:

    def test_portrait_image_is_resized_for_portrait_display(self) -> None:
        image = Image.new(
            mode="RGB",
            size=(2297, 3247),
            color="white",
        )

        result = resize_for_display(
            image=image,
            portrait_screen_size=(1404, 1872),
        )

        assert result.size == (1324, 1872)
        assert result is not image
        assert image.size == (2297, 3247)


    def test_landscape_image_is_resized_for_landscape_display(self) -> None:
        image = Image.new(
            mode="RGB",
            size=(4594, 3247),
            color="white",
        )

        result = resize_for_display(
            image=image,
            portrait_screen_size=(1404, 1872),
        )

        assert result.size == (1872, 1323)
        assert result is not image
        assert image.size == (4594, 3247)


    def test_landscape_image_can_use_portrait_display_bounds(self) -> None:
        image = Image.new(
            mode="RGB",
            size=(4594, 3247),
            color="white",
        )

        result = resize_for_display(
            image=image,
            portrait_screen_size=(1404, 1872),
            use_landscape_display=False,
        )

        assert result.size == (1404, 992)
        assert result is not image
        assert image.size == (4594, 3247)


    def test_image_is_returned_unchanged_when_resize_is_not_required(
        self,
    ) -> None:
        image = Image.new(
            mode="RGB",
            size=(1000, 1400),
            color="white",
        )

        result = resize_for_display(
            image=image,
            portrait_screen_size=(1404, 1872),
        )

        assert result is image
        assert result.size == (1000, 1400)


    def test_image_is_upscaled_when_enabled(self) -> None:
        image = Image.new(
            mode="RGB",
            size=(1000, 500),
            color="white",
        )

        result = resize_for_display(
            image=image,
            portrait_screen_size=(1404, 1872),
            allow_upscale=True,
        )

        assert result.size == (1872, 936)
        assert result is not image
        assert image.size == (1000, 500)


    def test_image_accepts_bicubic_resampling(self) -> None:
        image = Image.new(
            mode="RGB",
            size=(2297, 3247),
            color="white",
        )

        result = resize_for_display(
            image=image,
            portrait_screen_size=(1404, 1872),
            resample=Image.Resampling.BICUBIC,
        )

        assert result.size == (1324, 1872)
        assert result is not image

class TestGetEmbeddedIccProfile:

    def test_missing_icc_profile_returns_none(self) -> None:
        image = Image.new(
            mode="RGB",
            size=(10, 20),
            color="white",
        )

        assert get_embedded_icc_profile(image) is None


    def test_embedded_icc_profile_is_returned(self) -> None:
        image = Image.new(
            mode="RGB",
            size=(10, 20),
            color="white",
        )
        profile = ImageCms.ImageCmsProfile(
            ImageCms.createProfile("sRGB")
        )
        profile_data = profile.tobytes()
        image.info["icc_profile"] = profile_data

        assert get_embedded_icc_profile(image) == profile_data


    def test_non_binary_icc_profile_returns_none(self) -> None:
        image = Image.new(
            mode="RGB",
            size=(10, 20),
            color="white",
        )
        image.info["icc_profile"] = "not-binary-profile-data"

        assert get_embedded_icc_profile(image) is None

class TestFlattenTransparencyOnWhite:

    def test_transparency_is_flattened_onto_white(self) -> None:
        image = Image.new(
            mode="RGBA",
            size=(3, 1),
        )
        image.putdata(
            [
                (255, 0, 0, 255),
                (255, 0, 0, 128),
                (255, 0, 0, 0),
            ]
        )

        result = flatten_transparency_on_white(image)

        assert image.mode == "RGBA"
        assert image.getpixel((1, 0)) == (255, 0, 0, 128)
        assert result.mode == "RGB"
        assert result.size == image.size
        assert result.getpixel((0, 0)) == (255, 0, 0)
        assert result.getpixel((1, 0)) == (255, 127, 127)
        assert result.getpixel((2, 0)) == (255, 255, 255)



    def test_flatten_transparency_returns_new_image(self) -> None:
        image = Image.new(
            mode="RGBA",
            size=(10, 20),
            color=(0, 0, 0, 0),
        )

        result = flatten_transparency_on_white(image)

        assert result is not image

class TestConvertCmykToRgb:

    def test_cmyk_image_without_profile_is_converted_to_rgb(self) -> None:
        image = Image.new(
            mode="CMYK",
            size=(10, 20),
            color=(0, 0, 0, 255),
        )

        result = convert_cmyk_to_rgb(
            image,
            preserve_icc_profile=False,
        )

        assert isinstance(result, PreparedImage)
        assert result.image.mode == "RGB"
        assert result.image.size == image.size
        assert result.image is not image
        assert result.icc_profile is None
        assert image.mode == "CMYK"


    def test_cmyk_conversion_falls_back_when_profile_is_invalid(self) -> None:
        image = Image.new(
            mode="CMYK",
            size=(10, 20),
            color=(0, 0, 0, 255),
        )
        image.info["icc_profile"] = b"invalid-icc-profile"

        result = convert_cmyk_to_rgb(
            image,
            preserve_icc_profile=True,
        )

        assert isinstance(result, PreparedImage)
        assert result.image.mode == "RGB"
        assert result.image.size == image.size
        assert result.image is not image
        assert result.icc_profile is None
        assert image.mode == "CMYK"


    def test_cmyk_conversion_preserves_icc_profile_when_requested(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        image = Image.new(
            mode="CMYK",
            size=(10, 20),
            color=(0, 0, 0, 255),
        )
        source_profile_data = b"valid-cmyk-profile"
        image.info["icc_profile"] = source_profile_data

        converted_image = Image.new(
            mode="RGB",
            size=image.size,
            color="black",
        )
        source_profile = Mock()
        created_srgb_profile = Mock()
        target_profile = Mock()
        target_profile.tobytes.return_value = b"generated-srgb-profile"

        image_cms_profile = Mock(
            side_effect=[
                source_profile,
                target_profile,
            ]
        )
        create_profile = Mock(
            return_value=created_srgb_profile,
        )
        profile_to_profile = Mock(
            return_value=converted_image,
        )

        monkeypatch.setattr(
            ImageCms,
            "ImageCmsProfile",
            image_cms_profile,
        )
        monkeypatch.setattr(
            ImageCms,
            "createProfile",
            create_profile,
        )
        monkeypatch.setattr(
            ImageCms,
            "profileToProfile",
            profile_to_profile,
        )

        result = convert_cmyk_to_rgb(
            image,
            preserve_icc_profile=True,
        )

        source_profile_stream = (
            image_cms_profile.call_args_list[0].args[0]
        )

        assert source_profile_stream.getvalue() == source_profile_data
        assert isinstance(result, PreparedImage)
        assert result.image is converted_image
        assert result.image.mode == "RGB"
        assert result.image.size == image.size
        assert result.icc_profile == b"generated-srgb-profile"

        create_profile.assert_called_once_with("sRGB")
        profile_to_profile.assert_called_once_with(
            image,
            source_profile,
            target_profile,
            outputMode="RGB",
        )
        target_profile.tobytes.assert_called_once_with()


    def test_cmyk_conversion_omits_profile_when_preservation_is_disabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        image = Image.new(
            mode="CMYK",
            size=(10, 20),
            color=(0, 0, 0, 255),
        )
        image.info["icc_profile"] = b"valid-cmyk-profile"

        converted_image = Image.new(
            mode="RGB",
            size=image.size,
            color="black",
        )
        source_profile = Mock()
        target_profile = Mock()

        image_cms_profile = Mock(
            side_effect=[
                source_profile,
                target_profile,
            ]
        )
        create_profile = Mock(
            return_value=Mock(),
        )
        profile_to_profile = Mock(
            return_value=converted_image,
        )

        monkeypatch.setattr(
            ImageCms,
            "ImageCmsProfile",
            image_cms_profile,
        )
        monkeypatch.setattr(
            ImageCms,
            "createProfile",
            create_profile,
        )
        monkeypatch.setattr(
            ImageCms,
            "profileToProfile",
            profile_to_profile,
        )

        result = convert_cmyk_to_rgb(
            image,
            preserve_icc_profile=False,
        )

        assert isinstance(result, PreparedImage)
        assert result.image is converted_image
        assert result.image.mode == "RGB"
        assert result.image.size == image.size
        assert result.icc_profile is None

        profile_to_profile.assert_called_once_with(
            image,
            source_profile,
            target_profile,
            outputMode="RGB",
        )
        target_profile.tobytes.assert_not_called()

class TestPrepareImageForFormat:

    def test_rgb_image_is_already_compatible_with_jpeg(self) -> None:
        image = Image.new(
            mode="RGB",
            size=(10, 20),
            color="white",
        )

        result = prepare_image_for_format(
            image,
            output_format="JPEG",
            preserve_icc_profile=False,
        )

        assert isinstance(result, PreparedImage)
        assert result.image is image
        assert result.image.mode == "RGB"
        assert result.icc_profile is None


    def test_rgba_image_is_already_compatible_with_png(self) -> None:
        image = Image.new(
            mode="RGBA",
            size=(10, 20),
            color=(255, 0, 0, 128),
        )

        result = prepare_image_for_format(
            image,
            output_format="PNG",
            preserve_icc_profile=False,
        )

        assert isinstance(result, PreparedImage)
        assert result.image is image
        assert result.image.mode == "RGBA"
        assert result.image.getpixel((0, 0)) == (255, 0, 0, 128)
        assert result.icc_profile is None


    def test_rgba_image_is_flattened_onto_white_for_jpeg(self) -> None:
        image = Image.new(
            mode="RGBA",
            size=(3, 1),
        )
        image.putdata(
            [
                (255, 0, 0, 255),
                (255, 0, 0, 128),
                (255, 0, 0, 0),
            ]
        )

        result = prepare_image_for_format(
            image,
            output_format="JPEG",
            preserve_icc_profile=False,
        )

        assert isinstance(result, PreparedImage)
        assert result.image is not image
        assert result.image.mode == "RGB"
        assert result.image.size == image.size
        assert result.image.getpixel((0, 0)) == (255, 0, 0)
        assert result.image.getpixel((1, 0)) == (255, 127, 127)
        assert result.image.getpixel((2, 0)) == (255, 255, 255)
        assert result.icc_profile is None


    def test_unsupported_output_format_is_rejected(self) -> None:
        image = Image.new(
            mode="RGB",
            size=(10, 20),
            color="white",
        )
        output_format = "XML"
        expected_message = (
            f"Unsupported image format: {output_format!r}."
        )

        with pytest.raises(
            UnsupportedImageFormatError,
            match=exact_message(expected_message),
        ):
            prepare_image_for_format(
                image,
                output_format=output_format,
                preserve_icc_profile=False,
            )


    def test_rgba_image_is_already_compatible_with_webp(self) -> None:
        image = Image.new(
            mode="RGBA",
            size=(10, 20),
            color=(255, 0, 0, 128),
        )

        result = prepare_image_for_format(
            image,
            output_format="WEBP",
            preserve_icc_profile=False,
        )

        assert isinstance(result, PreparedImage)
        assert result.image is image
        assert result.image.mode == "RGBA"
        assert result.image.getpixel((0, 0)) == (255, 0, 0, 128)
        assert result.icc_profile is None


    def test_grayscale_image_is_already_compatible_with_jpeg(self) -> None:
        image = Image.new(
            mode="L",
            size=(10, 20),
            color=128,
        )

        result = prepare_image_for_format(
            image,
            output_format="JPEG",
            preserve_icc_profile=False,
        )

        assert result.image is image
        assert result.image.mode == "L"
        assert result.icc_profile is None


    def test_cmyk_image_is_converted_to_rgb_for_jpeg(self) -> None:
        image = Image.new(
            mode="CMYK",
            size=(10, 20),
            color=(0, 0, 0, 255),
        )

        result = prepare_image_for_format(
            image,
            output_format="JPEG",
            preserve_icc_profile=False,
        )

        assert isinstance(result, PreparedImage)
        assert result.image is not image
        assert result.image.mode == "RGB"
        assert result.image.size == image.size
        assert result.icc_profile is None


    def test_compatible_image_preserves_icc_profile_when_requested(
        self,
    ) -> None:
        image = Image.new(
            mode="RGB",
            size=(10, 20),
            color="white",
        )
        profile = ImageCms.ImageCmsProfile(
            ImageCms.createProfile("sRGB")
        )
        profile_data = profile.tobytes()
        image.info["icc_profile"] = profile_data

        result = prepare_image_for_format(
            image,
            output_format="JPEG",
            preserve_icc_profile=True,
        )

        assert isinstance(result, PreparedImage)
        assert result.image is image
        assert result.image.mode == "RGB"
        assert result.icc_profile == profile_data


    def test_compatible_image_omits_icc_profile_when_disabled(
        self,
    ) -> None:
        image = Image.new(
            mode="RGB",
            size=(10, 20),
            color="white",
        )
        profile = ImageCms.ImageCmsProfile(
            ImageCms.createProfile("sRGB")
        )
        image.info["icc_profile"] = profile.tobytes()

        result = prepare_image_for_format(
            image,
            output_format="JPEG",
            preserve_icc_profile=False,
        )

        assert isinstance(result, PreparedImage)
        assert result.image is image
        assert result.image.mode == "RGB"
        assert result.icc_profile is None


    def test_mode_conversion_does_not_preserve_source_icc_profile(
        self,
    ) -> None:
        image = Image.new(
            mode="RGBA",
            size=(10, 20),
            color=(255, 0, 0, 128),
        )
        profile = ImageCms.ImageCmsProfile(
            ImageCms.createProfile("sRGB")
        )
        image.info["icc_profile"] = profile.tobytes()

        result = prepare_image_for_format(
            image,
            output_format="JPEG",
            preserve_icc_profile=True,
        )

        assert isinstance(result, PreparedImage)
        assert result.image.mode == "RGB"
        assert result.image is not image
        assert result.icc_profile is None


    def test_jpg_alias_is_normalized_for_format_preparation(
        self,
    ) -> None:
        image = Image.new(
            mode="RGB",
            size=(10, 20),
            color="white",
        )

        result = prepare_image_for_format(
            image,
            output_format="jpg",
            preserve_icc_profile=False,
        )

        assert isinstance(result, PreparedImage)
        assert result.image is image
        assert result.image.mode == "RGB"
        assert result.icc_profile is None
