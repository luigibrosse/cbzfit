# SPDX-License-Identifier: GPL-3.0-or-later

import re
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, Mock
from zipfile import (
    ZIP_DEFLATED,
    ZIP_STORED,
    BadZipFile,
    ZipFile,
)

import pytest
from PIL import Image, UnidentifiedImageError

import cbzfit.process as process_module
from cbzfit.archive import (
    DEFAULT_MAX_ARCHIVE_FILES,
    ArchivePathLimits,
    ArchiveReadLimits,
    ArchiveReadState,
    InvalidArchiveError,
    MemberDateTimeMode,
    MemberDateTimePolicy,
)
from cbzfit.decode import (
    UnsupportedImageContentError,
    UnsupportedImageFormatError,
)
from cbzfit.encode import EncodedImage, EncoderOptions
from cbzfit.process import (
    ArchiveTransformationOptions,
    ArchiveTransformationResult,
    DestinationConflictMode,
    ImageProcessingOptions,
    OutputVerificationMode,
    ProcessedImage,
    process_archive_file,
    process_image_data,
    transform_archive_contents,
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


def _read_zip_member(
    archive_stream: BytesIO,
    filename: str,
) -> bytes:
    """Read one member without changing the caller's final stream position."""
    position = archive_stream.tell()
    archive_stream.seek(0)
    try:
        with ZipFile(archive_stream, mode="r") as archive:
            return archive.read(filename)
    finally:
        archive_stream.seek(position)


def create_mock_opened_image() -> MagicMock:
    """Create a Pillow image mock usable as a context manager."""
    image = MagicMock(spec=Image.Image)
    image.__enter__.return_value = image
    image.__exit__.return_value = None
    image.format = "JPEG"
    image.is_animated = False
    image.size = (10, 20)

    return image


def create_archive_file(
    archive_path: Path,
    members: list[tuple[str, bytes]],
) -> None:
    """Create a ZIP archive at the selected path."""
    with ZipFile(archive_path, mode="w") as archive:
        for filename, data in members:
            archive.writestr(filename, data)


def temporary_archive_paths(destination_path: Path) -> list[Path]:
    """Return temporary archive paths associated with a destination."""
    return list(
        destination_path.parent.glob(
            f".{destination_path.name}.*.tmp"
        )
    )


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


class TestArchiveTransformationOptions:
    def test_default_options_are_created(self) -> None:
        image_options = ImageProcessingOptions(
            portrait_screen_size=(1404, 1872),
        )

        options = ArchiveTransformationOptions(
            image_options=image_options,
        )

        assert options.image_options is image_options
        assert options.max_files == DEFAULT_MAX_ARCHIVE_FILES
        assert options.read_limits == ArchiveReadLimits()
        assert options.path_limits == ArchivePathLimits()
        assert options.date_time_policy == MemberDateTimePolicy()

    def test_default_nested_options_are_not_shared(self) -> None:
        image_options = ImageProcessingOptions(
            portrait_screen_size=(1404, 1872),
        )

        first = ArchiveTransformationOptions(image_options=image_options)
        second = ArchiveTransformationOptions(image_options=image_options)

        assert first.read_limits is not second.read_limits
        assert first.path_limits is not second.path_limits
        assert first.date_time_policy is not second.date_time_policy

    def test_custom_options_are_retained(self) -> None:
        image_options = ImageProcessingOptions(
            portrait_screen_size=(1404, 1872),
        )
        read_limits = ArchiveReadLimits(
            max_file_size=100,
            max_total_size=200,
        )
        path_limits = ArchivePathLimits(
            max_path_length=100,
            max_component_length=50,
        )
        date_time_policy = MemberDateTimePolicy(
            mode=MemberDateTimeMode.FIXED,
            fixed_date_time=(2000, 1, 2, 3, 4, 6),
        )

        options = ArchiveTransformationOptions(
            image_options=image_options,
            max_files=10,
            read_limits=read_limits,
            path_limits=path_limits,
            date_time_policy=date_time_policy,
        )

        assert options.max_files == 10
        assert options.read_limits is read_limits
        assert options.path_limits is path_limits
        assert options.date_time_policy is date_time_policy

    @pytest.mark.parametrize("max_files", [0, -1])
    def test_invalid_max_files_is_rejected(self, max_files: int) -> None:
        expected_message = "Maximum file count must be a positive integer."

        with pytest.raises(
            ValueError,
            match=exact_message(expected_message),
        ):
            ArchiveTransformationOptions(
                image_options=ImageProcessingOptions(
                    portrait_screen_size=(1404, 1872),
                ),
                max_files=max_files,
            )


class TestArchiveTransformationResult:
    def test_result_fields_are_retained(self) -> None:
        result = ArchiveTransformationResult(
            total_file_members=4,
            image_members=3,
            transformed_images=2,
            unchanged_images=1,
            copied_other_members=1,
            input_uncompressed_size=1_000,
            output_uncompressed_size=600,
        )

        assert result.total_file_members == 4
        assert result.image_members == 3
        assert result.transformed_images == 2
        assert result.unchanged_images == 1
        assert result.copied_other_members == 1
        assert result.input_uncompressed_size == 1_000
        assert result.output_uncompressed_size == 600


class TestOutputVerificationMode:
    def test_supported_modes_have_expected_values(self) -> None:
        assert OutputVerificationMode.NONE == "none"
        assert OutputVerificationMode.STRUCTURE == "structure"
        assert OutputVerificationMode.CRC == "crc"


class TestDestinationConflictMode:
    def test_supported_modes_have_expected_values(self) -> None:
        assert DestinationConflictMode.ERROR == "error"
        assert DestinationConflictMode.REPLACE == "replace"


class TestValidateDestinationConflictMode:
    @pytest.mark.parametrize("mode", list(DestinationConflictMode))
    def test_supported_mode_is_accepted(
        self,
        mode: DestinationConflictMode,
    ) -> None:
        process_module._validate_destination_conflict_mode(mode)

    @pytest.mark.parametrize(
        ("mode", "type_name"),
        [
            ("error", "str"),
            (None, "NoneType"),
            (1, "int"),
        ],
    )
    def test_invalid_mode_type_is_rejected(
        self,
        mode: object,
        type_name: str,
    ) -> None:
        expected_message = (
            "Destination conflict mode must be a "
            f"DestinationConflictMode, not {type_name}."
        )

        with pytest.raises(
            TypeError,
            match=exact_message(expected_message),
        ):
            process_module._validate_destination_conflict_mode(mode)


class TestPublishArchive:
    def test_error_mode_creates_destination_without_replacement(
        self,
        tmp_path: Path,
    ) -> None:
        temporary_path = tmp_path / ".output.cbz.temporary.tmp"
        destination_path = tmp_path / "output.cbz"
        archive_data = b"completed archive"
        temporary_path.write_bytes(archive_data)

        process_module._publish_archive(
            temporary_path,
            destination_path,
            conflict_mode=DestinationConflictMode.ERROR,
        )

        assert destination_path.read_bytes() == archive_data
        assert not temporary_path.exists()

    def test_error_mode_preserves_destination_created_concurrently(
        self,
        tmp_path: Path,
    ) -> None:
        temporary_path = tmp_path / ".output.cbz.temporary.tmp"
        destination_path = tmp_path / "output.cbz"
        temporary_data = b"completed archive"
        existing_data = b"concurrent destination"
        temporary_path.write_bytes(temporary_data)
        destination_path.write_bytes(existing_data)
        expected_message = (
            f"Destination archive already exists: {destination_path}."
        )

        with pytest.raises(
            FileExistsError,
            match=exact_message(expected_message),
        ) as exception_info:
            process_module._publish_archive(
                temporary_path,
                destination_path,
                conflict_mode=DestinationConflictMode.ERROR,
            )

        assert isinstance(exception_info.value.__cause__, FileExistsError)
        assert destination_path.read_bytes() == existing_data
        assert temporary_path.read_bytes() == temporary_data

    def test_error_mode_suppresses_temporary_unlink_failure_after_linking(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        temporary_path = tmp_path / ".output.cbz.temporary.tmp"
        destination_path = tmp_path / "output.cbz"
        archive_data = b"completed archive"
        temporary_path.write_bytes(archive_data)
        error = OSError("Temporary link removal failed")
        unlink = Mock(side_effect=error)

        with monkeypatch.context() as unlink_patch:
            unlink_patch.setattr(process_module.Path, "unlink", unlink)
            process_module._publish_archive(
                temporary_path,
                destination_path,
                conflict_mode=DestinationConflictMode.ERROR,
            )

        assert destination_path.read_bytes() == archive_data
        assert temporary_path.exists()
        unlink.assert_called_once_with()

    def test_replace_mode_atomically_replaces_existing_destination(
        self,
        tmp_path: Path,
    ) -> None:
        temporary_path = tmp_path / ".output.cbz.temporary.tmp"
        destination_path = tmp_path / "output.cbz"
        temporary_path.write_bytes(b"replacement archive")
        destination_path.write_bytes(b"existing archive")

        process_module._publish_archive(
            temporary_path,
            destination_path,
            conflict_mode=DestinationConflictMode.REPLACE,
        )

        assert destination_path.read_bytes() == b"replacement archive"
        assert not temporary_path.exists()

    def test_invalid_mode_is_rejected_before_publication(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        temporary_path = tmp_path / ".output.cbz.temporary.tmp"
        destination_path = tmp_path / "output.cbz"
        temporary_path.write_bytes(b"completed archive")
        link = Mock()
        replace = Mock()
        monkeypatch.setattr(process_module.os, "link", link)
        monkeypatch.setattr(process_module.os, "replace", replace)
        expected_message = (
            "Destination conflict mode must be a "
            "DestinationConflictMode, not str."
        )

        with pytest.raises(
            TypeError,
            match=exact_message(expected_message),
        ):
            process_module._publish_archive(
                temporary_path,
                destination_path,
                conflict_mode="error",
            )

        link.assert_not_called()
        replace.assert_not_called()
        assert temporary_path.exists()
        assert not destination_path.exists()


class TestValidateOutputVerificationMode:
    @pytest.mark.parametrize("mode", list(OutputVerificationMode))
    def test_supported_mode_is_accepted(
        self,
        mode: OutputVerificationMode,
    ) -> None:
        process_module._validate_output_verification_mode(mode)

    @pytest.mark.parametrize(
        ("mode", "type_name"),
        [
            ("structure", "str"),
            (None, "NoneType"),
            (1, "int"),
        ],
    )
    def test_invalid_mode_type_is_rejected(
        self,
        mode: object,
        type_name: str,
    ) -> None:
        expected_message = (
            "Output verification mode must be an "
            f"OutputVerificationMode, not {type_name}."
        )

        with pytest.raises(
            TypeError,
            match=exact_message(expected_message),
        ):
            process_module._validate_output_verification_mode(mode)


class TestVerifyOutputArchive:
    def test_none_mode_does_not_open_archive(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        archive_path = tmp_path / "output.cbz"
        open_archive = Mock()
        monkeypatch.setattr(
            process_module,
            "ZipFile",
            open_archive,
        )

        process_module._verify_output_archive(
            archive_path,
            mode=OutputVerificationMode.NONE,
        )

        open_archive.assert_not_called()

    def test_structure_mode_reopens_and_parses_archive(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        archive_path = tmp_path / "output.cbz"
        archive = MagicMock(spec=ZipFile)
        archive_context = MagicMock()
        archive_context.__enter__.return_value = archive
        open_archive = Mock(return_value=archive_context)
        monkeypatch.setattr(
            process_module,
            "ZipFile",
            open_archive,
        )

        process_module._verify_output_archive(
            archive_path,
            mode=OutputVerificationMode.STRUCTURE,
        )

        open_archive.assert_called_once_with(archive_path, mode="r")
        archive.infolist.assert_called_once_with()
        archive_context.__exit__.assert_called_once()

    def test_crc_mode_delegates_integrity_check(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        archive_path = tmp_path / "output.cbz"
        archive = MagicMock(spec=ZipFile)
        archive_context = MagicMock()
        archive_context.__enter__.return_value = archive
        open_archive = Mock(return_value=archive_context)
        verify_integrity = Mock()
        monkeypatch.setattr(
            process_module,
            "ZipFile",
            open_archive,
        )
        monkeypatch.setattr(
            process_module,
            "verify_archive_integrity",
            verify_integrity,
        )

        process_module._verify_output_archive(
            archive_path,
            mode=OutputVerificationMode.CRC,
        )

        open_archive.assert_called_once_with(archive_path, mode="r")
        verify_integrity.assert_called_once_with(archive)
        archive.infolist.assert_not_called()
        archive_context.__exit__.assert_called_once()

    def test_invalid_mode_is_rejected_before_archive_is_opened(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        archive_path = tmp_path / "output.cbz"
        open_archive = Mock()
        monkeypatch.setattr(
            process_module,
            "ZipFile",
            open_archive,
        )
        expected_message = (
            "Output verification mode must be an "
            "OutputVerificationMode, not str."
        )

        with pytest.raises(
            TypeError,
            match=exact_message(expected_message),
        ):
            process_module._verify_output_archive(
                archive_path,
                mode="structure",
            )

        open_archive.assert_not_called()

    def test_invalid_zip_is_wrapped(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        archive_path = tmp_path / "output.cbz"
        error = BadZipFile("Invalid central directory")
        monkeypatch.setattr(
            process_module,
            "ZipFile",
            Mock(side_effect=error),
        )
        expected_message = (
            "The transformed output is not a valid ZIP archive."
        )

        with pytest.raises(
            InvalidArchiveError,
            match=exact_message(expected_message),
        ) as exception_info:
            process_module._verify_output_archive(
                archive_path,
                mode=OutputVerificationMode.STRUCTURE,
            )

        assert exception_info.value.__cause__ is error

    def test_crc_integrity_failure_is_propagated(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        archive_path = tmp_path / "output.cbz"
        archive = MagicMock(spec=ZipFile)
        archive_context = MagicMock()
        archive_context.__enter__.return_value = archive
        error = InvalidArchiveError(
            "Archive member failed its integrity check: '002.jpg'."
        )
        monkeypatch.setattr(
            process_module,
            "ZipFile",
            Mock(return_value=archive_context),
        )
        monkeypatch.setattr(
            process_module,
            "verify_archive_integrity",
            Mock(side_effect=error),
        )

        with pytest.raises(InvalidArchiveError) as exception_info:
            process_module._verify_output_archive(
                archive_path,
                mode=OutputVerificationMode.CRC,
            )

        assert exception_info.value is error
        archive_context.__exit__.assert_called_once()


class TestTransformArchiveContents:
    def test_mixed_archive_is_transformed_in_original_order(self) -> None:
        large_image = create_encoded_image(
            "JPEG",
            size=(20, 40),
        )
        unchanged_image = create_encoded_image(
            "PNG",
            size=(10, 20),
        )
        metadata = b"<ComicInfo><Title>Example</Title></ComicInfo>"
        source_stream = BytesIO()
        destination_stream = BytesIO()

        with ZipFile(source_stream, mode="w") as source:
            source.writestr("001.jpg", large_image)
            source.writestr("ComicInfo.xml", metadata)
            source.writestr("002.png", unchanged_image)

        source_stream.seek(0)
        with (
            ZipFile(source_stream, mode="r") as source,
            ZipFile(destination_stream, mode="w") as destination,
        ):
            result = transform_archive_contents(
                source,
                destination,
                options=ArchiveTransformationOptions(
                    image_options=ImageProcessingOptions(
                        portrait_screen_size=(10, 20),
                    ),
                    date_time_policy=MemberDateTimePolicy(
                        mode=MemberDateTimeMode.FIXED,
                        fixed_date_time=(2000, 1, 2, 3, 4, 6),
                    ),
                ),
            )

            assert source.fp is not None
            assert destination.fp is not None

        destination_stream.seek(0)
        with ZipFile(destination_stream, mode="r") as destination:
            assert destination.namelist() == [
                "001.jpg",
                "ComicInfo.xml",
                "002.png",
            ]
            first_image = destination.getinfo("001.jpg")
            metadata_member = destination.getinfo("ComicInfo.xml")
            second_image = destination.getinfo("002.png")
            assert first_image.compress_type == ZIP_STORED
            assert second_image.compress_type == ZIP_STORED
            assert metadata_member.compress_type == ZIP_DEFLATED
            assert destination.read("ComicInfo.xml") == metadata
            assert destination.read("002.png") == unchanged_image
            assert first_image.date_time == (2000, 1, 2, 3, 4, 6)
            assert metadata_member.date_time == (2000, 1, 2, 3, 4, 6)
            assert second_image.date_time == (2000, 1, 2, 3, 4, 6)
            with open_encoded_image(destination.read("001.jpg")) as image:
                assert image.size == (10, 20)

        assert result == ArchiveTransformationResult(
            total_file_members=3,
            image_members=2,
            transformed_images=1,
            unchanged_images=1,
            copied_other_members=1,
            input_uncompressed_size=(
                len(large_image)
                + len(metadata)
                + len(unchanged_image)
            ),
            output_uncompressed_size=(
                len(first_image_data := _read_zip_member(
                    destination_stream,
                    "001.jpg",
                ))
                + len(metadata)
                + len(unchanged_image)
            ),
        )
        assert first_image_data != large_image

    def test_unchanged_image_is_written_as_untransformed(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source = Mock(spec=ZipFile)
        destination = Mock(spec=ZipFile)

        image_member = Mock()
        image_member.filename = "001.png"

        manifest = Mock()
        manifest.file_members = (image_member,)
        manifest.image_members = (image_member,)
        manifest.image_count = 1

        monkeypatch.setattr(
            process_module,
            "build_manifest",
            Mock(return_value=manifest),
        )
        monkeypatch.setattr(
            process_module,
            "read_member_data",
            Mock(return_value=b"source image"),
        )
        monkeypatch.setattr(
            process_module,
            "process_image_data",
            Mock(
                return_value=ProcessedImage(
                    data=b"source image",
                    format="PNG",
                    transformed=False,
                )
            ),
        )

        write = Mock()
        monkeypatch.setattr(
            process_module,
            "write_member_data",
            write,
        )

        path_limits = ArchivePathLimits()
        policy = MemberDateTimePolicy(
            mode=MemberDateTimeMode.MODIFIED,
        )

        result = transform_archive_contents(
            source,
            destination,
            options=ArchiveTransformationOptions(
                image_options=ImageProcessingOptions(
                    portrait_screen_size=(10, 20),
                ),
                path_limits=path_limits,
                date_time_policy=policy,
            ),
        )

        write.assert_called_once_with(
            destination,
            image_member,
            b"source image",
            compression=ZIP_STORED,
            date_time_policy=policy,
            transformed=False,
            path_limits=path_limits,
        )

        assert result.transformed_images == 0
        assert result.unchanged_images == 1

    def test_options_and_member_policies_are_forwarded(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source = Mock(spec=ZipFile)
        destination = Mock(spec=ZipFile)

        image_member = Mock()
        image_member.filename = "001.jpg"

        other_member = Mock()
        other_member.filename = "ComicInfo.xml"

        manifest = Mock()
        manifest.file_members = (
            image_member,
            other_member,
        )
        manifest.image_members = (image_member,)
        manifest.image_count = 1

        build = Mock(return_value=manifest)

        member_data = iter(
            (
                b"source image",
                b"metadata",
            )
        )

        def read_member(
            archive: ZipFile,
            member: object,
            *,
            limits: ArchiveReadLimits,
            state: ArchiveReadState,
        ) -> bytes:
            data = next(member_data)
            state.total_size += len(data)
            return data

        read = Mock(side_effect=read_member)

        process_image = Mock(
            return_value=ProcessedImage(
                data=b"processed image",
                format="JPEG",
                transformed=True,
            )
        )
        write = Mock()

        monkeypatch.setattr(
            process_module,
            "build_manifest",
            build,
        )
        monkeypatch.setattr(
            process_module,
            "read_member_data",
            read,
        )
        monkeypatch.setattr(
            process_module,
            "process_image_data",
            process_image,
        )
        monkeypatch.setattr(
            process_module,
            "write_member_data",
            write,
        )

        image_options = ImageProcessingOptions(
            portrait_screen_size=(10, 20),
        )
        read_limits = ArchiveReadLimits(
            max_file_size=100,
            max_total_size=200,
        )
        path_limits = ArchivePathLimits(
            max_path_length=100,
            max_component_length=50,
        )
        policy = MemberDateTimePolicy(
            mode=MemberDateTimeMode.PRESERVE,
        )
        options = ArchiveTransformationOptions(
            image_options=image_options,
            max_files=5,
            read_limits=read_limits,
            path_limits=path_limits,
            date_time_policy=policy,
        )

        result = transform_archive_contents(
            source,
            destination,
            options=options,
        )

        build.assert_called_once_with(
            source,
            max_files=5,
            max_file_uncompressed_size=100,
            max_total_uncompressed_size=200,
            path_limits=path_limits,
        )

        assert read.call_count == 2

        first_state = read.call_args_list[0].kwargs["state"]
        second_state = read.call_args_list[1].kwargs["state"]

        assert first_state is second_state

        assert read.call_args_list[0].args == (
            source,
            image_member,
        )
        assert read.call_args_list[0].kwargs == {
            "limits": read_limits,
            "state": first_state,
        }
        assert read.call_args_list[1].args == (
            source,
            other_member,
        )
        assert read.call_args_list[1].kwargs == {
            "limits": read_limits,
            "state": first_state,
        }

        process_image.assert_called_once_with(
            b"source image",
            "001.jpg",
            options=image_options,
        )

        assert write.call_args_list[0].args == (
            destination,
            image_member,
            b"processed image",
        )
        assert write.call_args_list[0].kwargs == {
            "compression": ZIP_STORED,
            "date_time_policy": policy,
            "transformed": True,
            "path_limits": path_limits,
        }

        assert write.call_args_list[1].args == (
            destination,
            other_member,
            b"metadata",
        )
        assert write.call_args_list[1].kwargs == {
            "compression": ZIP_DEFLATED,
            "date_time_policy": policy,
            "transformed": False,
            "path_limits": path_limits,
        }

        assert result == ArchiveTransformationResult(
            total_file_members=2,
            image_members=1,
            transformed_images=1,
            unchanged_images=0,
            copied_other_members=1,
            input_uncompressed_size=(
                len(b"source image")
                + len(b"metadata")
            ),
            output_uncompressed_size=(
                len(b"processed image")
                + len(b"metadata")
            ),
        )

    def test_total_uncompressed_size_limit_is_enforced_before_processing(
        self,
    ) -> None:
        first = create_encoded_image("PNG", size=(10, 20))
        second = create_encoded_image("PNG", size=(10, 20))
        source_stream = BytesIO()
        destination_stream = BytesIO()

        with ZipFile(source_stream, mode="w") as source:
            source.writestr("001.png", first)
            source.writestr("002.png", second)

        source_stream.seek(0)
        with (
            ZipFile(source_stream, mode="r") as source,
            ZipFile(destination_stream, mode="w") as destination,
            pytest.raises(
                InvalidArchiveError,
                match=exact_message(
                    "The archive's uncompressed contents exceed "
                    "the permitted size."
                ),
            ),
        ):
            transform_archive_contents(
                source,
                destination,
                options=ArchiveTransformationOptions(
                    image_options=ImageProcessingOptions(
                        portrait_screen_size=(10, 20),
                    ),
                    read_limits=ArchiveReadLimits(
                        max_file_size=max(len(first), len(second)),
                        max_total_size=len(first) + len(second) - 1,
                    ),
                ),
            )

    def test_max_file_count_is_enforced_before_writing(self) -> None:
        source_stream = BytesIO()
        destination_stream = BytesIO()
        image = create_encoded_image("PNG")

        with ZipFile(source_stream, mode="w") as source:
            source.writestr("001.png", image)
            source.writestr("002.png", image)

        source_stream.seek(0)
        with (
            ZipFile(source_stream, mode="r") as source,
            ZipFile(destination_stream, mode="w") as destination,
            pytest.raises(
                InvalidArchiveError,
                match=exact_message(
                    "The archive exceeds the permitted file count of 1."
                ),
            ),
        ):
            transform_archive_contents(
                source,
                destination,
                options=ArchiveTransformationOptions(
                    image_options=ImageProcessingOptions(
                        portrait_screen_size=(10, 20),
                    ),
                    max_files=1,
                ),
            )

        destination_stream.seek(0)
        with ZipFile(destination_stream, mode="r") as destination:
            assert destination.namelist() == []

    def test_invalid_image_stops_processing_before_member_is_written(self) -> None:
        source_stream = BytesIO()
        destination_stream = BytesIO()

        with ZipFile(source_stream, mode="w") as source:
            source.writestr("001.jpg", b"not an image")

        source_stream.seek(0)
        with (
            ZipFile(source_stream, mode="r") as source,
            ZipFile(destination_stream, mode="w") as destination,
            pytest.raises(
                UnsupportedImageContentError,
                match=exact_message(
                    "Image data could not be decoded: '001.jpg'."
                ),
            ),
        ):
            transform_archive_contents(
                source,
                destination,
                options=ArchiveTransformationOptions(
                    image_options=ImageProcessingOptions(
                        portrait_screen_size=(10, 20),
                    ),
                ),
            )

        destination_stream.seek(0)
        with ZipFile(destination_stream, mode="r") as destination:
            assert destination.namelist() == []

    def test_failure_keeps_members_written_before_failing_member(
        self,
    ) -> None:
        source_stream = BytesIO()
        destination_stream = BytesIO()
        valid_image = create_encoded_image(
            "PNG",
            size=(10, 20),
        )

        with ZipFile(source_stream, mode="w") as source:
            source.writestr("001.png", valid_image)
            source.writestr("002.jpg", b"not an image")

        source_stream.seek(0)

        with (
            ZipFile(source_stream, mode="r") as source,
            ZipFile(destination_stream, mode="w") as destination,
            pytest.raises(
                UnsupportedImageContentError,
                match=exact_message(
                    "Image data could not be decoded: '002.jpg'."
                ),
            ),
        ):
            transform_archive_contents(
                source,
                destination,
                options=ArchiveTransformationOptions(
                    image_options=ImageProcessingOptions(
                        portrait_screen_size=(10, 20),
                    ),
                ),
            )

        destination_stream.seek(0)

        with ZipFile(destination_stream, mode="r") as destination:
            assert destination.namelist() == ["001.png"]
            assert destination.read("001.png") == valid_image

    def test_archive_without_images_is_rejected_before_writing(
        self,
    ) -> None:
        source_stream = BytesIO()
        destination_stream = BytesIO()

        with ZipFile(source_stream, mode="w") as source:
            source.writestr(
                "ComicInfo.xml",
                b"<ComicInfo />",
            )

        source_stream.seek(0)

        with (
            ZipFile(source_stream, mode="r") as source,
            ZipFile(destination_stream, mode="w") as destination,
            pytest.raises(
                InvalidArchiveError,
                match=exact_message(
                    "The archive does not contain any supported images."
                ),
            ),
        ):
            transform_archive_contents(
                source,
                destination,
                options=ArchiveTransformationOptions(
                    image_options=ImageProcessingOptions(
                        portrait_screen_size=(10, 20),
                    ),
                ),
            )

        destination_stream.seek(0)

        with ZipFile(destination_stream, mode="r") as destination:
            assert destination.namelist() == []

    def test_write_failure_is_propagated_and_stops_processing(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source = Mock(spec=ZipFile)
        destination = Mock(spec=ZipFile)

        first_member = Mock()
        first_member.filename = "001.jpg"

        second_member = Mock()
        second_member.filename = "002.jpg"

        manifest = Mock()
        manifest.file_members = (
            first_member,
            second_member,
        )
        manifest.image_members = (
            first_member,
            second_member,
        )
        manifest.image_count = 2

        error = OSError("Archive write failed")

        monkeypatch.setattr(
            process_module,
            "build_manifest",
            Mock(return_value=manifest),
        )
        read = Mock(
            side_effect=[
                b"first image",
                b"second image",
            ]
        )
        monkeypatch.setattr(
            process_module,
            "read_member_data",
            read,
        )
        process_image = Mock(
            return_value=ProcessedImage(
                data=b"processed image",
                format="JPEG",
                transformed=True,
            )
        )
        monkeypatch.setattr(
            process_module,
            "process_image_data",
            process_image,
        )
        write = Mock(side_effect=error)
        monkeypatch.setattr(
            process_module,
            "write_member_data",
            write,
        )

        with pytest.raises(
            OSError,
            match=exact_message("Archive write failed"),
        ) as exception_info:
            transform_archive_contents(
                source,
                destination,
                options=ArchiveTransformationOptions(
                    image_options=ImageProcessingOptions(
                        portrait_screen_size=(10, 20),
                    ),
                ),
            )

        assert exception_info.value is error
        assert read.call_count == 1
        assert process_image.call_count == 1
        assert write.call_count == 1


class TestProcessArchiveFile:
    def test_archive_file_is_processed_and_published_atomically(
        self,
        tmp_path: Path,
    ) -> None:
        source_path = tmp_path / "source.cbz"
        destination_path = tmp_path / "output.cbz"
        large_image = create_encoded_image(
            "JPEG",
            size=(20, 40),
        )
        unchanged_image = create_encoded_image(
            "PNG",
            size=(10, 20),
        )
        metadata = b"<ComicInfo><Title>Example</Title></ComicInfo>"
        create_archive_file(
            source_path,
            [
                ("001.jpg", large_image),
                ("ComicInfo.xml", metadata),
                ("002.png", unchanged_image),
            ],
        )
        source_bytes = source_path.read_bytes()

        result = process_archive_file(
            source_path,
            destination_path,
            options=ArchiveTransformationOptions(
                image_options=ImageProcessingOptions(
                    portrait_screen_size=(10, 20),
                ),
                date_time_policy=MemberDateTimePolicy(
                    mode=MemberDateTimeMode.FIXED,
                    fixed_date_time=(2000, 1, 2, 3, 4, 6),
                ),
            ),
        )

        assert source_path.read_bytes() == source_bytes
        assert destination_path.is_file()
        assert temporary_archive_paths(destination_path) == []

        with ZipFile(destination_path, mode="r") as destination:
            assert destination.namelist() == [
                "001.jpg",
                "ComicInfo.xml",
                "002.png",
            ]
            transformed_image = destination.read("001.jpg")
            assert destination.read("ComicInfo.xml") == metadata
            assert destination.read("002.png") == unchanged_image
            with open_encoded_image(transformed_image) as image:
                assert image.size == (10, 20)

        assert result == ArchiveTransformationResult(
            total_file_members=3,
            image_members=2,
            transformed_images=1,
            unchanged_images=1,
            copied_other_members=1,
            input_uncompressed_size=(
                len(large_image)
                + len(metadata)
                + len(unchanged_image)
            ),
            output_uncompressed_size=(
                len(transformed_image)
                + len(metadata)
                + len(unchanged_image)
            ),
        )
    def test_transformation_result_is_returned(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source_path = tmp_path / "source.cbz"
        destination_path = tmp_path / "output.cbz"
        create_archive_file(
            source_path,
            [("001.png", create_encoded_image("PNG"))],
        )
        options = ArchiveTransformationOptions(
            image_options=ImageProcessingOptions(
                portrait_screen_size=(10, 20),
            ),
        )
        expected_result = ArchiveTransformationResult(
            total_file_members=1,
            image_members=1,
            transformed_images=0,
            unchanged_images=1,
            copied_other_members=0,
            input_uncompressed_size=100,
            output_uncompressed_size=100,
        )
        transform = Mock(return_value=expected_result)
        real_publish = process_module._publish_archive

        def publish_after_archives_are_closed(
            temporary_path: Path,
            published_path: Path,
            *,
            conflict_mode: DestinationConflictMode,
        ) -> None:
            source_archive, destination_archive = transform.call_args.args
            assert source_archive.fp is None
            assert destination_archive.fp is None
            assert temporary_path.is_file()
            assert published_path == destination_path
            assert conflict_mode is DestinationConflictMode.ERROR
            real_publish(
                temporary_path,
                published_path,
                conflict_mode=conflict_mode,
            )

        publish = Mock(side_effect=publish_after_archives_are_closed)
        monkeypatch.setattr(
            process_module,
            "transform_archive_contents",
            transform,
        )
        monkeypatch.setattr(
            process_module,
            "_publish_archive",
            publish,
        )

        result = process_archive_file(
            source_path,
            destination_path,
            options=options,
        )

        assert result is expected_result
        transform.assert_called_once()
        assert transform.call_args.kwargs == {"options": options}
        publish.assert_called_once()
        assert destination_path.is_file()
    @pytest.mark.parametrize(
        "verification_mode",
        list(OutputVerificationMode),
    )
    def test_archive_is_processed_end_to_end_with_each_verification_mode(
        self,
        tmp_path: Path,
        verification_mode: OutputVerificationMode,
    ) -> None:
        source_path = tmp_path / "source.cbz"
        destination_path = tmp_path / "output.cbz"
        image_data = create_encoded_image(
            "PNG",
            size=(10, 20),
        )
        create_archive_file(
            source_path,
            [("001.png", image_data)],
        )

        result = process_archive_file(
            source_path,
            destination_path,
            options=ArchiveTransformationOptions(
                image_options=ImageProcessingOptions(
                    portrait_screen_size=(10, 20),
                ),
            ),
            verification_mode=verification_mode,
        )

        assert result == ArchiveTransformationResult(
            total_file_members=1,
            image_members=1,
            transformed_images=0,
            unchanged_images=1,
            copied_other_members=0,
            input_uncompressed_size=len(image_data),
            output_uncompressed_size=len(image_data),
        )
        assert source_path.is_file()
        assert destination_path.is_file()
        assert temporary_archive_paths(destination_path) == []

        with ZipFile(destination_path, mode="r") as destination_archive:
            assert destination_archive.namelist() == ["001.png"]
            assert destination_archive.read("001.png") == image_data
            assert destination_archive.testzip() is None

    @pytest.mark.parametrize(
        "verification_mode",
        list(OutputVerificationMode),
    )
    def test_selected_verification_mode_is_applied_before_publication(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        verification_mode: OutputVerificationMode,
    ) -> None:
        source_path = tmp_path / "source.cbz"
        destination_path = tmp_path / "output.cbz"
        create_archive_file(
            source_path,
            [("001.png", create_encoded_image("PNG"))],
        )
        options = ArchiveTransformationOptions(
            image_options=ImageProcessingOptions(
                portrait_screen_size=(10, 20),
            ),
        )
        expected_result = ArchiveTransformationResult(
            total_file_members=1,
            image_members=1,
            transformed_images=0,
            unchanged_images=1,
            copied_other_members=0,
            input_uncompressed_size=100,
            output_uncompressed_size=100,
        )
        transform = Mock(return_value=expected_result)
        real_publish = process_module._publish_archive
        events: list[str] = []

        def verify_after_archives_are_closed(
            archive_path: Path,
            *,
            mode: OutputVerificationMode,
        ) -> None:
            source_archive, destination_archive = transform.call_args.args
            assert source_archive.fp is None
            assert destination_archive.fp is None
            assert archive_path.is_file()
            assert mode is verification_mode
            events.append("verify")

        def publish_after_verification(
            temporary_path: Path,
            published_path: Path,
            *,
            conflict_mode: DestinationConflictMode,
        ) -> None:
            events.append("publish")
            real_publish(
                temporary_path,
                published_path,
                conflict_mode=conflict_mode,
            )

        verify = Mock(side_effect=verify_after_archives_are_closed)
        publish = Mock(side_effect=publish_after_verification)
        monkeypatch.setattr(
            process_module,
            "transform_archive_contents",
            transform,
        )
        monkeypatch.setattr(
            process_module,
            "_verify_output_archive",
            verify,
        )
        monkeypatch.setattr(
            process_module,
            "_publish_archive",
            publish,
        )

        result = process_archive_file(
            source_path,
            destination_path,
            options=options,
            verification_mode=verification_mode,
        )

        assert result is expected_result
        assert events == ["verify", "publish"]
        verify.assert_called_once()
        publish.assert_called_once()
        assert destination_path.is_file()
    def test_structure_verification_is_used_by_default(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source_path = tmp_path / "source.cbz"
        destination_path = tmp_path / "output.cbz"
        create_archive_file(
            source_path,
            [("001.png", create_encoded_image("PNG"))],
        )
        verify = Mock()
        monkeypatch.setattr(
            process_module,
            "_verify_output_archive",
            verify,
        )

        process_archive_file(
            source_path,
            destination_path,
            options=ArchiveTransformationOptions(
                image_options=ImageProcessingOptions(
                    portrait_screen_size=(10, 20),
                ),
            ),
        )

        verify.assert_called_once()
        assert verify.call_args.kwargs == {
            "mode": OutputVerificationMode.STRUCTURE,
        }

    def test_invalid_verification_mode_is_rejected_before_file_processing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source_path = tmp_path / "missing.cbz"
        destination_path = tmp_path / "output.cbz"
        create_temporary = Mock()
        transform = Mock()
        monkeypatch.setattr(
            process_module,
            "NamedTemporaryFile",
            create_temporary,
        )
        monkeypatch.setattr(
            process_module,
            "transform_archive_contents",
            transform,
        )
        expected_message = (
            "Output verification mode must be an "
            "OutputVerificationMode, not str."
        )

        with pytest.raises(
            TypeError,
            match=exact_message(expected_message),
        ):
            process_archive_file(
                source_path,
                destination_path,
                options=ArchiveTransformationOptions(
                    image_options=ImageProcessingOptions(
                        portrait_screen_size=(10, 20),
                    ),
                ),
                verification_mode="crc",
            )

        create_temporary.assert_not_called()
        transform.assert_not_called()
        assert not destination_path.exists()

    def test_verification_failure_removes_temporary_file_without_publishing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source_path = tmp_path / "source.cbz"
        destination_path = tmp_path / "output.cbz"
        create_archive_file(
            source_path,
            [("001.png", create_encoded_image("PNG"))],
        )
        error = InvalidArchiveError(
            "The transformed output is not a valid ZIP archive."
        )
        verify = Mock(side_effect=error)
        publish = Mock()
        monkeypatch.setattr(process_module, "_verify_output_archive", verify)
        monkeypatch.setattr(process_module, "_publish_archive", publish)

        with pytest.raises(InvalidArchiveError) as exception_info:
            process_archive_file(
                source_path,
                destination_path,
                options=ArchiveTransformationOptions(
                    image_options=ImageProcessingOptions(
                        portrait_screen_size=(10, 20),
                    ),
                ),
                verification_mode=OutputVerificationMode.CRC,
            )

        assert exception_info.value is error
        verify.assert_called_once()
        assert verify.call_args.kwargs == {
            "mode": OutputVerificationMode.CRC,
        }
        publish.assert_not_called()
        assert not destination_path.exists()
        assert temporary_archive_paths(destination_path) == []
    def test_missing_source_file_is_rejected(
        self,
        tmp_path: Path,
    ) -> None:
        source_path = tmp_path / "missing.cbz"
        destination_path = tmp_path / "output.cbz"
        expected_message = (
            f"Source archive file does not exist: {source_path}."
        )

        with pytest.raises(
            FileNotFoundError,
            match=exact_message(expected_message),
        ):
            process_archive_file(
                source_path,
                destination_path,
                options=ArchiveTransformationOptions(
                    image_options=ImageProcessingOptions(
                        portrait_screen_size=(10, 20),
                    ),
                ),
            )

        assert not destination_path.exists()

    def test_source_directory_is_rejected(
        self,
        tmp_path: Path,
    ) -> None:
        source_path = tmp_path / "source.cbz"
        source_path.mkdir()
        destination_path = tmp_path / "output.cbz"
        expected_message = (
            f"Source archive path is not a file: {source_path}."
        )

        with pytest.raises(
            IsADirectoryError,
            match=exact_message(expected_message),
        ):
            process_archive_file(
                source_path,
                destination_path,
                options=ArchiveTransformationOptions(
                    image_options=ImageProcessingOptions(
                        portrait_screen_size=(10, 20),
                    ),
                ),
            )

    def test_missing_destination_directory_is_rejected(
        self,
        tmp_path: Path,
    ) -> None:
        source_path = tmp_path / "source.cbz"
        create_archive_file(
            source_path,
            [("001.png", create_encoded_image("PNG"))],
        )
        destination_parent = tmp_path / "missing"
        destination_path = destination_parent / "output.cbz"
        expected_message = (
            f"Destination directory does not exist: {destination_parent}."
        )

        with pytest.raises(
            FileNotFoundError,
            match=exact_message(expected_message),
        ):
            process_archive_file(
                source_path,
                destination_path,
                options=ArchiveTransformationOptions(
                    image_options=ImageProcessingOptions(
                        portrait_screen_size=(10, 20),
                    ),
                ),
            )

    def test_destination_parent_file_is_rejected(
        self,
        tmp_path: Path,
    ) -> None:
        source_path = tmp_path / "source.cbz"
        create_archive_file(
            source_path,
            [("001.png", create_encoded_image("PNG"))],
        )
        destination_parent = tmp_path / "not-a-directory"
        destination_parent.write_bytes(b"file")
        destination_path = destination_parent / "output.cbz"
        expected_message = (
            "Destination parent is not a directory: "
            f"{destination_parent}."
        )

        with pytest.raises(
            NotADirectoryError,
            match=exact_message(expected_message),
        ):
            process_archive_file(
                source_path,
                destination_path,
                options=ArchiveTransformationOptions(
                    image_options=ImageProcessingOptions(
                        portrait_screen_size=(10, 20),
                    ),
                ),
            )

    def test_equivalent_source_and_destination_paths_are_rejected(
        self,
        tmp_path: Path,
    ) -> None:
        source_path = tmp_path / "source.cbz"
        create_archive_file(
            source_path,
            [("001.png", create_encoded_image("PNG"))],
        )
        destination_path = tmp_path / "." / "source.cbz"
        expected_message = (
            "Source and destination archive paths must be different "
            "unless destination replacement is enabled."
        )

        with pytest.raises(
            ValueError,
            match=exact_message(expected_message),
        ):
            process_archive_file(
                source_path,
                destination_path,
                options=ArchiveTransformationOptions(
                    image_options=ImageProcessingOptions(
                        portrait_screen_size=(10, 20),
                    ),
                ),
            )

        assert source_path.is_file()
    def test_destination_directory_is_rejected(
        self,
        tmp_path: Path,
    ) -> None:
        source_path = tmp_path / "source.cbz"
        create_archive_file(
            source_path,
            [("001.png", create_encoded_image("PNG"))],
        )
        destination_path = tmp_path / "output.cbz"
        destination_path.mkdir()
        expected_message = (
            f"Destination path is a directory: {destination_path}."
        )

        with pytest.raises(
            IsADirectoryError,
            match=exact_message(expected_message),
        ):
            process_archive_file(
                source_path,
                destination_path,
                options=ArchiveTransformationOptions(
                    image_options=ImageProcessingOptions(
                        portrait_screen_size=(10, 20),
                    ),
                ),
            )

    def test_existing_destination_file_is_not_replaced(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source_path = tmp_path / "source.cbz"
        destination_path = tmp_path / "output.cbz"
        create_archive_file(
            source_path,
            [("001.png", create_encoded_image("PNG"))],
        )
        original_destination_data = b"existing destination"
        destination_path.write_bytes(original_destination_data)
        expected_message = (
            f"Destination archive already exists: {destination_path}."
        )

        create_temporary = Mock()
        transform = Mock()

        monkeypatch.setattr(
            process_module,
            "NamedTemporaryFile",
            create_temporary,
        )
        monkeypatch.setattr(
            process_module,
            "transform_archive_contents",
            transform,
        )

        with pytest.raises(
            FileExistsError,
            match=exact_message(expected_message),
        ):
            process_archive_file(
                source_path,
                destination_path,
                options=ArchiveTransformationOptions(
                    image_options=ImageProcessingOptions(
                        portrait_screen_size=(10, 20),
                    ),
                ),
            )

        assert destination_path.read_bytes() == original_destination_data
        assert temporary_archive_paths(destination_path) == []
        create_temporary.assert_not_called()
        transform.assert_not_called()

    def test_existing_destination_is_replaced_when_enabled(
        self,
        tmp_path: Path,
    ) -> None:
        source_path = tmp_path / "source.cbz"
        destination_path = tmp_path / "output.cbz"
        image_data = create_encoded_image("PNG")
        create_archive_file(source_path, [("001.png", image_data)])
        destination_path.write_bytes(b"existing destination")

        process_archive_file(
            source_path,
            destination_path,
            options=ArchiveTransformationOptions(
                image_options=ImageProcessingOptions(
                    portrait_screen_size=(10, 20),
                ),
            ),
            conflict_mode=DestinationConflictMode.REPLACE,
        )

        with ZipFile(destination_path, mode="r") as archive:
            assert archive.namelist() == ["001.png"]
            assert archive.read("001.png") == image_data
        assert temporary_archive_paths(destination_path) == []

    def test_in_place_processing_succeeds_when_replacement_is_enabled(
        self,
        tmp_path: Path,
    ) -> None:
        archive_path = tmp_path / "source.cbz"
        large_image = create_encoded_image("PNG", size=(20, 40))
        create_archive_file(archive_path, [("001.png", large_image)])
        original_data = archive_path.read_bytes()

        result = process_archive_file(
            archive_path,
            archive_path,
            options=ArchiveTransformationOptions(
                image_options=ImageProcessingOptions(
                    portrait_screen_size=(10, 20),
                ),
            ),
            conflict_mode=DestinationConflictMode.REPLACE,
        )

        assert archive_path.read_bytes() != original_data
        assert result.transformed_images == 1
        with (
            ZipFile(archive_path, mode="r") as archive,
            open_encoded_image(archive.read("001.png")) as image,
        ):
            assert image.size == (10, 20)
        assert temporary_archive_paths(archive_path) == []

    def test_in_place_failure_preserves_original_archive(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        archive_path = tmp_path / "source.cbz"
        create_archive_file(
            archive_path,
            [("001.png", create_encoded_image("PNG"))],
        )
        original_data = archive_path.read_bytes()
        error = RuntimeError("Transformation failed")
        monkeypatch.setattr(
            process_module,
            "transform_archive_contents",
            Mock(side_effect=error),
        )

        with pytest.raises(RuntimeError) as exception_info:
            process_archive_file(
                archive_path,
                archive_path,
                options=ArchiveTransformationOptions(
                    image_options=ImageProcessingOptions(
                        portrait_screen_size=(10, 20),
                    ),
                ),
                conflict_mode=DestinationConflictMode.REPLACE,
            )

        assert exception_info.value is error
        assert archive_path.read_bytes() == original_data
        assert temporary_archive_paths(archive_path) == []

    def test_invalid_conflict_mode_is_rejected_before_file_processing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source_path = tmp_path / "missing.cbz"
        destination_path = tmp_path / "output.cbz"
        create_temporary = Mock()
        transform = Mock()
        monkeypatch.setattr(
            process_module,
            "NamedTemporaryFile",
            create_temporary,
        )
        monkeypatch.setattr(
            process_module,
            "transform_archive_contents",
            transform,
        )
        expected_message = (
            "Destination conflict mode must be a "
            "DestinationConflictMode, not str."
        )

        with pytest.raises(
            TypeError,
            match=exact_message(expected_message),
        ):
            process_archive_file(
                source_path,
                destination_path,
                options=ArchiveTransformationOptions(
                    image_options=ImageProcessingOptions(
                        portrait_screen_size=(10, 20),
                    ),
                ),
                conflict_mode="replace",
            )

        create_temporary.assert_not_called()
        transform.assert_not_called()
        assert not destination_path.exists()

    def test_invalid_zip_is_wrapped_and_temporary_file_is_removed(
        self,
        tmp_path: Path,
    ) -> None:
        source_path = tmp_path / "invalid.cbz"
        destination_path = tmp_path / "output.cbz"
        source_path.write_bytes(b"not a ZIP archive")
        expected_message = (
            f"File is not a valid ZIP archive: {source_path}."
        )

        with pytest.raises(
            InvalidArchiveError,
            match=exact_message(expected_message),
        ) as exception_info:
            process_archive_file(
                source_path,
                destination_path,
                options=ArchiveTransformationOptions(
                    image_options=ImageProcessingOptions(
                        portrait_screen_size=(10, 20),
                    ),
                ),
            )

        assert isinstance(exception_info.value.__cause__, BadZipFile)
        assert not destination_path.exists()
        assert temporary_archive_paths(destination_path) == []

    @pytest.mark.parametrize(
        "error",
        [
            RuntimeError("Transformation failed"),
            KeyboardInterrupt(),
        ],
    )
    def test_transformation_failure_removes_temporary_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        error: BaseException,
    ) -> None:
        source_path = tmp_path / "source.cbz"
        destination_path = tmp_path / "output.cbz"
        create_archive_file(
            source_path,
            [("001.png", create_encoded_image("PNG"))],
        )
        transform = Mock(side_effect=error)
        monkeypatch.setattr(
            process_module,
            "transform_archive_contents",
            transform,
        )

        with pytest.raises(type(error)) as exception_info:
            process_archive_file(
                source_path,
                destination_path,
                options=ArchiveTransformationOptions(
                    image_options=ImageProcessingOptions(
                        portrait_screen_size=(10, 20),
                    ),
                ),
            )

        assert exception_info.value is error
        assert not destination_path.exists()
        assert temporary_archive_paths(destination_path) == []
        transform.assert_called_once()

    def test_destination_zip_finalization_failure_removes_temporary_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source_path = tmp_path / "source.cbz"
        destination_path = tmp_path / "output.cbz"
        create_archive_file(
            source_path,
            [("001.png", create_encoded_image("PNG"))],
        )
        error = OSError("ZIP finalization failed")
        real_zip_file = process_module.ZipFile

        class FailingDestinationArchive:
            def __init__(self, archive_path: Path) -> None:
                self.archive = real_zip_file(archive_path, mode="w")

            def __enter__(self) -> ZipFile:
                return self.archive

            def __exit__(
                self,
                exception_type: object,
                exception: object,
                traceback: object,
            ) -> None:
                self.archive.close()
                raise error

        def open_archive(
            archive_path: Path,
            *,
            mode: str,
        ) -> ZipFile | FailingDestinationArchive:
            if mode == "w":
                return FailingDestinationArchive(archive_path)
            return real_zip_file(archive_path, mode=mode)

        verify = Mock()
        publish = Mock()
        monkeypatch.setattr(process_module, "ZipFile", open_archive)
        monkeypatch.setattr(process_module, "_verify_output_archive", verify)
        monkeypatch.setattr(process_module, "_publish_archive", publish)

        with pytest.raises(
            OSError,
            match=exact_message("ZIP finalization failed"),
        ) as exception_info:
            process_archive_file(
                source_path,
                destination_path,
                options=ArchiveTransformationOptions(
                    image_options=ImageProcessingOptions(
                        portrait_screen_size=(10, 20),
                    ),
                ),
            )

        assert exception_info.value is error
        verify.assert_not_called()
        publish.assert_not_called()
        assert source_path.is_file()
        assert not destination_path.exists()
        assert temporary_archive_paths(destination_path) == []
    def test_publication_failure_removes_temporary_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source_path = tmp_path / "source.cbz"
        destination_path = tmp_path / "output.cbz"
        create_archive_file(
            source_path,
            [("001.png", create_encoded_image("PNG"))],
        )
        error = OSError("Atomic publication failed")
        publish = Mock(side_effect=error)
        monkeypatch.setattr(process_module, "_publish_archive", publish)

        with pytest.raises(OSError) as exception_info:
            process_archive_file(
                source_path,
                destination_path,
                options=ArchiveTransformationOptions(
                    image_options=ImageProcessingOptions(
                        portrait_screen_size=(10, 20),
                    ),
                ),
            )

        assert exception_info.value is error
        assert not destination_path.exists()
        assert temporary_archive_paths(destination_path) == []
        publish.assert_called_once()
        temporary_path, published_path = publish.call_args.args
        temporary_path = Path(temporary_path)
        assert temporary_path.parent == destination_path.parent
        assert temporary_path.name.startswith(f".{destination_path.name}.")
        assert temporary_path.suffix == ".tmp"
        assert published_path == destination_path
        assert publish.call_args.kwargs == {
            "conflict_mode": DestinationConflictMode.ERROR,
        }
    def test_temporary_cleanup_failure_does_not_hide_original_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source_path = tmp_path / "source.cbz"
        destination_path = tmp_path / "output.cbz"
        create_archive_file(
            source_path,
            [("001.png", create_encoded_image("PNG"))],
        )
        original_error = RuntimeError("Transformation failed")
        monkeypatch.setattr(
            process_module,
            "transform_archive_contents",
            Mock(side_effect=original_error),
        )
        unlink = Mock(side_effect=OSError("Cleanup failed"))

        with monkeypatch.context() as cleanup_patch:
            cleanup_patch.setattr(
                process_module.Path,
                "unlink",
                unlink,
            )
            with pytest.raises(RuntimeError) as exception_info:
                process_archive_file(
                    source_path,
                    destination_path,
                    options=ArchiveTransformationOptions(
                        image_options=ImageProcessingOptions(
                            portrait_screen_size=(10, 20),
                        ),
                    ),
                )

        assert exception_info.value is original_error
        unlink.assert_called_once_with(missing_ok=True)

    def test_temporary_file_creation_failure_is_propagated(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source_path = tmp_path / "source.cbz"
        destination_path = tmp_path / "output.cbz"
        create_archive_file(
            source_path,
            [("001.png", create_encoded_image("PNG"))],
        )

        error = PermissionError("Temporary file creation failed")

        monkeypatch.setattr(
            process_module,
            "NamedTemporaryFile",
            Mock(side_effect=error),
        )

        with pytest.raises(PermissionError) as exception_info:
            process_archive_file(
                source_path,
                destination_path,
                options=ArchiveTransformationOptions(
                    image_options=ImageProcessingOptions(
                        portrait_screen_size=(10, 20),
                    ),
                ),
            )

        assert exception_info.value is error
        assert not destination_path.exists()

    def test_destination_created_during_processing_is_preserved(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source_path = tmp_path / "source.cbz"
        destination_path = tmp_path / "output.cbz"
        concurrent_data = b"concurrent destination"

        create_archive_file(
            source_path,
            [("001.png", create_encoded_image("PNG"))],
        )

        real_publish = process_module._publish_archive

        def create_destination_before_publication(
            temporary_path: Path,
            published_path: Path,
            *,
            conflict_mode: DestinationConflictMode,
        ) -> None:
            published_path.write_bytes(concurrent_data)
            real_publish(
                temporary_path,
                published_path,
                conflict_mode=conflict_mode,
            )

        monkeypatch.setattr(
            process_module,
            "_publish_archive",
            create_destination_before_publication,
        )

        expected_message = (
            f"Destination archive already exists: {destination_path}."
        )

        with pytest.raises(
            FileExistsError,
            match=exact_message(expected_message),
        ) as exception_info:
            process_archive_file(
                source_path,
                destination_path,
                options=ArchiveTransformationOptions(
                    image_options=ImageProcessingOptions(
                        portrait_screen_size=(10, 20),
                    ),
                ),
            )

        assert isinstance(exception_info.value.__cause__, FileExistsError)
        assert destination_path.read_bytes() == concurrent_data
        assert temporary_archive_paths(destination_path) == []

    def test_in_place_verification_failure_preserves_original_archive(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        archive_path = tmp_path / "source.cbz"
        create_archive_file(
            archive_path,
            [("001.png", create_encoded_image("PNG"))],
        )
        original_data = archive_path.read_bytes()
        error = InvalidArchiveError(
            "The transformed output is not a valid ZIP archive."
        )

        monkeypatch.setattr(
            process_module,
            "_verify_output_archive",
            Mock(side_effect=error),
        )

        with pytest.raises(InvalidArchiveError) as exception_info:
            process_archive_file(
                archive_path,
                archive_path,
                options=ArchiveTransformationOptions(
                    image_options=ImageProcessingOptions(
                        portrait_screen_size=(10, 20),
                    ),
                ),
                conflict_mode=DestinationConflictMode.REPLACE,
            )

        assert exception_info.value is error
        assert archive_path.read_bytes() == original_data
        assert temporary_archive_paths(archive_path) == []

    def test_replacement_failure_preserves_existing_destination(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source_path = tmp_path / "source.cbz"
        destination_path = tmp_path / "output.cbz"
        existing_data = b"existing destination"

        create_archive_file(
            source_path,
            [("001.png", create_encoded_image("PNG"))],
        )
        destination_path.write_bytes(existing_data)

        error = OSError("Atomic replacement failed")
        monkeypatch.setattr(
            process_module.os,
            "replace",
            Mock(side_effect=error),
        )

        with pytest.raises(OSError) as exception_info:
            process_archive_file(
                source_path,
                destination_path,
                options=ArchiveTransformationOptions(
                    image_options=ImageProcessingOptions(
                        portrait_screen_size=(10, 20),
                    ),
                ),
                conflict_mode=DestinationConflictMode.REPLACE,
            )

        assert exception_info.value is error
        assert destination_path.read_bytes() == existing_data
        assert temporary_archive_paths(destination_path) == []
