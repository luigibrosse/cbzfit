# SPDX-License-Identifier: GPL-3.0-or-later

from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from unicodedata import category
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile, ZipInfo

SUPPORTED_IMAGE_EXTENSIONS = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
    }
)

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


class InvalidArchiveError(ValueError):
    """Raised when an input archive does not satisfy CBZFit requirements."""


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


def has_supported_image_extension(filename: str) -> bool:
    """Return whether a filename has a supported image extension.

    The check is case-insensitive and does not inspect the file contents.
    """
    return PurePosixPath(filename).suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS


def contains_control_characters(value: str) -> bool:
    """Return whether text contains Unicode control characters."""
    return any(category(character) == "Cc" for character in value)


def validate_member_path(
    filename: str,
    *,
    max_path_length: int = DEFAULT_MAX_MEMBER_PATH_LENGTH,
    max_component_length: int = DEFAULT_MAX_PATH_COMPONENT_LENGTH,
) -> str:
    """Validate and return a normalized archive member path.

    A member path must not be empty, absolute, drive-qualified, use a network
    share, contain control characters, contain an overlong path component, or
    contain a parent-directory component ("..").
    """
    if max_path_length <= 0:
        raise ValueError(
            "Maximum member path length must be a positive integer."
        )

    if max_component_length <= 0:
        raise ValueError(
            "Maximum path component length must be a positive integer."
        )

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
        len(component) > max_component_length
        for component in posix_path.parts
    ):
        raise InvalidArchiveError(
            f"Archive member contains a path component longer than "
            f"{max_component_length} characters: {filename!r}."
        )

    normalized_path = posix_path.as_posix()

    if len(normalized_path) > max_path_length:
        raise InvalidArchiveError(
            f"Archive member path exceeds {max_path_length} characters: "
            f"{filename!r}."
        )

    return normalized_path


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
    max_file_uncompressed_size: int = DEFAULT_MAX_FILE_UNCOMPRESSED_SIZE,
    max_total_uncompressed_size: int = DEFAULT_MAX_TOTAL_UNCOMPRESSED_SIZE,
    max_path_length: int = DEFAULT_MAX_MEMBER_PATH_LENGTH,
    max_component_length: int = DEFAULT_MAX_PATH_COMPONENT_LENGTH,
) -> ArchiveManifest:
    """Validate an open CBZ archive and describe its contents."""
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

    if max_path_length <= 0:
        raise ValueError(
            "Maximum member path length must be a positive integer."
        )

    if max_component_length <= 0:
        raise ValueError(
            "Maximum path component length must be a positive integer."
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
            max_path_length=max_path_length,
            max_component_length=max_component_length,
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
    max_file_uncompressed_size: int = DEFAULT_MAX_FILE_UNCOMPRESSED_SIZE,
    max_total_uncompressed_size: int = DEFAULT_MAX_TOTAL_UNCOMPRESSED_SIZE,
    max_path_length: int = DEFAULT_MAX_MEMBER_PATH_LENGTH,
    max_component_length: int = DEFAULT_MAX_PATH_COMPONENT_LENGTH,
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
                max_path_length=max_path_length,
                max_component_length=max_component_length,
            )
    except BadZipFile as error:
        raise InvalidArchiveError(
            f"File is not a valid ZIP archive: {archive_path}."
        ) from error
