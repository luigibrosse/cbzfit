# SPDX-License-Identifier: GPL-3.0-or-later

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath
from unicodedata import category
from zipfile import (
    ZIP_DEFLATED,
    ZIP_STORED,
    BadZipFile,
    ZipFile,
    ZipInfo,
)

from cbzfit.decode import EXTENSION_FORMATS

SUPPORTED_ZIP_COMPRESSION = frozenset(
    {
        ZIP_STORED,
        ZIP_DEFLATED,
    }
)

DEFAULT_MAX_ARCHIVE_FILES = 1_000
DEFAULT_MAX_FILE_UNCOMPRESSED_SIZE = 512 * 1024**2
DEFAULT_MAX_TOTAL_UNCOMPRESSED_SIZE = 4 * 1024**3
DEFAULT_MAX_MEMBER_PATH_LENGTH = 1_024
DEFAULT_MAX_PATH_COMPONENT_LENGTH = 255
DEFAULT_MEMBER_READ_CHUNK_SIZE = 64 * 1024

ZipDateTime = tuple[int, int, int, int, int, int]


class InvalidArchiveError(ValueError):
    """Raised when an archive does not satisfy CBZFit requirements."""


class MemberDateTimeMode(StrEnum):
    """Define supported output archive-member timestamp modes."""

    PRESERVE = "preserve"
    MODIFIED = "modified"
    FIXED = "fixed"


def validate_zip_date_time(
    date_time: ZipDateTime,
) -> ZipDateTime:
    """Validate and return a timestamp representable by the ZIP format."""
    if (
        not isinstance(date_time, tuple)
        or len(date_time) != 6
        or any(
            not isinstance(value, int)
            or isinstance(value, bool)
            for value in date_time
        )
    ):
        raise ValueError(
            "ZIP member timestamp must contain exactly six integers."
        )

    try:
        timestamp = datetime(
            *date_time,
            tzinfo=UTC,
        )
    except (TypeError, ValueError) as error:
        raise ValueError(
            "ZIP member timestamp must be a valid date and time."
        ) from error

    if not 1980 <= timestamp.year <= 2107:
        raise ValueError(
            "ZIP member timestamp year must be between 1980 and 2107."
        )

    if timestamp.second % 2:
        raise ValueError(
            "ZIP member timestamp seconds must use two-second precision."
        )

    return date_time


@dataclass(frozen=True)
class MemberDateTimePolicy:
    """Define how output archive-member timestamps are selected."""

    mode: MemberDateTimeMode = MemberDateTimeMode.MODIFIED
    fixed_date_time: ZipDateTime | None = None

    def __post_init__(self) -> None:
        """Validate the archive-member timestamp policy."""
        if not isinstance(self.mode, MemberDateTimeMode):
            raise TypeError(
                f"Archive-member timestamp mode must be a "
                f"MemberDateTimeMode, not {type(self.mode).__name__}."
            )

        if (
            self.mode is MemberDateTimeMode.FIXED
            and self.fixed_date_time is None
        ):
            raise ValueError(
                "A fixed timestamp is required in fixed timestamp mode."
            )

        if (
            self.mode is not MemberDateTimeMode.FIXED
            and self.fixed_date_time is not None
        ):
            raise ValueError(
                "A fixed timestamp can only be used in fixed timestamp mode."
            )

        if self.fixed_date_time is not None:
            validate_zip_date_time(self.fixed_date_time)


@dataclass(frozen=True)
class ArchivePathLimits:
    """Store limits applied to archive-member paths."""

    max_path_length: int = DEFAULT_MAX_MEMBER_PATH_LENGTH
    max_component_length: int = DEFAULT_MAX_PATH_COMPONENT_LENGTH

    def __post_init__(self) -> None:
        """Validate archive-member path limits."""
        if self.max_path_length <= 0:
            raise ValueError(
                "Maximum member path length must be a positive integer."
            )

        if self.max_component_length <= 0:
            raise ValueError(
                "Maximum path component length must be a positive integer."
            )


@dataclass(frozen=True)
class ArchiveManifest:
    """Describe the validated contents of a CBZ archive."""

    file_members: tuple[ZipInfo, ...]
    image_members: tuple[ZipInfo, ...]
    other_members: tuple[ZipInfo, ...]

    @property
    def image_count(self) -> int:
        """Return the number of supported images in the archive."""
        return len(self.image_members)

    @property
    def total_uncompressed_size(self) -> int:
        """Return the total uncompressed size of all file members."""
        return sum(
            file_member.file_size
            for file_member in self.file_members
        )


@dataclass(frozen=True)
class ArchiveReadLimits:
    """Store limits applied while reading decompressed archive members."""

    max_file_size: int = DEFAULT_MAX_FILE_UNCOMPRESSED_SIZE
    max_total_size: int = DEFAULT_MAX_TOTAL_UNCOMPRESSED_SIZE

    def __post_init__(self) -> None:
        """Validate archive-member read limits."""
        if self.max_file_size <= 0:
            raise ValueError(
                "Maximum file size must be a positive integer."
            )

        if self.max_total_size <= 0:
            raise ValueError(
                "Maximum total uncompressed size must be a positive integer."
            )


@dataclass
class ArchiveReadState:
    """Track the total number of successfully read decompressed bytes."""

    total_size: int = 0

    def __post_init__(self) -> None:
        """Validate the initial archive-member read state."""
        if self.total_size < 0:
            raise ValueError(
                "Total read size must not be negative."
            )


def has_supported_image_extension(filename: str) -> bool:
    """Return whether a filename has a supported image extension.

    The check is case-insensitive and does not inspect the file contents.
    """
    return (
        PurePosixPath(filename).suffix.lower()
        in EXTENSION_FORMATS
    )


def contains_control_characters(value: str) -> bool:
    """Return whether text contains Unicode control characters."""
    return any(
        category(character) == "Cc"
        for character in value
    )


def validate_member_path(
    filename: str,
    *,
    limits: ArchivePathLimits | None = None,
) -> str:
    """Validate and return a normalized archive member path.

    A member path must not be empty, absolute, drive-qualified, use a network
    share, contain control characters, contain an overlong path component, or
    contain a parent-directory component ("..").
    """
    path_limits = limits or ArchivePathLimits()

    if not filename:
        raise InvalidArchiveError(
            "Archive members must have a non-empty path."
        )

    if contains_control_characters(filename):
        raise InvalidArchiveError(
            f"Archive member contains control characters: {filename!r}."
        )

    normalized_filename = filename.replace("\\", "/")
    posix_path = PurePosixPath(normalized_filename)
    windows_path = PureWindowsPath(filename)

    if (
        posix_path.is_absolute()
        or windows_path.drive
        or ".." in posix_path.parts
    ):
        raise InvalidArchiveError(
            f"Archive member has an unsafe path: {filename!r}."
        )

    if any(
        len(component) > path_limits.max_component_length
        for component in posix_path.parts
    ):
        raise InvalidArchiveError(
            f"Archive member contains a path component longer than "
            f"{path_limits.max_component_length} characters: "
            f"{filename!r}."
        )

    normalized_path = posix_path.as_posix()

    if len(normalized_path) > path_limits.max_path_length:
        raise InvalidArchiveError(
            f"Archive member path exceeds "
            f"{path_limits.max_path_length} characters: {filename!r}."
        )

    return normalized_path


def read_member_data(
    archive: ZipFile,
    member: ZipInfo,
    *,
    limits: ArchiveReadLimits,
    state: ArchiveReadState,
    chunk_size: int = DEFAULT_MEMBER_READ_CHUNK_SIZE,
) -> bytes:
    """Read an archive member while enforcing actual decompressed limits.

    Reading the member completely allows the ZIP implementation to validate
    the compressed data and CRC. The cumulative state is updated only after
    the complete member has been read successfully.
    """
    if chunk_size <= 0:
        raise ValueError(
            "Archive member read chunk size must be a positive integer."
        )

    if state.total_size > limits.max_total_size:
        raise InvalidArchiveError(
            "The archive's uncompressed contents exceed the permitted "
            "size while reading."
        )

    chunks: list[bytes] = []
    member_size = 0

    try:
        with archive.open(member, mode="r") as member_stream:
            while chunk := member_stream.read(chunk_size):
                member_size += len(chunk)

                if member_size > limits.max_file_size:
                    raise InvalidArchiveError(
                        "Archive member exceeds the permitted size while "
                        f"reading: {member.filename!r}."
                    )

                if (
                    state.total_size + member_size
                    > limits.max_total_size
                ):
                    raise InvalidArchiveError(
                        "The archive's uncompressed contents exceed the "
                        "permitted size while reading."
                    )

                chunks.append(chunk)

    except InvalidArchiveError:
        raise
    except (BadZipFile, EOFError, OSError, RuntimeError) as error:
        raise InvalidArchiveError(
            f"Failed to read archive member: {member.filename!r}."
        ) from error

    state.total_size += member_size

    return b"".join(chunks)


def current_zip_date_time() -> ZipDateTime:
    """Return the current local time using ZIP two-second precision."""
    current_time = datetime.now(UTC).astimezone()
    normalized_second = (
        current_time.second
        - current_time.second % 2
    )

    date_time = (
        current_time.year,
        current_time.month,
        current_time.day,
        current_time.hour,
        current_time.minute,
        normalized_second,
    )

    return validate_zip_date_time(date_time)


def resolve_member_date_time(
    member: ZipInfo,
    *,
    policy: MemberDateTimePolicy,
    transformed: bool,
) -> ZipDateTime:
    """Resolve a valid output timestamp for an archive member.

    Preserve mode always retains the source timestamp.

    Modified mode retains the source timestamp for unchanged members and
    generates the current timestamp for transformed members.

    Fixed mode applies the configured fixed timestamp to every member.
    """
    if policy.mode is MemberDateTimeMode.PRESERVE:
        return validate_zip_date_time(member.date_time)

    if policy.mode is MemberDateTimeMode.MODIFIED:
        if transformed:
            return current_zip_date_time()

        return validate_zip_date_time(member.date_time)

    if policy.mode is MemberDateTimeMode.FIXED:
        if policy.fixed_date_time is None:
            raise RuntimeError(
                "Fixed timestamp policy does not contain a timestamp."
            )

        return policy.fixed_date_time

    raise ValueError(
        f"Unsupported archive-member timestamp mode: {policy.mode!r}."
    )


def _clone_member_info(
    member: ZipInfo,
    *,
    date_time: ZipDateTime,
    compression: int,
    path_limits: ArchivePathLimits,
    filename: str | None = None,
) -> ZipInfo:
    """Create safe output metadata using resolved output policies.

    The caller must provide a validated ZIP-compatible timestamp, select the
    output compression method, and supply the applicable member-path limits.

    Input-specific fields such as CRC, compressed size, uncompressed size,
    encryption flags, data-descriptor flags, and raw extra records are not
    copied. ZipFile calculates structural fields when writing the output
    member.
    """
    if compression not in SUPPORTED_ZIP_COMPRESSION:
        raise ValueError(
            "Unsupported ZIP compression method for output."
        )

    candidate_filename = (
        member.filename
        if filename is None
        else filename
    )
    output_filename = validate_member_path(
        candidate_filename,
        limits=path_limits,
    )

    output_member = ZipInfo(
        filename=output_filename,
        date_time=date_time,
    )
    output_member.compress_type = compression
    output_member.comment = member.comment
    output_member.create_system = member.create_system
    output_member.internal_attr = member.internal_attr
    output_member.external_attr = member.external_attr

    return output_member


def write_member_data(
    archive: ZipFile,
    member: ZipInfo,
    data: bytes,
    *,
    compression: int,
    date_time_policy: MemberDateTimePolicy,
    transformed: bool,
    path_limits: ArchivePathLimits,
    filename: str | None = None,
) -> ZipInfo:
    """Resolve output metadata and write archive-member data.

    Compression and path limits are selected by the caller. The timestamp is
    resolved from the supplied policy and transformation state.
    """
    date_time = resolve_member_date_time(
        member,
        policy=date_time_policy,
        transformed=transformed,
    )

    output_member = _clone_member_info(
        member,
        date_time=date_time,
        compression=compression,
        path_limits=path_limits,
        filename=filename,
    )

    archive.writestr(
        output_member,
        data,
    )

    return output_member


def verify_archive_integrity(archive: ZipFile) -> None:
    """Verify the CRC and file header of every archive member."""
    corrupt_member = archive.testzip()

    if corrupt_member is not None:
        raise InvalidArchiveError(
            f"Archive member failed its integrity check: "
            f"{corrupt_member!r}."
        )


def build_manifest(
    archive: ZipFile,
    *,
    max_files: int = DEFAULT_MAX_ARCHIVE_FILES,
    max_file_uncompressed_size: int = (
        DEFAULT_MAX_FILE_UNCOMPRESSED_SIZE
    ),
    max_total_uncompressed_size: int = (
        DEFAULT_MAX_TOTAL_UNCOMPRESSED_SIZE
    ),
    path_limits: ArchivePathLimits | None = None,
) -> ArchiveManifest:
    """Validate an open CBZ archive and describe its contents."""
    member_path_limits = (
        ArchivePathLimits()
        if path_limits is None
        else path_limits
    )

    if max_files <= 0:
        raise ValueError(
            "Maximum file count must be a positive integer."
        )

    if max_file_uncompressed_size <= 0:
        raise ValueError(
            "Maximum file size must be a positive integer."
        )

    if max_total_uncompressed_size <= 0:
        raise ValueError(
            "Maximum total uncompressed size must be a positive integer."
        )

    file_members = tuple(
        member
        for member in archive.infolist()
        if not member.is_dir()
    )

    if not file_members:
        raise InvalidArchiveError(
            "The archive does not contain any files."
        )

    if len(file_members) > max_files:
        raise InvalidArchiveError(
            f"The archive exceeds the permitted file count of {max_files}."
        )

    total_uncompressed_size = sum(
        member.file_size
        for member in file_members
    )

    if total_uncompressed_size > max_total_uncompressed_size:
        raise InvalidArchiveError(
            "The archive's uncompressed contents exceed the permitted size."
        )

    seen_paths: set[str] = set()

    for member in file_members:
        normalized_path = validate_member_path(
            member.filename,
            limits=member_path_limits,
        )

        if normalized_path in seen_paths:
            raise InvalidArchiveError(
                f"The archive contains a duplicate path: "
                f"{member.filename!r}."
            )

        seen_paths.add(normalized_path)

        if member.flag_bits & 0x1:
            raise InvalidArchiveError(
                f"Encrypted archive members are not supported: "
                f"{member.filename!r}."
            )

        if member.compress_type not in SUPPORTED_ZIP_COMPRESSION:
            raise InvalidArchiveError(
                f"Unsupported ZIP compression method for "
                f"{member.filename!r}."
            )

        if member.file_size > max_file_uncompressed_size:
            raise InvalidArchiveError(
                f"Archive member exceeds the permitted size: "
                f"{member.filename!r}."
            )

    image_members: list[ZipInfo] = []
    other_members: list[ZipInfo] = []

    for member in file_members:
        if has_supported_image_extension(member.filename):
            image_members.append(member)
        else:
            other_members.append(member)

    if not image_members:
        raise InvalidArchiveError(
            "The archive does not contain any supported images."
        )

    return ArchiveManifest(
        file_members=file_members,
        image_members=tuple(image_members),
        other_members=tuple(other_members),
    )


def inspect_cbz(
    archive_path: Path,
    *,
    max_files: int = DEFAULT_MAX_ARCHIVE_FILES,
    max_file_uncompressed_size: int = (
        DEFAULT_MAX_FILE_UNCOMPRESSED_SIZE
    ),
    max_total_uncompressed_size: int = (
        DEFAULT_MAX_TOTAL_UNCOMPRESSED_SIZE
    ),
    path_limits: ArchivePathLimits | None = None,
) -> ArchiveManifest:
    """Open, validate, and describe a CBZ archive."""
    if not archive_path.is_file():
        raise InvalidArchiveError(
            f"Archive file does not exist: {archive_path}."
        )

    try:
        with ZipFile(archive_path, mode="r") as archive:
            return build_manifest(
                archive,
                max_files=max_files,
                max_file_uncompressed_size=max_file_uncompressed_size,
                max_total_uncompressed_size=max_total_uncompressed_size,
                path_limits=path_limits,
            )

    except BadZipFile as error:
        raise InvalidArchiveError(
            f"File is not a valid ZIP archive: {archive_path}."
        ) from error
