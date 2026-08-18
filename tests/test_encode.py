# SPDX-License-Identifier: GPL-3.0-or-later

import re
from collections.abc import Callable
from io import BytesIO
from unittest.mock import Mock

import pytest
from PIL import Image, ImageCms

import cbzfit.encode as encode_module
from cbzfit.decode import UnsupportedImageFormatError
from cbzfit.encode import (
    DEFAULT_JPEG_QUALITY,
    DEFAULT_PNG_COMPRESSION_LEVEL,
    DEFAULT_WEBP_METHOD,
    DEFAULT_WEBP_QUALITY,
    EncodedImage,
    EncoderOptions,
    encode_image,
    get_encoding_metadata,
    save_jpeg,
    save_png,
    save_webp,
)
from cbzfit.image import PreparedImage


def exact_message(message: str) -> str:
    """Return a regular expression that matches a complete error message."""
    return rf"^{re.escape(message)}$"


class TestEncoderOptions:

    def test_default_options_are_created(self) -> None:
        options = EncoderOptions()

        assert options.jpeg_quality == DEFAULT_JPEG_QUALITY
        assert options.jpeg_optimize is True
        assert options.jpeg_progressive is False

        assert (
            options.png_compress_level
            == DEFAULT_PNG_COMPRESSION_LEVEL
        )
        assert options.png_optimize is True

        assert options.webp_quality == DEFAULT_WEBP_QUALITY
        assert options.webp_method == DEFAULT_WEBP_METHOD
        assert options.webp_lossless is False

        assert options.preserve_icc_profile is True

    @pytest.mark.parametrize(
        ("arguments", "attribute", "expected_value"),
        [
            (
                {"jpeg_quality": 0},
                "jpeg_quality",
                0,
            ),
            (
                {"jpeg_quality": 95},
                "jpeg_quality",
                95,
            ),
            (
                {"png_compress_level": 0},
                "png_compress_level",
                0,
            ),
            (
                {"png_compress_level": 9},
                "png_compress_level",
                9,
            ),
            (
                {"webp_quality": 0},
                "webp_quality",
                0,
            ),
            (
                {"webp_quality": 100},
                "webp_quality",
                100,
            ),
            (
                {"webp_method": 0},
                "webp_method",
                0,
            ),
            (
                {"webp_method": 6},
                "webp_method",
                6,
            ),
        ],
    )
    def test_boundary_values_are_accepted(
        self,
        arguments: dict[str, int],
        attribute: str,
        expected_value: int,
    ) -> None:
        options = EncoderOptions(
            **arguments,
        )

        assert getattr(options, attribute) == expected_value

    @pytest.mark.parametrize(
        ("arguments", "expected_message"),
        [
            (
                {"jpeg_quality": -1},
                "JPEG quality must be between 0 and 95.",
            ),
            (
                {"jpeg_quality": 96},
                "JPEG quality must be between 0 and 95.",
            ),
            (
                {"png_compress_level": -1},
                "PNG compression level must be between 0 and 9.",
            ),
            (
                {"png_compress_level": 10},
                "PNG compression level must be between 0 and 9.",
            ),
            (
                {"webp_quality": -1},
                "WebP quality must be between 0 and 100.",
            ),
            (
                {"webp_quality": 101},
                "WebP quality must be between 0 and 100.",
            ),
            (
                {"webp_method": -1},
                "WebP method must be between 0 and 6.",
            ),
            (
                {"webp_method": 7},
                "WebP method must be between 0 and 6.",
            ),
        ],
    )
    def test_invalid_values_are_rejected(
        self,
        arguments: dict[str, int],
        expected_message: str,
    ) -> None:
        with pytest.raises(
            ValueError,
            match=exact_message(expected_message),
        ):
            EncoderOptions(
                **arguments,
            )

    def test_custom_boolean_options_are_retained(self) -> None:
        options = EncoderOptions(
            jpeg_optimize=False,
            jpeg_progressive=True,
            png_optimize=False,
            webp_lossless=True,
            preserve_icc_profile=False,
        )

        assert options.jpeg_optimize is False
        assert options.jpeg_progressive is True
        assert options.png_optimize is False
        assert options.webp_lossless is True
        assert options.preserve_icc_profile is False


class TestEncodedImage:

    @pytest.mark.parametrize(
        ("image_format", "expected_extension"),
        [
            ("JPEG", ".jpg"),
            ("PNG", ".png"),
            ("WEBP", ".webp"),
        ],
    )
    def test_preferred_extension_is_returned(
        self,
        image_format: str,
        expected_extension: str,
    ) -> None:
        encoded_image = EncodedImage(
            data=b"encoded-image-data",
            format=image_format,
        )

        assert encoded_image.extension == expected_extension

    def test_encoded_size_is_returned(self) -> None:
        encoded_image = EncodedImage(
            data=b"encoded-image-data",
            format="JPEG",
        )

        assert encoded_image.size == len(b"encoded-image-data")


class TestGetEncodingMetadata:

    def test_icc_profile_none_is_returned_as_encoding_metadata(
        self,
    ) -> None:
        image = Image.new(
            mode="RGB",
            size=(10, 20),
            color="white",
        )
        prepared_image = PreparedImage(
            image=image,
        )

        result = get_encoding_metadata(prepared_image)

        assert result == {
            "icc_profile": None,
        }

    def test_icc_profile_is_returned_as_encoding_metadata(
        self,
    ) -> None:
        image = Image.new(
            mode="RGB",
            size=(10, 20),
            color="white",
        )
        prepared_image = PreparedImage(
            image=image,
            icc_profile=b"embedded-icc-profile",
        )

        result = get_encoding_metadata(prepared_image)

        assert result == {
            "icc_profile": b"embedded-icc-profile",
        }


class TestSaveImage:

    @pytest.mark.parametrize(
        ("save_function", "format_options"),
        [
            (
                save_jpeg,
                {
                    "format": "JPEG",
                    "quality": DEFAULT_JPEG_QUALITY,
                    "optimize": True,
                    "progressive": False,
                },
            ),
            (
                save_png,
                {
                    "format": "PNG",
                    "compress_level": DEFAULT_PNG_COMPRESSION_LEVEL,
                    "optimize": True,
                },
            ),
            (
                save_webp,
                {
                    "format": "WEBP",
                    "quality": DEFAULT_WEBP_QUALITY,
                    "method": DEFAULT_WEBP_METHOD,
                    "lossless": False,
                },
            ),
        ],
    )
    def test_missing_icc_profile_is_explicitly_omitted(
        self,
        save_function: Callable[
            [Image.Image, BytesIO, EncoderOptions],
            None,
        ],
        format_options: dict[str, object],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source_image = Mock(spec=Image.Image)
        compatible_image = Mock(spec=Image.Image)
        destination = BytesIO()
        prepared_image = PreparedImage(
            image=compatible_image,
        )
        prepare_image = Mock(
            return_value=prepared_image,
        )

        monkeypatch.setattr(
            encode_module,
            "prepare_image_for_format",
            prepare_image,
        )

        save_function(
            source_image,
            destination,
            EncoderOptions(),
        )

        compatible_image.save.assert_called_once_with(
            destination,
            **format_options,
            icc_profile=None,
        )


class TestSaveJpeg:

    def test_image_is_prepared_and_saved_with_jpeg_options(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source_image = Mock(spec=Image.Image)
        compatible_image = Mock(spec=Image.Image)
        destination = BytesIO()
        options = EncoderOptions(
            jpeg_quality=90,
            jpeg_optimize=False,
            jpeg_progressive=True,
            preserve_icc_profile=True,
        )
        prepared_image = PreparedImage(
            image=compatible_image,
            icc_profile=b"prepared-icc-profile",
        )
        prepare_image = Mock(
            return_value=prepared_image,
        )

        monkeypatch.setattr(
            encode_module,
            "prepare_image_for_format",
            prepare_image,
        )

        save_jpeg(
            source_image,
            destination,
            options,
        )

        prepare_image.assert_called_once_with(
            source_image,
            output_format="JPEG",
            preserve_icc_profile=True,
        )
        compatible_image.save.assert_called_once_with(
            destination,
            format="JPEG",
            quality=90,
            optimize=False,
            progressive=True,
            icc_profile=b"prepared-icc-profile",
        )

    def test_jpeg_is_encoded_to_binary_stream(self) -> None:
        image = Image.new(
            mode="RGB",
            size=(10, 20),
            color="white",
        )
        destination = BytesIO()

        save_jpeg(
            image,
            destination,
            EncoderOptions(),
        )

        destination.seek(0)

        with Image.open(destination) as encoded_image:
            assert encoded_image.format == "JPEG"
            assert encoded_image.mode == "RGB"
            assert encoded_image.size == image.size


class TestSavePng:

    def test_image_is_prepared_and_saved_with_png_options(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source_image = Mock(spec=Image.Image)
        compatible_image = Mock(spec=Image.Image)
        destination = BytesIO()
        options = EncoderOptions(
            png_compress_level=6,
            png_optimize=False,
            preserve_icc_profile=False,
        )
        prepared_image = PreparedImage(
            image=compatible_image,
        )
        prepare_image = Mock(
            return_value=prepared_image,
        )

        monkeypatch.setattr(
            encode_module,
            "prepare_image_for_format",
            prepare_image,
        )

        save_png(
            source_image,
            destination,
            options,
        )

        prepare_image.assert_called_once_with(
            source_image,
            output_format="PNG",
            preserve_icc_profile=False,
        )
        compatible_image.save.assert_called_once_with(
            destination,
            format="PNG",
            compress_level=6,
            optimize=False,
            icc_profile=None,
        )

    def test_png_is_encoded_to_binary_stream(self) -> None:
        image = Image.new(
            mode="RGBA",
            size=(10, 20),
            color=(255, 0, 0, 128),
        )
        destination = BytesIO()

        save_png(
            image,
            destination,
            EncoderOptions(),
        )

        destination.seek(0)

        with Image.open(destination) as encoded_image:
            assert encoded_image.format == "PNG"
            assert encoded_image.mode == "RGBA"
            assert encoded_image.size == image.size
            assert encoded_image.getpixel((0, 0)) == (
                255,
                0,
                0,
                128,
            )


class TestSaveWebp:

    def test_image_is_prepared_and_saved_with_webp_options(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source_image = Mock(spec=Image.Image)
        compatible_image = Mock(spec=Image.Image)
        destination = BytesIO()
        options = EncoderOptions(
            webp_quality=90,
            webp_method=6,
            webp_lossless=True,
            preserve_icc_profile=True,
        )
        prepared_image = PreparedImage(
            image=compatible_image,
            icc_profile=b"prepared-icc-profile",
        )
        prepare_image = Mock(
            return_value=prepared_image,
        )

        monkeypatch.setattr(
            encode_module,
            "prepare_image_for_format",
            prepare_image,
        )

        save_webp(
            source_image,
            destination,
            options,
        )

        prepare_image.assert_called_once_with(
            source_image,
            output_format="WEBP",
            preserve_icc_profile=True,
        )
        compatible_image.save.assert_called_once_with(
            destination,
            format="WEBP",
            quality=90,
            method=6,
            lossless=True,
            icc_profile=b"prepared-icc-profile",
        )

    def test_webp_is_encoded_to_binary_stream(self) -> None:
        image = Image.new(
            mode="RGBA",
            size=(10, 20),
            color=(255, 0, 0, 128),
        )
        destination = BytesIO()

        save_webp(
            image,
            destination,
            EncoderOptions(),
        )

        destination.seek(0)

        with Image.open(destination) as encoded_image:
            assert encoded_image.format == "WEBP"
            assert encoded_image.mode == "RGBA"
            assert encoded_image.size == image.size
            assert encoded_image.getpixel((0, 0))[3] == 128


class TestEncodeImage:

    @pytest.mark.parametrize(
        ("output_format", "expected_format", "expected_extension"),
        [
            ("JPEG", "JPEG", ".jpg"),
            ("jpg", "JPEG", ".jpg"),
            ("PNG", "PNG", ".png"),
            ("png", "PNG", ".png"),
            ("WEBP", "WEBP", ".webp"),
            ("webp", "WEBP", ".webp"),
        ],
    )
    def test_image_is_encoded_in_selected_format(
        self,
        output_format: str,
        expected_format: str,
        expected_extension: str,
    ) -> None:
        image = Image.new(
            mode="RGB",
            size=(10, 20),
            color="white",
        )

        result = encode_image(
            image,
            output_format=output_format,
        )

        assert isinstance(result, EncodedImage)
        assert result.format == expected_format
        assert result.extension == expected_extension
        assert result.size == len(result.data)
        assert result.size > 0

        with Image.open(BytesIO(result.data)) as encoded_image:
            assert encoded_image.format == expected_format
            assert encoded_image.size == image.size

    def test_custom_options_are_forwarded_to_selected_encoder(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        image = Mock(spec=Image.Image)
        options = EncoderOptions(
            jpeg_quality=90,
        )
        save = Mock(
            side_effect=lambda source_image, destination, encoder_options: (
                destination.write(b"encoded-data")
            )
        )

        monkeypatch.setattr(
            encode_module,
            "save_jpeg",
            save,
        )

        result = encode_image(
            image,
            output_format="JPEG",
            options=options,
        )

        assert result == EncodedImage(
            data=b"encoded-data",
            format="JPEG",
        )

        called_image, called_destination, called_options = (
            save.call_args.args
        )

        assert called_image is image
        assert isinstance(called_destination, BytesIO)
        assert called_options is options

    @pytest.mark.parametrize(
        ("output_format", "selected_encoder"),
        [
            ("JPEG", "save_jpeg"),
            ("PNG", "save_png"),
            ("WEBP", "save_webp"),
        ],
    )
    def test_correct_encoder_is_selected(
        self,
        output_format: str,
        selected_encoder: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        image = Mock(spec=Image.Image)

        encoders = {
            "save_jpeg": Mock(),
            "save_png": Mock(),
            "save_webp": Mock(),
        }

        for encoder_name, encoder in encoders.items():
            encoder.side_effect = (
                lambda source_image, destination, options: (
                    destination.write(b"encoded-data")
                )
            )
            monkeypatch.setattr(
                encode_module,
                encoder_name,
                encoder,
            )

        result = encode_image(
            image,
            output_format=output_format,
        )

        assert result.data == b"encoded-data"
        assert result.format == output_format

        encoders[selected_encoder].assert_called_once()

        for encoder_name, encoder in encoders.items():
            if encoder_name != selected_encoder:
                encoder.assert_not_called()

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
            encode_image(
                image,
                output_format=output_format,
            )

    def test_recognized_but_unimplemented_format_is_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        image = Mock(spec=Image.Image)
        output_format = "AVIF"
        expected_message = (
            f"Unsupported image format: {output_format!r}."
        )

        monkeypatch.setattr(
            encode_module,
            "normalize_image_format",
            lambda image_format: "AVIF",
        )

        with pytest.raises(
            UnsupportedImageFormatError,
            match=exact_message(expected_message),
        ):
            encode_image(
                image,
                output_format=output_format,
            )


    @pytest.mark.parametrize(
        "output_format",
        [
            "JPEG",
            "PNG",
            "WEBP",
        ],
    )
    def test_icc_profile_is_embedded_in_encoded_output(
        self,
        output_format: str,
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

        result = encode_image(
            image,
            output_format=output_format,
            options=EncoderOptions(
                preserve_icc_profile=True,
            ),
        )

        with Image.open(BytesIO(result.data)) as encoded_image:
            assert encoded_image.info.get("icc_profile") == profile_data


    @pytest.mark.parametrize(
        "output_format",
        [
            "JPEG",
            "PNG",
            "WEBP",
        ],
    )
    def test_icc_profile_is_omitted_when_preservation_is_disabled(
        self,
        output_format: str,
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

        result = encode_image(
            image,
            output_format=output_format,
            options=EncoderOptions(
                preserve_icc_profile=False,
            ),
        )

        with Image.open(BytesIO(result.data)) as encoded_image:
            assert encoded_image.info.get("icc_profile") is None
