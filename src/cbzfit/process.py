# SPDX-License-Identifier: GPL-3.0-or-later

from dataclasses import dataclass, field
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from PIL import Image, UnidentifiedImageError

from cbzfit.archive import (
    DEFAULT_MAX_ARCHIVE_FILES,
    ArchivePathLimits,
    ArchiveReadLimits,
    ArchiveReadState,
    MemberDateTimePolicy,
    build_manifest,
    read_member_data,
    write_member_data,
)
from cbzfit.decode import (
    UnsupportedImageContentError,
    validate_source_image,
)
from cbzfit.encode import (
    EncoderOptions,
    encode_image,
)
from cbzfit.image import resize_for_display


@dataclass(frozen=True)
class ImageProcessingOptions:
    """Store settings used to process one image."""

    portrait_screen_size: tuple[int, int]
    use_landscape_display: bool = True
    allow_upscale: bool = False
    encoder_options: EncoderOptions = field(
        default_factory=EncoderOptions
    )


@dataclass(frozen=True)
class ArchiveProcessingOptions:
    """Store settings used to transform one CBZ archive."""

    image_options: ImageProcessingOptions
    max_files: int = DEFAULT_MAX_ARCHIVE_FILES
    read_limits: ArchiveReadLimits = field(
        default_factory=ArchiveReadLimits
    )
    path_limits: ArchivePathLimits = field(
        default_factory=ArchivePathLimits
    )
    date_time_policy: MemberDateTimePolicy = field(
        default_factory=MemberDateTimePolicy
    )

    def __post_init__(self) -> None:
        """Validate archive-processing settings."""
        if self.max_files <= 0:
            raise ValueError(
                "Maximum file count must be a positive integer."
            )


@dataclass(frozen=True)
class ProcessedImage:
    """Contain processed image data and transformation details."""

    data: bytes
    format: str
    transformed: bool

    @property
    def size(self) -> int:
        """Return the processed image size in bytes."""
        return len(self.data)


@dataclass(frozen=True)
class ArchiveProcessingResult:
    """Summarize one transformed CBZ archive."""

    total_file_members: int
    image_members: int
    transformed_images: int
    unchanged_images: int
    copied_other_members: int
    input_uncompressed_size: int
    output_uncompressed_size: int


def process_image_data(
    data: bytes,
    filename: str,
    *,
    options: ImageProcessingOptions,
) -> ProcessedImage:
    """Validate, resize, and encode one image member when required.

    Original encoded bytes are returned unchanged when the image already fits
    the target display and upscaling is disabled.
    """
    try:
        image = Image.open(
            BytesIO(data),
        )
    except Image.DecompressionBombError as error:
        raise UnsupportedImageContentError(
            f"Image dimensions exceed the permitted limit: {filename!r}."
        ) from error
    except (UnidentifiedImageError, OSError) as error:
        raise UnsupportedImageContentError(
            f"Image data could not be decoded: {filename!r}."
        ) from error

    with image:
        source_format = validate_source_image(
            image=image,
            filename=filename,
        )

        try:
            image.load()
        except Image.DecompressionBombError as error:
            raise UnsupportedImageContentError(
                f"Image dimensions exceed the permitted limit: {filename!r}."
            ) from error
        except OSError as error:
            raise UnsupportedImageContentError(
                f"Image data could not be decoded: {filename!r}."
            ) from error

        resized_image = resize_for_display(
            image=image,
            portrait_screen_size=options.portrait_screen_size,
            use_landscape_display=options.use_landscape_display,
            allow_upscale=options.allow_upscale,
        )

        if resized_image is image:
            return ProcessedImage(
                data=data,
                format=source_format,
                transformed=False,
            )

        try:
            encoded_image = encode_image(
                resized_image,
                output_format=source_format,
                options=options.encoder_options,
            )
        finally:
            resized_image.close()

    return ProcessedImage(
        data=encoded_image.data,
        format=encoded_image.format,
        transformed=True,
    )


def process_cbz_archive(
    source_archive: ZipFile,
    destination_archive: ZipFile,
    *,
    options: ArchiveProcessingOptions,
) -> ArchiveProcessingResult:
    """Transform the contents of one open CBZ archive.

    Supported image members are validated, resized when required, and written
    without additional ZIP compression. Other members are copied unchanged
    and written using ZIP deflate compression.

    Members are processed and written in their original archive order. The
    source and destination archives remain open and are owned by the caller.
    """
    manifest = build_manifest(
        source_archive,
        max_files=options.max_files,
        max_file_uncompressed_size=(
            options.read_limits.max_file_size
        ),
        max_total_uncompressed_size=(
            options.read_limits.max_total_size
        ),
        path_limits=options.path_limits,
    )

    read_state = ArchiveReadState()
    image_filenames = {
        member.filename
        for member in manifest.image_members
    }

    transformed_images = 0
    unchanged_images = 0
    copied_other_members = 0
    output_uncompressed_size = 0

    for member in manifest.file_members:
        member_data = read_member_data(
            source_archive,
            member,
            limits=options.read_limits,
            state=read_state,
        )

        if member.filename in image_filenames:
            processed_image = process_image_data(
                member_data,
                member.filename,
                options=options.image_options,
            )
            output_data = processed_image.data

            write_member_data(
                destination_archive,
                member,
                output_data,
                compression=ZIP_STORED,
                date_time_policy=options.date_time_policy,
                transformed=processed_image.transformed,
                path_limits=options.path_limits,
            )

            if processed_image.transformed:
                transformed_images += 1
            else:
                unchanged_images += 1
        else:
            output_data = member_data

            write_member_data(
                destination_archive,
                member,
                output_data,
                compression=ZIP_DEFLATED,
                date_time_policy=options.date_time_policy,
                transformed=False,
                path_limits=options.path_limits,
            )

            copied_other_members += 1

        output_uncompressed_size += len(output_data)

    return ArchiveProcessingResult(
        total_file_members=len(manifest.file_members),
        image_members=manifest.image_count,
        transformed_images=transformed_images,
        unchanged_images=unchanged_images,
        copied_other_members=copied_other_members,
        input_uncompressed_size=read_state.total_size,
        output_uncompressed_size=output_uncompressed_size,
    )
