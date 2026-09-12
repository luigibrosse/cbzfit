# SPDX-License-Identifier: GPL-3.0-or-later

import stat
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
ARCHIVE_MEMBER_READ_ERRORS = (BadZipFile, EOFError, OSError, RuntimeError)

ZIP_CREATOR_DOS = 0
ZIP_CREATOR_UNIX = 3
DEFAULT_UNIX_FILE_PERMISSIONS = 0o644

DOS_ATTRIBUTE_READ_ONLY = 0x01
DOS_ATTRIBUTE_HIDDEN = 0x02
DOS_ATTRIBUTE_SYSTEM = 0x04
DOS_ATTRIBUTE_VOLUME_LABEL = 0x08
DOS_ATTRIBUTE_DIRECTORY = 0x10
DOS_ATTRIBUTE_ARCHIVE = 0x20
DOS_ATTRIBUTE_DEVICE = 0x40
DOS_ATTRIBUTE_NORMAL = 0x80
DOS_ATTRIBUTE_TEMPORARY = 0x100
DOS_ATTRIBUTE_SPARSE_FILE = 0x200
DOS_ATTRIBUTE_REPARSE_POINT = 0x400
DOS_ATTRIBUTE_COMPRESSED = 0x800
DOS_ATTRIBUTE_OFFLINE = 0x1000
DOS_ATTRIBUTE_NOT_CONTENT_INDEXED = 0x2000
DOS_ATTRIBUTE_ENCRYPTED = 0x4000
DOS_ATTRIBUTE_VIRTUAL = 0x10000
DOS_SAFE_FILE_ATTRIBUTES = (
    DOS_ATTRIBUTE_READ_ONLY
    | DOS_ATTRIBUTE_HIDDEN
    | DOS_ATTRIBUTE_SYSTEM
    | DOS_ATTRIBUTE_ARCHIVE
)
DOS_REJECTED_ATTRIBUTES = (
    DOS_ATTRIBUTE_VOLUME_LABEL
    | DOS_ATTRIBUTE_DEVICE
    | DOS_ATTRIBUTE_REPARSE_POINT
)

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


def validate_member_type(member: ZipInfo) -> None:
    """Validate that a ZIP member is a regular file or directory marker."""
    is_directory_path = member.filename.endswith("/")

    if member.create_system == ZIP_CREATOR_UNIX:
        unix_mode = member.external_attr >> 16
        file_type = stat.S_IFMT(unix_mode)

        if file_type == 0:
            return

        if stat.S_ISREG(unix_mode):
            if is_directory_path:
                raise InvalidArchiveError(
                    "ZIP member type conflicts with its path: "
                    f"{member.filename!r}."
                )
            return

        if stat.S_ISDIR(unix_mode):
            if not is_directory_path:
                raise InvalidArchiveError(
                    "ZIP member type conflicts with its path: "
                    f"{member.filename!r}."
                )
            return

        special_types = (
            (stat.S_ISLNK, "symbolic link"),
            (stat.S_ISCHR, "character device"),
            (stat.S_ISBLK, "block device"),
            (stat.S_ISFIFO, "FIFO"),
            (stat.S_ISSOCK, "socket"),
        )
        for type_check, type_name in special_types:
            if type_check(unix_mode):
                raise InvalidArchiveError(
                    f"Unsupported ZIP member type for {member.filename!r}: "
                    f"{type_name}."
                )

        raise InvalidArchiveError(
            f"Unsupported ZIP member type for {member.filename!r}."
        )

    if member.create_system == ZIP_CREATOR_DOS:
        dos_attributes = member.external_attr & 0xFFFF

        if (
            dos_attributes & DOS_ATTRIBUTE_NORMAL
            and dos_attributes != DOS_ATTRIBUTE_NORMAL
        ):
            raise InvalidArchiveError(
                "ZIP member has inconsistent DOS attributes: "
                f"{member.filename!r}."
            )

        if dos_attributes & DOS_REJECTED_ATTRIBUTES:
            raise InvalidArchiveError(
                f"Unsupported ZIP member type for {member.filename!r}."
            )

        has_directory_attribute = bool(
            dos_attributes & DOS_ATTRIBUTE_DIRECTORY
        )
        if has_directory_attribute != is_directory_path:
            raise InvalidArchiveError(
                "ZIP member type conflicts with its path: "
                f"{member.filename!r}."
            )

        return

    # Unknown creator systems carry no type metadata that CBZFit can
    # interpret safely. The validated trailing slash determines whether
    # the member is a directory marker.
    return


def resolve_output_member_attributes(member: ZipInfo) -> tuple[int, int]:
    """Return a safe creator system and external attributes for output."""
    if member.create_system == ZIP_CREATOR_UNIX:
        source_mode = member.external_attr >> 16
        permissions = stat.S_IMODE(source_mode) & 0o777
        if permissions == 0:
            permissions = DEFAULT_UNIX_FILE_PERMISSIONS
        output_mode = stat.S_IFREG | permissions
        return ZIP_CREATOR_UNIX, output_mode << 16

    if member.create_system == ZIP_CREATOR_DOS:
        source_attributes = member.external_attr & 0xFFFF
        safe_attributes = source_attributes & DOS_SAFE_FILE_ATTRIBUTES
        if safe_attributes == 0:
            safe_attributes = DOS_ATTRIBUTE_NORMAL
        return ZIP_CREATOR_DOS, safe_attributes

    output_mode = stat.S_IFREG | DEFAULT_UNIX_FILE_PERMISSIONS
    return ZIP_CREATOR_UNIX, output_mode << 16


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
    except ARCHIVE_MEMBER_READ_ERRORS as error:
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
        if policy.fixed_date_time is None:  # pragma: no cover - validated by policy
            raise RuntimeError(
                "Fixed timestamp policy does not contain a timestamp."
            )

        return policy.fixed_date_time

    raise ValueError(  # pragma: no cover - guards future enum members
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
    output_member.internal_attr = member.internal_attr
    (
        output_member.create_system,
        output_member.external_attr,
    ) = resolve_output_member_attributes(member)

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

    all_members = tuple(archive.infolist())
    file_members: list[ZipInfo] = []
    seen_paths: set[str] = set()

    for member in all_members:
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

        validate_member_type(member)

        if member.filename.endswith("/"):
            continue

        file_members.append(member)

    file_members_tuple = tuple(file_members)

    if not file_members_tuple:
        raise InvalidArchiveError(
            "The archive does not contain any files."
        )

    if len(file_members_tuple) > max_files:
        raise InvalidArchiveError(
            f"The archive exceeds the permitted file count of {max_files}."
        )

    total_uncompressed_size = sum(
        member.file_size
        for member in file_members_tuple
    )

    if total_uncompressed_size > max_total_uncompressed_size:
        raise InvalidArchiveError(
            "The archive's uncompressed contents exceed the permitted size."
        )

    for member in file_members_tuple:
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

    for member in file_members_tuple:
        if has_supported_image_extension(member.filename):
            image_members.append(member)
        else:
            other_members.append(member)

    if not image_members:
        raise InvalidArchiveError(
            "The archive does not contain any supported images."
        )

    return ArchiveManifest(
        file_members=file_members_tuple,
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
