# SPDX-License-Identifier: GPL-3.0-or-later

import os
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import perf_counter
from warnings import catch_warnings, simplefilter
from zipfile import (
    ZIP_DEFLATED,
    ZIP_STORED,
    BadZipFile,
    ZipFile,
)

from PIL import Image, UnidentifiedImageError

from cbzfit.archive import (
    ARCHIVE_MEMBER_READ_ERRORS,
    DEFAULT_MAX_ARCHIVE_FILES,
    DEFAULT_MEMBER_READ_CHUNK_SIZE,
    ArchivePathLimits,
    ArchiveReadLimits,
    ArchiveReadState,
    InvalidArchiveError,
    MemberDateTimePolicy,
    build_manifest,
    read_member_data,
    write_member_data,
)
from cbzfit.decode import (
    DEFAULT_MAX_IMAGE_PIXELS,
    UnsupportedImageContentError,
    validate_image_dimensions,
    validate_source_image,
)
from cbzfit.encode import (
    EncoderOptions,
    encode_image,
)
from cbzfit.image import (
    resize_for_display,
    validate_portrait_screen_size,
)
from cbzfit.progress import (
    ArchiveProgress,
    ArchiveProgressPhase,
    ProgressCallback,
    report_progress,
)


class SourceDestinationConflictError(ValueError):
    """Raised when equivalent source and destination paths conflict."""


@dataclass(frozen=True)
class ImageProcessingOptions:
    """Store settings used to process one image."""

    portrait_screen_size: tuple[int, int]
    use_landscape_display: bool = True
    allow_upscale: bool = False
    max_image_pixels: int = DEFAULT_MAX_IMAGE_PIXELS
    encoder_options: EncoderOptions = field(
        default_factory=EncoderOptions
    )

    def __post_init__(self) -> None:
        """Validate image-processing settings."""
        validate_portrait_screen_size(
            self.portrait_screen_size
        )

        if (
            isinstance(self.max_image_pixels, bool)
            or not isinstance(self.max_image_pixels, int)
        ):
            raise TypeError(
                "Maximum image pixel count must be an integer."
            )

        if self.max_image_pixels <= 0:
            raise ValueError(
                "Maximum image pixel count must be a positive integer."
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


@dataclass(frozen=True)
class ArchiveProcessingResult:
    """Summarize a successfully processed archive file."""

    transformation_result: ArchiveTransformationResult
    source_file_size: int
    destination_file_size: int
    elapsed_seconds: float


class OutputVerificationMode(StrEnum):
    """Define verification performed before publishing an output archive."""

    NONE = "none"
    STRUCTURE = "structure"
    CRC = "crc"


class DestinationConflictMode(StrEnum):
    """Define how an existing destination archive is handled."""

    ERROR = "error"
    REPLACE = "replace"


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
    with catch_warnings():
        simplefilter("error", Image.DecompressionBombWarning)

        try:
            image = Image.open(
                BytesIO(data),
            )
        except (
            Image.DecompressionBombWarning,
            Image.DecompressionBombError,
        ) as error:
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
            validate_image_dimensions(
                image=image,
                filename=filename,
                max_pixels=options.max_image_pixels,
            )

            try:
                image.load()
            except (
                Image.DecompressionBombWarning,
                Image.DecompressionBombError,
            ) as error:
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
    progress_callback: ProgressCallback | None = None,
) -> ArchiveTransformationResult:
    """Transform contents between two open ZIP archives.

    Supported image members are validated, resized when required, and written
    without additional ZIP compression. Other members are copied unchanged
    and written using ZIP deflate compression.

    Members are processed and written in their original archive order. The
    source and destination archives remain open and are owned by the caller.
    Progress reports completed members, allowing a future coordinator to emit
    the same monotonic events when member processing becomes parallel.
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

    total_file_members = len(manifest.file_members)
    report_progress(
        progress_callback,
        ArchiveProgress(
            phase=ArchiveProgressPhase.TRANSFORMING,
            completed=0,
            total=total_file_members,
        ),
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

    for completed_members, member in enumerate(
        manifest.file_members,
        start=1,
    ):
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

        report_progress(
            progress_callback,
            ArchiveProgress(
                phase=ArchiveProgressPhase.TRANSFORMING,
                completed=completed_members,
                total=total_file_members,
                member_name=member.filename,
            ),
        )
    return ArchiveTransformationResult(
        total_file_members=total_file_members,
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
    progress_callback: ProgressCallback | None = None,
) -> None:
    """Verify a finalized output archive and report verification progress.

    NONE trusts successful ZIP finalization and emits no progress.
    STRUCTURE emits one indeterminate event before parsing the central directory.
    CRC emits the same verification-start event before opening the archive, then
    switches to 0/N progress and reports each member after it is read fully, which
    validates its local header, compressed data, and CRC.
    """
    _validate_output_verification_mode(mode)
    if mode is OutputVerificationMode.NONE:
        return

    report_progress(
        progress_callback,
        ArchiveProgress(phase=ArchiveProgressPhase.VERIFYING),
    )

    try:
        with ZipFile(archive_path, mode="r") as archive:
            if mode is OutputVerificationMode.STRUCTURE:
                archive.infolist()
            elif mode is OutputVerificationMode.CRC:
                members = tuple(
                    member
                    for member in archive.infolist()
                    if not member.is_dir()
                )
                total_members = len(members)
                report_progress(
                    progress_callback,
                    ArchiveProgress(
                        phase=ArchiveProgressPhase.VERIFYING,
                        completed=0,
                        total=total_members,
                    ),
                )
                for completed_members, member in enumerate(members, start=1):
                    try:
                        with archive.open(member, mode="r") as member_stream:
                            while member_stream.read(
                                DEFAULT_MEMBER_READ_CHUNK_SIZE
                            ):
                                pass
                    except ARCHIVE_MEMBER_READ_ERRORS as error:
                        raise InvalidArchiveError(
                            "Archive member failed its integrity check: "
                            f"{member.filename!r}."
                        ) from error
                    report_progress(
                        progress_callback,
                        ArchiveProgress(
                            phase=ArchiveProgressPhase.VERIFYING,
                            completed=completed_members,
                            total=total_members,
                            member_name=member.filename,
                        ),
                    )
            else:  # pragma: no cover - guards future enum members
                raise ValueError(
                    f"Unsupported output verification mode: {mode!r}."
                )
    except BadZipFile as error:
        raise InvalidArchiveError(
            "The transformed output is not a valid ZIP archive."
        ) from error


def _validate_destination_conflict_mode(
    mode: DestinationConflictMode,
) -> None:
    """Validate a destination conflict mode."""
    if not isinstance(mode, DestinationConflictMode):
        raise TypeError(
            "Destination conflict mode must be a "
            "DestinationConflictMode, not "
            f"{type(mode).__name__}."
        )


def _publish_archive(
    temporary_path: Path,
    destination_path: Path,
    *,
    conflict_mode: DestinationConflictMode,
) -> None:
    """Atomically publish a completed temporary archive.

    ERROR creates the destination only when it does not already exist.
    REPLACE atomically replaces an existing destination.

    Both paths must reside on the same filesystem.
    """
    _validate_destination_conflict_mode(conflict_mode)

    if conflict_mode is DestinationConflictMode.ERROR:
        try:
            os.link(
                temporary_path,
                destination_path,
            )
        except FileExistsError as error:
            raise FileExistsError(
                f"Destination archive already exists: {destination_path}."
            ) from error

        # The destination now references the complete temporary archive.
        # Failure to remove the temporary link must not turn a successful
        # publication into a reported processing failure.
        with suppress(OSError):
            temporary_path.unlink()

        return

    if conflict_mode is DestinationConflictMode.REPLACE:
        os.replace(
            temporary_path,
            destination_path,
        )
        return

    # Defensive guard against a future enum member that is not implemented.
    raise ValueError(  # pragma: no cover
        f"Unsupported destination conflict mode: {conflict_mode!r}."
    )


def process_archive_file(
    source_path: Path,
    destination_path: Path,
    *,
    options: ArchiveTransformationOptions,
    verification_mode: OutputVerificationMode = (
        OutputVerificationMode.STRUCTURE
    ),
    conflict_mode: DestinationConflictMode = (
        DestinationConflictMode.ERROR
    ),
    progress_callback: ProgressCallback | None = None,
) -> ArchiveProcessingResult:
    """Transform, verify, and atomically publish one archive file.

    The source file size is captured before processing starts. Archive contents
    are written to a temporary file beside the destination, and both ZIP files
    are closed before the temporary archive is verified and published.

    ERROR publishes only when the destination does not exist. REPLACE atomically
    replaces an existing destination and permits in-place processing when the
    source and destination paths identify the same file.

    After successful publication, the destination file size is read from the
    published path. Elapsed time uses a monotonic performance timer and covers
    validation, transformation, verification, publication, and final size
    measurement. The temporary file is removed if transformation, verification,
    or publication fails.

    Progress is emitted as interface-neutral events. Callbacks run in the
    coordinating caller's execution context and should return quickly; worker
    threads or processes must not invoke user-interface code directly.
    Return file-level metrics together with the archive-content transformation
    result.
    """
    started_at = perf_counter()
    _validate_output_verification_mode(verification_mode)
    _validate_destination_conflict_mode(conflict_mode)

    if not source_path.exists():
        raise FileNotFoundError(
            f"Source archive file does not exist: {source_path}."
        )

    if not source_path.is_file():
        raise IsADirectoryError(
            f"Source archive path is not a file: {source_path}."
        )

    source_file_size = source_path.stat().st_size
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

    paths_are_equivalent = (
        source_path.resolve()
        == destination_path.resolve()
    )

    if (
        paths_are_equivalent
        and conflict_mode is DestinationConflictMode.ERROR
    ):
        raise SourceDestinationConflictError(
            "Source and destination archive paths must be different "
            "unless destination replacement is enabled."
        )

    if destination_path.is_dir():
        raise IsADirectoryError(
            f"Destination path is a directory: {destination_path}."
        )

    if (
        destination_path.exists()
        and conflict_mode is DestinationConflictMode.ERROR
    ):
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
                    progress_callback=progress_callback,
                )
        except BadZipFile as error:
            raise InvalidArchiveError(
                f"File is not a valid ZIP archive: {source_path}."
            ) from error

        _verify_output_archive(
            temporary_path,
            mode=verification_mode,
            progress_callback=progress_callback,
        )

        report_progress(
            progress_callback,
            ArchiveProgress(phase=ArchiveProgressPhase.PUBLISHING),
        )
        _publish_archive(
            temporary_path,
            destination_path,
            conflict_mode=conflict_mode,
        )
    except BaseException:
        with suppress(OSError):
            temporary_path.unlink(missing_ok=True)
        raise

    destination_file_size = destination_path.stat().st_size
    finished_at = perf_counter()

    return ArchiveProcessingResult(
        transformation_result=result,
        source_file_size=source_file_size,
        destination_file_size=destination_file_size,
        elapsed_seconds=finished_at - started_at,
    )
