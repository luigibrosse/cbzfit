# SPDX-License-Identifier: GPL-3.0-or-later

import re
from io import BytesIO
from pathlib import Path
from typing import Self
from unittest.mock import Mock
from zipfile import (
    ZIP_BZIP2,
    ZIP_DEFLATED,
    ZIP_STORED,
    BadZipFile,
    ZipFile,
    ZipInfo,
)

import pytest

from cbzfit.archive import (
    DEFAULT_MEMBER_READ_CHUNK_SIZE,
    ArchiveReadLimits,
    ArchiveReadState,
    InvalidArchiveError,
    build_manifest,
    contains_control_characters,
    has_supported_image_extension,
    inspect_cbz,
    read_member_data,
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

class TrackingStream:
    """Track reads and closure of an in-memory member stream."""

    def __init__(self, data: bytes) -> None:
        self._stream = BytesIO(data)
        self.read_sizes: list[int] = []
        self.closed = False

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: object,
        exception: object,
        traceback: object,
    ) -> None:
        self.closed = True
        self._stream.close()

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return self._stream.read(size)


class FailingStream:
    """Raise a configured exception when member data is read."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.closed = False

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: object,
        exception: object,
        traceback: object,
    ) -> None:
        self.closed = True

    def read(self, size: int = -1) -> bytes:
        raise self.error


class TestHasSupportedImageExtension:

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
        self,
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
            ".jpg",  # Hidden file whose entire name is ".jpg"; suffix is empty
        ],
    )
    def test_unsupported_or_missing_image_extensions_are_rejected(
        self,
        filename: str,
    ) -> None:
        assert not has_supported_image_extension(filename)

class TestContainsControlCharacters:

    @pytest.mark.parametrize(
        "value",
        [
            "page\n001.jpg",
            "page\r001.jpg",
            "page\t001.jpg",
            "page\x00001.jpg",
        ],
    )
    def test_control_characters_are_detected(
        self,
        value: str,
    ) -> None:
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
        self,
        value: str,
    ) -> None:
        assert not contains_control_characters(value)

class TestValidateMemberPath:

    def test_member_path_is_normalized(self) -> None:
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
    def test_unsafe_member_paths_are_rejected(
        self,
        filename: str,
    ) -> None:
        expected_message = (
            f"Archive member has an unsafe path: {filename!r}."
        )

        with pytest.raises(
            InvalidArchiveError,
            match=exact_message(expected_message),
        ):
            validate_member_path(filename)


    def test_empty_member_path_is_rejected(self) -> None:
        expected_message = "Archive members must have a non-empty path."

        with pytest.raises(
            InvalidArchiveError,
            match=exact_message(expected_message),
        ):
            validate_member_path("")


    def test_member_path_with_control_characters_is_rejected(self) -> None:
        filename = "chapter\n01/001.jpg"
        expected_message = (
            f"Archive member contains control characters: {filename!r}."
        )

        with pytest.raises(
            InvalidArchiveError,
            match=exact_message(expected_message),
        ):
            validate_member_path(filename)


    def test_overlong_path_component_is_rejected(self) -> None:
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


    def test_overlong_complete_path_is_rejected(self) -> None:
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
        self,
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

class TestArchiveReadLimits:
    def test_default_limits_are_created(self) -> None:
        limits = ArchiveReadLimits()

        assert limits.max_file_size > 0
        assert limits.max_total_size > 0

    @pytest.mark.parametrize(
        ("arguments", "expected_message"),
        [
            (
                {"max_file_size": 0},
                "Maximum file size must be a positive integer.",
            ),
            (
                {"max_file_size": -1},
                "Maximum file size must be a positive integer.",
            ),
            (
                {"max_total_size": 0},
                "Maximum total uncompressed size must be a positive integer.",
            ),
            (
                {"max_total_size": -1},
                "Maximum total uncompressed size must be a positive integer.",
            ),
        ],
    )
    def test_invalid_limits_are_rejected(
        self,
        arguments: dict[str, int],
        expected_message: str,
    ) -> None:
        with pytest.raises(
            ValueError,
            match=exact_message(expected_message),
        ):
            ArchiveReadLimits(**arguments)


class TestArchiveReadState:
    def test_default_state_starts_at_zero(self) -> None:
        state = ArchiveReadState()

        assert state.total_size == 0

    def test_existing_total_is_retained(self) -> None:
        state = ArchiveReadState(total_size=10)

        assert state.total_size == 10

    def test_negative_total_is_rejected(self) -> None:
        expected_message = "Total read size must not be negative."

        with pytest.raises(
            ValueError,
            match=exact_message(expected_message),
        ):
            ArchiveReadState(total_size=-1)


class TestReadMemberData:
    def test_member_data_is_returned(self) -> None:
        content = b"archive member content"
        archive_stream = create_archive(
            [("001.jpg", content)],
            compression=ZIP_DEFLATED,
        )
        state = ArchiveReadState()

        with ZipFile(archive_stream, mode="r") as archive:
            result = read_member_data(
                archive,
                archive.getinfo("001.jpg"),
                limits=ArchiveReadLimits(),
                state=state,
            )

        assert result == content
        assert state.total_size == len(content)

    def test_empty_member_is_returned(self) -> None:
        archive_stream = create_archive(
            [("ComicInfo.xml", b"")],
        )
        state = ArchiveReadState()

        with ZipFile(archive_stream, mode="r") as archive:
            result = read_member_data(
                archive,
                archive.getinfo("ComicInfo.xml"),
                limits=ArchiveReadLimits(),
                state=state,
            )

        assert result == b""
        assert state.total_size == 0

    def test_multiple_chunks_are_combined_and_stream_is_closed(self) -> None:
        content = b"abcdefghij"
        member = ZipInfo("001.jpg")
        member_stream = TrackingStream(content)
        archive = Mock(spec=ZipFile)
        archive.open.return_value = member_stream
        state = ArchiveReadState()

        result = read_member_data(
            archive,
            member,
            limits=ArchiveReadLimits(
                max_file_size=20,
                max_total_size=20,
            ),
            state=state,
            chunk_size=3,
        )

        assert result == content
        assert state.total_size == len(content)
        assert member_stream.read_sizes == [3, 3, 3, 3, 3]
        assert member_stream.closed is True
        archive.open.assert_called_once_with(member, mode="r")

    def test_member_at_file_size_limit_is_accepted(self) -> None:
        content = b"12345"
        archive_stream = create_archive([("001.jpg", content)])
        state = ArchiveReadState()

        with ZipFile(archive_stream, mode="r") as archive:
            result = read_member_data(
                archive,
                archive.getinfo("001.jpg"),
                limits=ArchiveReadLimits(
                    max_file_size=len(content),
                    max_total_size=len(content),
                ),
                state=state,
            )

        assert result == content
        assert state.total_size == len(content)

    def test_actual_file_size_limit_is_enforced(self) -> None:
        archive_stream = create_archive([("001.jpg", b"12345")])
        state = ArchiveReadState()
        expected_message = (
            "Archive member exceeds the permitted size while reading: "
            "'001.jpg'."
        )

        with ZipFile(archive_stream, mode="r") as archive, pytest.raises(
            InvalidArchiveError,
            match=exact_message(expected_message),
        ):
            read_member_data(
                archive,
                archive.getinfo("001.jpg"),
                limits=ArchiveReadLimits(
                    max_file_size=4,
                    max_total_size=10,
                ),
                state=state,
                chunk_size=2,
            )

        assert state.total_size == 0

    def test_member_at_total_size_limit_is_accepted(self) -> None:
        archive_stream = create_archive(
            [
                ("001.jpg", b"123"),
                ("002.jpg", b"45"),
            ]
        )
        limits = ArchiveReadLimits(
            max_file_size=5,
            max_total_size=5,
        )
        state = ArchiveReadState()

        with ZipFile(archive_stream, mode="r") as archive:
            first_result = read_member_data(
                archive,
                archive.getinfo("001.jpg"),
                limits=limits,
                state=state,
            )
            second_result = read_member_data(
                archive,
                archive.getinfo("002.jpg"),
                limits=limits,
                state=state,
            )

        assert first_result == b"123"
        assert second_result == b"45"
        assert state.total_size == 5

    def test_actual_total_size_limit_is_enforced(self) -> None:
        archive_stream = create_archive(
            [
                ("001.jpg", b"123"),
                ("002.jpg", b"456"),
            ]
        )
        limits = ArchiveReadLimits(
            max_file_size=5,
            max_total_size=5,
        )
        state = ArchiveReadState()
        expected_message = (
            "The archive's uncompressed contents exceed the permitted "
            "size while reading."
        )

        with ZipFile(archive_stream, mode="r") as archive:
            first_result = read_member_data(
                archive,
                archive.getinfo("001.jpg"),
                limits=limits,
                state=state,
            )

            with pytest.raises(
                InvalidArchiveError,
                match=exact_message(expected_message),
            ):
                read_member_data(
                    archive,
                    archive.getinfo("002.jpg"),
                    limits=limits,
                    state=state,
                    chunk_size=2,
                )

        assert first_result == b"123"
        assert state.total_size == 3

    def test_total_size_already_above_limit_is_rejected(self) -> None:
        archive = Mock(spec=ZipFile)
        member = ZipInfo("001.jpg")
        state = ArchiveReadState(total_size=6)
        expected_message = (
            "The archive's uncompressed contents exceed the permitted "
            "size while reading."
        )

        with pytest.raises(
            InvalidArchiveError,
            match=exact_message(expected_message),
        ):
            read_member_data(
                archive,
                member,
                limits=ArchiveReadLimits(
                    max_file_size=5,
                    max_total_size=5,
                ),
                state=state,
            )

        assert state.total_size == 6
        archive.open.assert_not_called()

    @pytest.mark.parametrize(
        "error",
        [
            BadZipFile("Bad CRC-32"),
            EOFError("Unexpected end of data"),
            OSError("Decompression failure"),
            RuntimeError("Archive read failure"),
        ],
    )
    def test_archive_read_errors_are_wrapped(
        self,
        error: Exception,
    ) -> None:
        member = ZipInfo("Chapter 01/001.jpg")
        member_stream = FailingStream(error)
        archive = Mock(spec=ZipFile)
        archive.open.return_value = member_stream
        state = ArchiveReadState(total_size=10)
        expected_message = (
            "Failed to read archive member: 'Chapter 01/001.jpg'."
        )

        with pytest.raises(
            InvalidArchiveError,
            match=exact_message(expected_message),
        ) as exception_info:
            read_member_data(
                archive,
                member,
                limits=ArchiveReadLimits(
                    max_file_size=20,
                    max_total_size=20,
                ),
                state=state,
            )

        assert exception_info.value.__cause__ is error
        assert state.total_size == 10
        assert member_stream.closed is True

    def test_archive_open_error_is_wrapped(self) -> None:
        member = ZipInfo("001.jpg")
        error = BadZipFile("Invalid local file header")
        archive = Mock(spec=ZipFile)
        archive.open.side_effect = error
        state = ArchiveReadState()
        expected_message = "Failed to read archive member: '001.jpg'."

        with pytest.raises(
            InvalidArchiveError,
            match=exact_message(expected_message),
        ) as exception_info:
            read_member_data(
                archive,
                member,
                limits=ArchiveReadLimits(),
                state=state,
            )

        assert exception_info.value.__cause__ is error
        assert state.total_size == 0
        archive.open.assert_called_once_with(
            member,
            mode="r",
        )

    @pytest.mark.parametrize(
        "chunk_size",
        [
            0,
            -1,
        ],
    )
    def test_invalid_chunk_size_is_rejected(
        self,
        chunk_size: int,
    ) -> None:
        archive = Mock(spec=ZipFile)
        member = ZipInfo("001.jpg")
        expected_message = (
            "Archive member read chunk size must be a positive integer."
        )

        with pytest.raises(
            ValueError,
            match=exact_message(expected_message),
        ):
            read_member_data(
                archive,
                member,
                limits=ArchiveReadLimits(),
                state=ArchiveReadState(),
                chunk_size=chunk_size,
            )

        archive.open.assert_not_called()

    def test_default_chunk_size_is_used(self) -> None:
        member = ZipInfo("001.jpg")
        member_stream = TrackingStream(b"image")
        archive = Mock(spec=ZipFile)
        archive.open.return_value = member_stream

        read_member_data(
            archive,
            member,
            limits=ArchiveReadLimits(),
            state=ArchiveReadState(),
        )

        assert member_stream.read_sizes == [
            DEFAULT_MEMBER_READ_CHUNK_SIZE,
            DEFAULT_MEMBER_READ_CHUNK_SIZE,
        ]

    def test_member_stream_is_closed_when_file_limit_is_exceeded(
        self,
    ) -> None:
        member = ZipInfo("001.jpg")
        member_stream = TrackingStream(b"12345")
        archive = Mock(spec=ZipFile)
        archive.open.return_value = member_stream
        state = ArchiveReadState()
        expected_message = (
            "Archive member exceeds the permitted size while reading: "
            "'001.jpg'."
        )

        with pytest.raises(
            InvalidArchiveError,
            match=exact_message(expected_message),
        ):
            read_member_data(
                archive,
                member,
                limits=ArchiveReadLimits(
                    max_file_size=4,
                    max_total_size=10,
                ),
                state=state,
                chunk_size=2,
            )

        assert state.total_size == 0
        assert member_stream.closed is True

class TestBuildManifest:

    def test_manifest_classifies_members_and_preserves_order(self) -> None:
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
        self,
        compression: int,
    ) -> None:
        archive_stream = create_archive(
            [("001.jpg", b"image")],
            compression=compression,
        )

        with ZipFile(archive_stream, mode="r") as archive:
            manifest = build_manifest(archive)

        assert manifest.image_count == 1


    def test_directory_entries_are_excluded_from_manifest(self) -> None:
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


    def test_empty_archive_is_rejected(self) -> None:
        archive_stream = create_archive([])
        expected_message = "The archive does not contain any files."

        with ZipFile(archive_stream, mode="r") as archive, pytest.raises(
            InvalidArchiveError,
            match=exact_message(expected_message),
        ):
            build_manifest(archive)


    def test_directory_only_archive_is_rejected(self) -> None:
        archive_stream = create_archive(
            [("Chapter 01/", b"")]
        )
        expected_message = "The archive does not contain any files."

        with ZipFile(archive_stream, mode="r") as archive, pytest.raises(
            InvalidArchiveError,
            match=exact_message(expected_message),
        ):
            build_manifest(archive)


    def test_archive_without_supported_images_is_rejected(self) -> None:
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


    def test_archive_file_count_limit_is_enforced(self) -> None:
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


    def test_per_file_size_limit_is_enforced(self) -> None:
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


    def test_total_uncompressed_size_limit_is_enforced(self) -> None:
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


    def test_duplicate_normalized_paths_are_rejected(self) -> None:
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


    def test_encrypted_member_is_rejected(self) -> None:
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


    def test_unsupported_compression_method_is_rejected(self) -> None:
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
        self,
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

class TestVerifyArchiveIntegrity:

    def test_integrity_check_accepts_valid_archive(self) -> None:
        archive_stream = create_archive(
            [("001.jpg", b"image")]
        )

        with ZipFile(archive_stream, mode="r") as archive:
            verify_archive_integrity(archive)


    def test_integrity_check_rejects_corrupt_member(self) -> None:
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

class TestInspectCbz:

    def test_inspect_cbz_returns_manifest(self, tmp_path: Path) -> None:
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


    def test_inspect_cbz_rejects_missing_file(self, tmp_path: Path) -> None:
        archive_path = tmp_path / "missing.cbz"
        expected_message = (
            f"Archive file does not exist: {archive_path}."
        )

        with pytest.raises(
            InvalidArchiveError,
            match=exact_message(expected_message),
        ):
            inspect_cbz(archive_path)


    def test_inspect_cbz_rejects_invalid_zip(self, tmp_path: Path) -> None:
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
