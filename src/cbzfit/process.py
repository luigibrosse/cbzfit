# SPDX-License-Identifier: GPL-3.0-or-later

import os
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from zipfile import (
    ZIP_DEFLATED,
    ZIP_STORED,
    BadZipFile,
    ZipFile,
)

from PIL import Image, UnidentifiedImageError

from cbzfit.archive import (
    DEFAULT_MAX_ARCHIVE_FILES,
    ArchivePathLimits,
    ArchiveReadLimits,
    ArchiveReadState,
    InvalidArchiveError,
    MemberDateTimePolicy,
    build_manifest,
    read_member_data,
    verify_archive_integrity,
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
class ArchiveTransformationOptions:
    """Store settings used to transform archive contents."""

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
        """Validate archive-transformation settings."""
        if self.max_files <= 0:
            raise ValueError(
                "Maximum file count must be a positive integer."
            )


@dataclass(frozen=True)
class ArchiveTransformationResult:
    """Summarize transformed archive contents."""

    total_file_members: int
    image_members: int
    transformed_images: int
    unchanged_images: int
    copied_other_members: int
    input_uncompressed_size: int
    output_uncompressed_size: int


class OutputVerificationMode(StrEnum):
    """Define verification performed before publishing an output archive."""

    NONE = "none"
    STRUCTURE = "structure"
    CRC = "crc"


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


def transform_archive_contents(
    source_archive: ZipFile,
    destination_archive: ZipFile,
    *,
    options: ArchiveTransformationOptions,
) -> ArchiveTransformationResult:
    """Transform contents between two open ZIP archives.

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

    return ArchiveTransformationResult(
        total_file_members=len(manifest.file_members),
        image_members=manifest.image_count,
        transformed_images=transformed_images,
        unchanged_images=unchanged_images,
        copied_other_members=copied_other_members,
        input_uncompressed_size=read_state.total_size,
        output_uncompressed_size=output_uncompressed_size,
    )


def _validate_output_verification_mode(
    mode: OutputVerificationMode,
) -> None:
    """Validate an output verification mode."""
    if not isinstance(mode, OutputVerificationMode):
        raise TypeError(
            "Output verification mode must be an "
            "OutputVerificationMode, not "
            f"{type(mode).__name__}."
        )


def _verify_output_archive(
    archive_path: Path,
    *,
    mode: OutputVerificationMode,
) -> None:
    """Verify a finalized output archive.


    NONE trusts successful ZIP finalization.
    STRUCTURE reopens and parses the ZIP central directory.
    CRC additionally reads every member and verifies its CRC.
    """
    _validate_output_verification_mode(mode)

    if mode is OutputVerificationMode.NONE:
        return

    try:
        with ZipFile(archive_path, mode="r") as archive:
            if mode is OutputVerificationMode.STRUCTURE:
                archive.infolist()
            elif mode is OutputVerificationMode.CRC:
                verify_archive_integrity(archive)
            else:  # pragma: no cover - guards future enum members
                raise ValueError(
                    f"Unsupported output verification mode: {mode!r}."
                )
    except BadZipFile as error:
        raise InvalidArchiveError(
            "The transformed output is not a valid ZIP archive."
        ) from error


def process_archive_file(
    source_path: Path,
    destination_path: Path,
    *,
    options: ArchiveTransformationOptions,
    verification_mode: OutputVerificationMode = (
        OutputVerificationMode.STRUCTURE
    ),
) -> ArchiveTransformationResult:
    """Atomically transform and optionally verify one archive file.

    The source archive is transformed into a temporary file created
    beside the destination. After transformation succeeds and both
    archives are closed, the temporary archive is verified using the
    selected mode and atomically published as the destination.

    The temporary file is removed when transformation, verification, or
    publication fails.
    """
    _validate_output_verification_mode(verification_mode)


    if not source_path.exists():
        raise FileNotFoundError(
            f"Source archive file does not exist: {source_path}."
        )

    if not source_path.is_file():
        raise IsADirectoryError(
            f"Source archive path is not a file: {source_path}."
        )

    destination_parent = destination_path.parent

    if not destination_parent.exists():
        raise FileNotFoundError(
            "Destination directory does not exist: "
            f"{destination_parent}."
        )

    if not destination_parent.is_dir():
        raise NotADirectoryError(
            "Destination parent is not a directory: "
            f"{destination_parent}."
        )

    if source_path.resolve() == destination_path.resolve():
        raise ValueError(
            "Source and destination archive paths must be different."
        )

    if destination_path.is_dir():
        raise IsADirectoryError(
            f"Destination path is a directory: {destination_path}."
        )

    if destination_path.exists():
        raise FileExistsError(
            f"Destination archive already exists: {destination_path}."
        )

    with NamedTemporaryFile(
        mode="w+b",
        prefix=f".{destination_path.name}.",
        suffix=".tmp",
        dir=destination_parent,
        delete=False,
    ) as temporary_file:
        temporary_path = Path(temporary_file.name)

    try:
        try:
            with (
                ZipFile(source_path, mode="r") as source_archive,
                ZipFile(temporary_path, mode="w") as destination_archive,
            ):
                result = transform_archive_contents(
                    source_archive,
                    destination_archive,
                    options=options,
                )
        except BadZipFile as error:
            raise InvalidArchiveError(
                f"File is not a valid ZIP archive: {source_path}."
            ) from error

        _verify_output_archive(
            temporary_path,
            mode=verification_mode,
        )

        os.replace(
            temporary_path,
            destination_path,
        )
    except BaseException:
        with suppress(OSError):
            temporary_path.unlink(missing_ok=True)
        raise

    return result
