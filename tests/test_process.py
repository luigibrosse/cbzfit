# SPDX-License-Identifier: GPL-3.0-or-later

import re
from io import BytesIO
from unittest.mock import MagicMock, Mock

import pytest
from PIL import Image, UnidentifiedImageError

import cbzfit.process as process_module
from cbzfit.decode import (
    UnsupportedImageContentError,
    UnsupportedImageFormatError,
)
from cbzfit.encode import EncodedImage, EncoderOptions
from cbzfit.process import (
    ImageProcessingOptions,
    ProcessedImage,
    process_image_data,
)


def exact_message(message: str) -> str:
    """Return a regular expression that matches a complete error message."""
    return rf"^{re.escape(message)}$"


def create_encoded_image(
    image_format: str,
    *,
    mode: str = "RGB",
    size: tuple[int, int] = (10, 20),
    color: str | int | tuple[int, ...] = "white",
) -> bytes:
    """Create encoded in-memory image data."""
    image = Image.new(
        mode=mode,
        size=size,
        color=color,
    )
    output = BytesIO()

    try:
        image.save(
            output,
            format=image_format,
        )
    finally:
        image.close()

    return output.getvalue()


def open_encoded_image(data: bytes) -> Image.Image:
    """Open and fully load encoded image data."""
    image = Image.open(
        BytesIO(data),
    )
    image.load()

    return image


def create_mock_opened_image() -> MagicMock:
    """Create a Pillow image mock usable as a context manager."""
    image = MagicMock(spec=Image.Image)
    image.__enter__.return_value = image
    image.__exit__.return_value = None
    image.format = "JPEG"
    image.is_animated = False
    image.size = (10, 20)

    return image


class TestImageProcessingOptions:

    def test_required_screen_size_is_retained(self) -> None:
        options = ImageProcessingOptions(
            portrait_screen_size=(1404, 1872),
        )

        assert options.portrait_screen_size == (1404, 1872)

    def test_default_options_are_created(self) -> None:
        options = ImageProcessingOptions(
            portrait_screen_size=(1404, 1872),
        )

        assert options.use_landscape_display is True
        assert options.allow_upscale is False
        assert options.encoder_options == EncoderOptions()

    def test_custom_options_are_retained(self) -> None:
        encoder_options = EncoderOptions(
            jpeg_quality=90,
            preserve_icc_profile=False,
        )
        options = ImageProcessingOptions(
            portrait_screen_size=(1404, 1872),
            use_landscape_display=False,
            allow_upscale=True,
            encoder_options=encoder_options,
        )

        assert options.portrait_screen_size == (1404, 1872)
        assert options.use_landscape_display is False
        assert options.allow_upscale is True
        assert options.encoder_options is encoder_options

    def test_default_encoder_options_are_not_shared(self) -> None:
        first_options = ImageProcessingOptions(
            portrait_screen_size=(1404, 1872),
        )
        second_options = ImageProcessingOptions(
            portrait_screen_size=(1404, 1872),
        )

        assert (
            first_options.encoder_options
            is not second_options.encoder_options
        )


class TestProcessedImage:

    def test_processed_image_fields_are_retained(self) -> None:
        result = ProcessedImage(
            data=b"processed-image-data",
            format="PNG",
            transformed=True,
        )

        assert result.data == b"processed-image-data"
        assert result.format == "PNG"
        assert result.transformed is True

    def test_processed_image_size_is_returned(self) -> None:
        result = ProcessedImage(
            data=b"processed-image-data",
            format="JPEG",
            transformed=False,
        )

        assert result.size == len(b"processed-image-data")


class TestProcessImageData:

    @pytest.mark.parametrize(
        ("image_format", "filename"),
        [
            ("JPEG", "001.jpg"),
            ("PNG", "001.png"),
            ("WEBP", "001.webp"),
        ],
    )
    def test_image_that_already_fits_returns_original_bytes(
        self,
        image_format: str,
        filename: str,
    ) -> None:
        source_data = create_encoded_image(
            image_format,
            size=(10, 20),
        )

        result = process_image_data(
            source_data,
            filename,
            options=ImageProcessingOptions(
                portrait_screen_size=(10, 20),
            ),
        )

        assert result.data is source_data
        assert result.data == source_data
        assert result.format == image_format
        assert result.transformed is False
        assert result.size == len(source_data)

    def test_large_jpeg_is_resized_and_reencoded(self) -> None:
        source_data = create_encoded_image(
            "JPEG",
            size=(20, 40),
        )

        result = process_image_data(
            source_data,
            "001.jpg",
            options=ImageProcessingOptions(
                portrait_screen_size=(10, 20),
            ),
        )

        assert result.data != source_data
        assert result.format == "JPEG"
        assert result.transformed is True

        with open_encoded_image(result.data) as image:
            assert image.format == "JPEG"
            assert image.mode == "RGB"
            assert image.size == (10, 20)

    def test_large_png_is_resized_with_transparency_preserved(
        self,
    ) -> None:
        source_data = create_encoded_image(
            "PNG",
            mode="RGBA",
            size=(20, 40),
            color=(255, 0, 0, 128),
        )

        result = process_image_data(
            source_data,
            "001.png",
            options=ImageProcessingOptions(
                portrait_screen_size=(10, 20),
            ),
        )

        assert result.data != source_data
        assert result.format == "PNG"
        assert result.transformed is True

        with open_encoded_image(result.data) as image:
            assert image.format == "PNG"
            assert image.mode == "RGBA"
            assert image.size == (10, 20)
            assert image.getpixel((0, 0)) == (
                255,
                0,
                0,
                128,
            )

    def test_large_webp_is_resized_and_reencoded(self) -> None:
        source_data = create_encoded_image(
            "WEBP",
            mode="RGBA",
            size=(20, 40),
            color=(255, 0, 0, 128),
        )

        result = process_image_data(
            source_data,
            "001.webp",
            options=ImageProcessingOptions(
                portrait_screen_size=(10, 20),
                encoder_options=EncoderOptions(
                    webp_lossless=True,
                ),
            ),
        )

        assert result.data != source_data
        assert result.format == "WEBP"
        assert result.transformed is True

        with open_encoded_image(result.data) as image:
            assert image.format == "WEBP"
            assert image.mode == "RGBA"
            assert image.size == (10, 20)
            assert image.getpixel((0, 0))[3] == 128

    @pytest.mark.parametrize(
        ("use_landscape_display", "expected_size"),
        [
            (True, (20, 10)),
            (False, (10, 5)),
        ],
    )
    def test_landscape_display_behavior_is_applied(
        self,
        use_landscape_display: bool,
        expected_size: tuple[int, int],
    ) -> None:
        source_data = create_encoded_image(
            "PNG",
            size=(40, 20),
        )

        result = process_image_data(
            source_data,
            "001.png",
            options=ImageProcessingOptions(
                portrait_screen_size=(10, 20),
                use_landscape_display=use_landscape_display,
            ),
        )

        assert result.transformed is True

        with open_encoded_image(result.data) as image:
            assert image.size == expected_size

    def test_small_image_is_not_upscaled_by_default(self) -> None:
        source_data = create_encoded_image(
            "PNG",
            size=(5, 10),
        )

        result = process_image_data(
            source_data,
            "001.png",
            options=ImageProcessingOptions(
                portrait_screen_size=(10, 20),
            ),
        )

        assert result.data is source_data
        assert result.format == "PNG"
        assert result.transformed is False

    def test_small_image_is_upscaled_when_enabled(self) -> None:
        source_data = create_encoded_image(
            "PNG",
            size=(5, 10),
        )

        result = process_image_data(
            source_data,
            "001.png",
            options=ImageProcessingOptions(
                portrait_screen_size=(10, 20),
                allow_upscale=True,
            ),
        )

        assert result.format == "PNG"
        assert result.transformed is True

        with open_encoded_image(result.data) as image:
            assert image.size == (10, 20)

    @pytest.mark.parametrize(
        ("image_format", "filename", "expected_format"),
        [
            ("JPEG", "001.jpeg", "JPEG"),
            ("JPEG", "Chapter 01/001.JPG", "JPEG"),
            ("PNG", "Chapter 01/001.PNG", "PNG"),
            ("WEBP", "Chapter 01/001.WebP", "WEBP"),
        ],
    )
    def test_source_canonical_format_is_retained(
        self,
        image_format: str,
        filename: str,
        expected_format: str,
    ) -> None:
        source_data = create_encoded_image(
            image_format,
            size=(20, 40),
        )

        result = process_image_data(
            source_data,
            filename,
            options=ImageProcessingOptions(
                portrait_screen_size=(10, 20),
            ),
        )

        assert result.format == expected_format
        assert result.transformed is True

        with open_encoded_image(result.data) as image:
            assert image.format == expected_format

    @pytest.mark.parametrize(
        ("image_format", "filename", "extension_format"),
        [
            ("JPEG", "001.png", "PNG"),
            ("PNG", "001.jpg", "JPEG"),
            ("WEBP", "001.png", "PNG"),
        ],
    )
    def test_content_and_filename_mismatch_is_rejected(
        self,
        image_format: str,
        filename: str,
        extension_format: str,
    ) -> None:
        source_data = create_encoded_image(
            image_format,
        )
        expected_message = (
            f"Image content format {image_format!r} does not match the "
            f"filename extension for {filename!r}, which indicates "
            f"{extension_format!r}."
        )

        with pytest.raises(
            UnsupportedImageFormatError,
            match=exact_message(expected_message),
        ):
            process_image_data(
                source_data,
                filename,
                options=ImageProcessingOptions(
                    portrait_screen_size=(10, 20),
                ),
            )

    def test_unsupported_filename_extension_is_rejected(
        self,
    ) -> None:
        source_data = create_encoded_image(
            "JPEG",
        )
        filename = "001.gif"
        expected_message = (
            f"Unsupported image filename extension: {filename!r}."
        )

        with pytest.raises(
            UnsupportedImageFormatError,
            match=exact_message(expected_message),
        ):
            process_image_data(
                source_data,
                filename,
                options=ImageProcessingOptions(
                    portrait_screen_size=(10, 20),
                ),
            )

    def test_invalid_image_data_is_rejected(self) -> None:
        filename = "001.jpg"
        expected_message = (
            f"Image data could not be decoded: {filename!r}."
        )

        with pytest.raises(
            UnsupportedImageContentError,
            match=exact_message(expected_message),
        ) as exception_info:
            process_image_data(
                b"not-an-image",
                filename,
                options=ImageProcessingOptions(
                    portrait_screen_size=(10, 20),
                ),
            )

        assert isinstance(
            exception_info.value.__cause__,
            UnidentifiedImageError,
        )

    def test_image_open_os_error_is_wrapped(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        filename = "001.jpg"
        error = OSError("Image header could not be read")
        expected_message = (
            f"Image data could not be decoded: {filename!r}."
        )

        open_image = Mock(
            side_effect=error,
        )
        monkeypatch.setattr(
            process_module.Image,
            "open",
            open_image,
        )

        with pytest.raises(
            UnsupportedImageContentError,
            match=exact_message(expected_message),
        ) as exception_info:
            process_image_data(
                b"encoded-image-data",
                filename,
                options=ImageProcessingOptions(
                    portrait_screen_size=(10, 20),
                ),
            )

        assert exception_info.value.__cause__ is error
        open_image.assert_called_once()

    def test_image_decompression_bomb_is_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        filename = "001.jpg"
        error = Image.DecompressionBombError(
            "Image size exceeds limit"
        )
        expected_message = (
            "Image dimensions exceed the permitted limit: "
            f"{filename!r}."
        )

        monkeypatch.setattr(
            process_module.Image,
            "open",
            Mock(side_effect=error),
        )

        with pytest.raises(
            UnsupportedImageContentError,
            match=exact_message(expected_message),
        ) as exception_info:
            process_image_data(
                b"encoded-image-data",
                filename,
                options=ImageProcessingOptions(
                    portrait_screen_size=(10, 20),
                ),
            )

        assert exception_info.value.__cause__ is error

    def test_pixel_decoding_failure_is_wrapped(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        filename = "001.jpg"
        image = create_mock_opened_image()
        error = OSError("Truncated image data")
        image.load.side_effect = error
        expected_message = (
            f"Image data could not be decoded: {filename!r}."
        )

        monkeypatch.setattr(
            process_module.Image,
            "open",
            Mock(return_value=image),
        )

        with pytest.raises(
            UnsupportedImageContentError,
            match=exact_message(expected_message),
        ) as exception_info:
            process_image_data(
                b"encoded-image-data",
                filename,
                options=ImageProcessingOptions(
                    portrait_screen_size=(10, 20),
                ),
            )

        assert exception_info.value.__cause__ is error
        image.__exit__.assert_called_once()

    def test_decompression_bomb_during_pixel_loading_is_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        filename = "001.jpg"
        image = create_mock_opened_image()
        error = Image.DecompressionBombError(
            "Image size exceeds limit"
        )
        image.load.side_effect = error
        expected_message = (
            "Image dimensions exceed the permitted limit: "
            f"{filename!r}."
        )

        monkeypatch.setattr(
            process_module.Image,
            "open",
            Mock(return_value=image),
        )

        with pytest.raises(
            UnsupportedImageContentError,
            match=exact_message(expected_message),
        ) as exception_info:
            process_image_data(
                b"encoded-image-data",
                filename,
                options=ImageProcessingOptions(
                    portrait_screen_size=(10, 20),
                ),
            )

        assert exception_info.value.__cause__ is error
        image.__exit__.assert_called_once()

    def test_animated_image_is_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        filename = "001.png"
        image = create_mock_opened_image()
        image.format = "PNG"
        image.is_animated = True
        expected_message = (
            f"Animated images are not supported: {filename!r}."
        )

        monkeypatch.setattr(
            process_module.Image,
            "open",
            Mock(return_value=image),
        )

        with pytest.raises(
            UnsupportedImageContentError,
            match=exact_message(expected_message),
        ):
            process_image_data(
                b"animated-image-data",
                filename,
                options=ImageProcessingOptions(
                    portrait_screen_size=(10, 20),
                ),
            )

        image.load.assert_not_called()
        image.__exit__.assert_called_once()

    def test_resize_options_are_forwarded(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source_data = create_encoded_image(
            "JPEG",
        )
        resize_image = Mock(
            side_effect=lambda **arguments: arguments["image"]
        )

        monkeypatch.setattr(
            process_module,
            "resize_for_display",
            resize_image,
        )

        result = process_image_data(
            source_data,
            "001.jpg",
            options=ImageProcessingOptions(
                portrait_screen_size=(1404, 1872),
                use_landscape_display=False,
                allow_upscale=True,
            ),
        )

        assert result.data is source_data
        assert result.transformed is False

        source_image = resize_image.call_args.kwargs["image"]

        resize_image.assert_called_once_with(
            image=source_image,
            portrait_screen_size=(1404, 1872),
            use_landscape_display=False,
            allow_upscale=True,
        )

    def test_encoder_options_are_forwarded(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source_data = create_encoded_image(
            "JPEG",
        )
        resized_image = Mock(spec=Image.Image)
        encoder_options = EncoderOptions(
            jpeg_quality=90,
            preserve_icc_profile=False,
        )
        encoded_image = EncodedImage(
            data=b"resized-image-data",
            format="JPEG",
        )
        resize = Mock(
            return_value=resized_image,
        )
        encode = Mock(
            return_value=encoded_image,
        )

        monkeypatch.setattr(
            process_module,
            "resize_for_display",
            resize,
        )
        monkeypatch.setattr(
            process_module,
            "encode_image",
            encode,
        )

        result = process_image_data(
            source_data,
            "001.jpg",
            options=ImageProcessingOptions(
                portrait_screen_size=(10, 20),
                encoder_options=encoder_options,
            ),
        )

        encode.assert_called_once_with(
            resized_image,
            output_format="JPEG",
            options=encoder_options,
        )
        assert result == ProcessedImage(
            data=b"resized-image-data",
            format="JPEG",
            transformed=True,
        )
        resized_image.close.assert_called_once_with()

    def test_resized_image_is_closed_when_encoding_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source_data = create_encoded_image(
            "JPEG",
        )
        resized_image = Mock(spec=Image.Image)
        error = OSError("Encoding failed")

        monkeypatch.setattr(
            process_module,
            "resize_for_display",
            Mock(return_value=resized_image),
        )
        monkeypatch.setattr(
            process_module,
            "encode_image",
            Mock(side_effect=error),
        )

        with pytest.raises(
            OSError,
            match=exact_message("Encoding failed"),
        ) as exception_info:
            process_image_data(
                source_data,
                "001.jpg",
                options=ImageProcessingOptions(
                    portrait_screen_size=(10, 20),
                ),
            )

        assert exception_info.value is error
        resized_image.close.assert_called_once_with()

    def test_encoder_is_not_called_for_unchanged_image(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source_data = create_encoded_image(
            "JPEG",
            size=(10, 20),
        )
        encode = Mock()

        monkeypatch.setattr(
            process_module,
            "encode_image",
            encode,
        )

        result = process_image_data(
            source_data,
            "001.jpg",
            options=ImageProcessingOptions(
                portrait_screen_size=(10, 20),
            ),
        )

        assert result.data is source_data
        assert result.transformed is False
        encode.assert_not_called()
