# SPDX-License-Identifier: GPL-3.0-or-later

import re
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock
from zipfile import ZIP_BZIP2, ZIP_DEFLATED, ZIP_STORED, ZipFile

import pytest

from cbzfit.archive import (
    InvalidArchiveError,
    build_manifest,
    contains_control_characters,
    has_supported_image_extension,
    inspect_cbz,
    validate_member_path,
    verify_archive_integrity,
)


def exact_message(message: str) -> str:
    """Return a regular expression that matches a complete error message."""
    return rf"^{re.escape(message)}$"


def create_archive(
    members: list[tuple[str, bytes]],
    *,
    compression: int = ZIP_STORED,
) -> BytesIO:
    """Create an in-memory ZIP archive and return its byte stream."""
    archive_stream = BytesIO()

    with ZipFile(
        archive_stream,
        mode="w",
        compression=compression,
    ) as archive:
        for filename, content in members:
            archive.writestr(filename, content)

    archive_stream.seek(0)
    return archive_stream


@pytest.mark.parametrize(
    "filename",
    [
        "001.jpg",
        "002.JPEG",
        "003.Png",
        "004.WEBP",
        "Chapter 01/005.JPG",
    ],
)
def test_supported_image_extensions_are_case_insensitive(
    filename: str,
) -> None:
    assert has_supported_image_extension(filename)


@pytest.mark.parametrize(
    "filename",
    [
        "ComicInfo.xml",
        "metadata.json",
        "page",
        "page.gif",
        "page.bmp",
        ".jpg", #Hidden file whose entire name is ".jpg"; suffix is empty
    ],
)
def test_unsupported_or_missing_image_extensions_are_rejected(
    filename: str,
) -> None:
    assert not has_supported_image_extension(filename)


@pytest.mark.parametrize(
    "value",
    [
        "page\n001.jpg",
        "page\r001.jpg",
        "page\t001.jpg",
        "page\x00001.jpg",
    ],
)
def test_control_characters_are_detected(value: str) -> None:
    assert contains_control_characters(value)


@pytest.mark.parametrize(
    "value",
    [
        "001.jpg",
        "Chapter 01/001.jpg",
        "進撃の巨人/第01話.jpg",
    ],
)
def test_printable_filenames_do_not_contain_control_characters(
    value: str,
) -> None:
    assert not contains_control_characters(value)


def test_member_path_is_normalized() -> None:
    result = validate_member_path(r"Chapter 01\001.jpg")

    assert result == "Chapter 01/001.jpg"


@pytest.mark.parametrize(
    "filename",
    [
        "/outside/page.jpg",
        "../outside/page.jpg",
        "chapter/../../outside/page.jpg",
        r"..\outside\page.jpg",
        r"C:\outside\page.jpg",
        "C:/outside/page.jpg",
        r"C:outside\page.jpg",
        r"\\server\share\page.jpg",
    ],
)
def test_unsafe_member_paths_are_rejected(filename: str) -> None:
    expected_message = (
        f"Archive member has an unsafe path: {filename!r}."
    )

    with pytest.raises(
        InvalidArchiveError,
        match=exact_message(expected_message),
    ):
        validate_member_path(filename)


def test_empty_member_path_is_rejected() -> None:
    expected_message = "Archive members must have a non-empty path."

    with pytest.raises(
        InvalidArchiveError,
        match=exact_message(expected_message),
    ):
        validate_member_path("")


def test_member_path_with_control_characters_is_rejected() -> None:
    filename = "chapter\n01/001.jpg"
    expected_message = (
        f"Archive member contains control characters: {filename!r}."
    )

    with pytest.raises(
        InvalidArchiveError,
        match=exact_message(expected_message),
    ):
        validate_member_path(filename)


def test_overlong_path_component_is_rejected() -> None:
    filename = f"{'a' * 10}.jpg"
    expected_message = (
        "Archive member contains a path component longer than "
        f"8 characters: {filename!r}."
    )

    with pytest.raises(
        InvalidArchiveError,
        match=exact_message(expected_message),
    ):
        validate_member_path(
            filename,
            max_component_length=8,
        )


def test_overlong_complete_path_is_rejected() -> None:
    filename = "chapter/001.jpg"
    expected_message = (
        f"Archive member path exceeds 10 characters: {filename!r}."
    )

    with pytest.raises(
        InvalidArchiveError,
        match=exact_message(expected_message),
    ):
        validate_member_path(
            filename,
            max_path_length=10,
        )


@pytest.mark.parametrize(
    ("arguments", "expected_message"),
    [
        (
            {"max_path_length": 0},
            "Maximum member path length must be a positive integer.",
        ),
        (
            {"max_component_length": 0},
            "Maximum path component length must be a positive integer.",
        ),
    ],
)
def test_invalid_member_path_limits_are_rejected(
    arguments: dict[str, int],
    expected_message: str,
) -> None:
    with pytest.raises(
        ValueError,
        match=exact_message(expected_message),
    ):
        validate_member_path(
            "001.jpg",
            **arguments,
        )


def test_manifest_classifies_members_and_preserves_order() -> None:
    archive_stream = create_archive(
        [
            ("001.JPG", b"first image"),
            ("ComicInfo.xml", b"<ComicInfo />"),
            ("002.png", b"second image"),
            ("notes.txt", b"notes"),
        ],
        compression=ZIP_DEFLATED,
    )

    with ZipFile(archive_stream, mode="r") as archive:
        manifest = build_manifest(archive)

    assert tuple(
        member.filename
        for member in manifest.file_members
    ) == (
        "001.JPG",
        "ComicInfo.xml",
        "002.png",
        "notes.txt",
    )

    assert tuple(
        member.filename
        for member in manifest.image_members
    ) == (
        "001.JPG",
        "002.png",
    )

    assert tuple(
        member.filename
        for member in manifest.other_members
    ) == (
        "ComicInfo.xml",
        "notes.txt",
    )

    assert manifest.image_count == 2
    assert manifest.total_uncompressed_size == (
        len(b"first image")
        + len(b"<ComicInfo />")
        + len(b"second image")
        + len(b"notes")
    )


@pytest.mark.parametrize(
    "compression",
    [
        ZIP_STORED,
        ZIP_DEFLATED,
    ],
)
def test_supported_zip_compression_methods_are_accepted(
    compression: int,
) -> None:
    archive_stream = create_archive(
        [("001.jpg", b"image")],
        compression=compression,
    )

    with ZipFile(archive_stream, mode="r") as archive:
        manifest = build_manifest(archive)

    assert manifest.image_count == 1


def test_directory_entries_are_excluded_from_manifest() -> None:
    archive_stream = create_archive(
        [
            ("Chapter 01/", b""),
            ("Chapter 01/001.jpg", b"image"),
        ]
    )

    with ZipFile(archive_stream, mode="r") as archive:
        manifest = build_manifest(archive)

    assert tuple(
        member.filename
        for member in manifest.file_members
    ) == ("Chapter 01/001.jpg",)


def test_empty_archive_is_rejected() -> None:
    archive_stream = create_archive([])
    expected_message = "The archive does not contain any files."

    with ZipFile(archive_stream, mode="r") as archive, pytest.raises(
        InvalidArchiveError,
        match=exact_message(expected_message),
    ):
        build_manifest(archive)


def test_directory_only_archive_is_rejected() -> None:
    archive_stream = create_archive(
        [("Chapter 01/", b"")]
    )
    expected_message = "The archive does not contain any files."

    with ZipFile(archive_stream, mode="r") as archive, pytest.raises(
        InvalidArchiveError,
        match=exact_message(expected_message),
    ):
        build_manifest(archive)


def test_archive_without_supported_images_is_rejected() -> None:
    archive_stream = create_archive(
        [
            ("ComicInfo.xml", b"<ComicInfo />"),
            ("notes.txt", b"notes"),
        ]
    )
    expected_message = (
        "The archive does not contain any supported images."
    )

    with ZipFile(archive_stream, mode="r") as archive, pytest.raises(
        InvalidArchiveError,
        match=exact_message(expected_message),
    ):
        build_manifest(archive)


def test_archive_file_count_limit_is_enforced() -> None:
    archive_stream = create_archive(
        [
            ("001.jpg", b"first"),
            ("002.jpg", b"second"),
        ]
    )
    expected_message = "The archive exceeds the permitted file count of 1."

    with ZipFile(archive_stream, mode="r") as archive, pytest.raises(
        InvalidArchiveError,
        match=exact_message(expected_message),
    ):
        build_manifest(
            archive,
            max_files=1,
        )


def test_per_file_size_limit_is_enforced() -> None:
    archive_stream = create_archive(
        [("001.jpg", b"12345")]
    )
    expected_message = (
        "Archive member exceeds the permitted size: '001.jpg'."
    )

    with ZipFile(archive_stream, mode="r") as archive, pytest.raises(
        InvalidArchiveError,
        match=exact_message(expected_message),
    ):
        build_manifest(
            archive,
            max_file_uncompressed_size=4,
        )


def test_total_uncompressed_size_limit_is_enforced() -> None:
    archive_stream = create_archive(
        [
            ("001.jpg", b"123"),
            ("002.jpg", b"456"),
        ]
    )
    expected_message = (
        "The archive's uncompressed contents exceed the permitted size."
    )

    with ZipFile(archive_stream, mode="r") as archive, pytest.raises(
        InvalidArchiveError,
        match=exact_message(expected_message),
    ):
        build_manifest(
            archive,
            max_total_uncompressed_size=5,
        )


def test_duplicate_normalized_paths_are_rejected() -> None:
    duplicate_filename = r"Chapter\001.jpg"
    archive_stream = create_archive(
        [
            ("Chapter/001.jpg", b"first"),
            ("temporary.jpg", b"second"),
        ]
    )
    expected_message = (
        "The archive contains a duplicate path: "
        f"{duplicate_filename!r}."
    )

    with ZipFile(archive_stream, mode="r") as archive:
        archive.infolist()[1].filename = duplicate_filename

        with pytest.raises(
            InvalidArchiveError,
            match=exact_message(expected_message),
        ):
            build_manifest(archive)


def test_encrypted_member_is_rejected() -> None:
    archive_stream = create_archive(
        [("001.jpg", b"image")]
    )
    expected_message = (
        "Encrypted archive members are not supported: '001.jpg'."
    )

    with ZipFile(archive_stream, mode="r") as archive:
        archive.infolist()[0].flag_bits |= 0x1

        with pytest.raises(
            InvalidArchiveError,
            match=exact_message(expected_message),
        ):
            build_manifest(archive)


def test_unsupported_compression_method_is_rejected() -> None:
    archive_stream = create_archive(
        [("001.jpg", b"image")]
    )
    expected_message = (
        "Unsupported ZIP compression method for '001.jpg'."
    )

    with ZipFile(archive_stream, mode="r") as archive:
        archive.infolist()[0].compress_type = ZIP_BZIP2

        with pytest.raises(
            InvalidArchiveError,
            match=exact_message(expected_message),
        ):
            build_manifest(archive)


@pytest.mark.parametrize(
    ("arguments", "expected_message"),
    [
        (
            {"max_files": 0},
            "Maximum file count must be a positive integer.",
        ),
        (
            {"max_file_uncompressed_size": 0},
            "Maximum file size must be a positive integer.",
        ),
        (
            {"max_total_uncompressed_size": 0},
            "Maximum total uncompressed size must be a positive integer.",
        ),
        (
            {"max_path_length": 0},
            "Maximum member path length must be a positive integer.",
        ),
        (
            {"max_component_length": 0},
            "Maximum path component length must be a positive integer.",
        ),
    ],
)
def test_invalid_manifest_limits_are_rejected(
    arguments: dict[str, int],
    expected_message: str,
) -> None:
    archive_stream = create_archive(
        [("001.jpg", b"image")]
    )

    with ZipFile(archive_stream, mode="r") as archive, pytest.raises(
        ValueError,
        match=exact_message(expected_message),
    ):
        build_manifest(
            archive,
            **arguments,
        )


def test_integrity_check_accepts_valid_archive() -> None:
    archive_stream = create_archive(
        [("001.jpg", b"image")]
    )

    with ZipFile(archive_stream, mode="r") as archive:
        verify_archive_integrity(archive)


def test_integrity_check_rejects_corrupt_member() -> None:
    archive = Mock(spec=ZipFile)
    archive.testzip.return_value = "002.jpg"
    expected_message = (
        "Archive member failed its integrity check: '002.jpg'."
    )

    with pytest.raises(
        InvalidArchiveError,
        match=exact_message(expected_message),
    ):
        verify_archive_integrity(archive)


def test_inspect_cbz_returns_manifest(tmp_path: Path) -> None:
    archive_path = tmp_path / "volume.cbz"

    with ZipFile(
        archive_path,
        mode="w",
        compression=ZIP_DEFLATED,
    ) as archive:
        archive.writestr("001.jpg", b"first")
        archive.writestr("ComicInfo.xml", b"<ComicInfo />")
        archive.writestr("002.png", b"second")

    manifest = inspect_cbz(archive_path)

    assert tuple(
        member.filename
        for member in manifest.image_members
    ) == (
        "001.jpg",
        "002.png",
    )

    assert tuple(
        member.filename
        for member in manifest.other_members
    ) == ("ComicInfo.xml",)


def test_inspect_cbz_rejects_missing_file(tmp_path: Path) -> None:
    archive_path = tmp_path / "missing.cbz"
    expected_message = (
        f"Archive file does not exist: {archive_path}."
    )

    with pytest.raises(
        InvalidArchiveError,
        match=exact_message(expected_message),
    ):
        inspect_cbz(archive_path)


def test_inspect_cbz_rejects_invalid_zip(tmp_path: Path) -> None:
    archive_path = tmp_path / "invalid.cbz"
    archive_path.write_text(
        "This is not a ZIP archive.",
        encoding="utf-8",
    )
    expected_message = (
        f"File is not a valid ZIP archive: {archive_path}."
    )

    with pytest.raises(
        InvalidArchiveError,
        match=exact_message(expected_message),
    ):
        inspect_cbz(archive_path)
