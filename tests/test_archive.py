# SPDX-License-Identifier: GPL-3.0-or-later

import stat
from datetime import UTC, datetime
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

import cbzfit.archive as archive_module
from cbzfit.archive import (
    DEFAULT_EXPANSION_GRACE_SIZE,
    DEFAULT_MAX_EXPANSION_RATIO,
    DEFAULT_MEMBER_READ_CHUNK_SIZE,
    DEFAULT_UNIX_FILE_PERMISSIONS,
    DOS_ATTRIBUTE_ARCHIVE,
    DOS_ATTRIBUTE_COMPRESSED,
    DOS_ATTRIBUTE_DEVICE,
    DOS_ATTRIBUTE_DIRECTORY,
    DOS_ATTRIBUTE_ENCRYPTED,
    DOS_ATTRIBUTE_HIDDEN,
    DOS_ATTRIBUTE_NORMAL,
    DOS_ATTRIBUTE_NOT_CONTENT_INDEXED,
    DOS_ATTRIBUTE_OFFLINE,
    DOS_ATTRIBUTE_READ_ONLY,
    DOS_ATTRIBUTE_REPARSE_POINT,
    DOS_ATTRIBUTE_SPARSE_FILE,
    DOS_ATTRIBUTE_SYSTEM,
    DOS_ATTRIBUTE_TEMPORARY,
    DOS_ATTRIBUTE_VIRTUAL,
    DOS_ATTRIBUTE_VOLUME_LABEL,
    ZIP_CREATOR_DOS,
    ZIP_CREATOR_UNIX,
    ArchivePathLimits,
    ArchiveReadLimits,
    ArchiveReadState,
    InvalidArchiveError,
    MemberDateTimeMode,
    MemberDateTimePolicy,
    build_manifest,
    contains_control_characters,
    current_zip_date_time,
    has_supported_image_extension,
    inspect_cbz,
    is_directory_marker,
    portable_member_path_key,
    read_member_data,
    resolve_member_date_time,
    resolve_output_member_attributes,
    validate_member_expansion,
    validate_member_path,
    validate_member_type,
    validate_positive_integer,
    validate_zip_date_time,
    write_member_data,
)
from tests.helpers import exact_message


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

def create_member(
    filename: str,
    *,
    create_system: int,
    external_attr: int,
) -> ZipInfo:
    """Create a ZIP member with explicit creator and external attributes."""
    member = ZipInfo(filename)
    member.create_system = create_system
    member.external_attr = external_attr
    return member


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

class TestValidateZipDateTime:
    @pytest.mark.parametrize(
        "date_time",
        [
            (1980, 1, 1, 0, 0, 0),
            (2026, 8, 22, 16, 30, 58),
            (2107, 12, 31, 23, 59, 58),
        ],
    )
    def test_valid_zip_timestamps_are_returned(
        self,
        date_time: tuple[int, int, int, int, int, int],
    ) -> None:
        assert validate_zip_date_time(date_time) is date_time

    @pytest.mark.parametrize(
        "date_time",
        [
            (),
            (2026, 1, 1),
            (2026, 1, 1, 0, 0, 0, 0),
            [2026, 1, 1, 0, 0, 0],
            (2026, 1, 1, 0, 0, "0"),
        ],
    )
    def test_timestamp_must_contain_exactly_six_integers(
        self,
        date_time: object,
    ) -> None:
        expected_message = (
            "ZIP member timestamp must contain exactly six integers."
        )

        with pytest.raises(
            ValueError,
            match=exact_message(expected_message),
        ):
            validate_zip_date_time(date_time)  # type: ignore[arg-type]

    def test_boolean_timestamp_value_follows_normal_integer_behavior(self) -> None:
        date_time = (2026, 1, 1, 0, 0, False)
        assert validate_zip_date_time(date_time) is date_time

    @pytest.mark.parametrize(
        "date_time",
        [
            (2026, 2, 29, 0, 0, 0),
            (2026, 13, 1, 0, 0, 0),
            (2026, 1, 1, 24, 0, 0),
        ],
    )
    def test_invalid_calendar_timestamp_is_rejected(
        self,
        date_time: tuple[int, int, int, int, int, int],
    ) -> None:
        expected_message = (
            "ZIP member timestamp must be a valid date and time."
        )

        with pytest.raises(
            ValueError,
            match=exact_message(expected_message),
        ):
            validate_zip_date_time(date_time)

    @pytest.mark.parametrize(
        "date_time",
        [
            (1979, 12, 31, 23, 59, 58),
            (2108, 1, 1, 0, 0, 0),
        ],
    )
    def test_timestamp_year_outside_zip_range_is_rejected(
        self,
        date_time: tuple[int, int, int, int, int, int],
    ) -> None:
        expected_message = (
            "ZIP member timestamp year must be between 1980 and 2107."
        )

        with pytest.raises(
            ValueError,
            match=exact_message(expected_message),
        ):
            validate_zip_date_time(date_time)

    def test_odd_timestamp_second_is_rejected(self) -> None:
        expected_message = (
            "ZIP member timestamp seconds must use two-second precision."
        )

        with pytest.raises(
            ValueError,
            match=exact_message(expected_message),
        ):
            validate_zip_date_time((2026, 8, 22, 16, 30, 59))


class TestMemberDateTimePolicy:
    def test_default_policy_uses_modified_mode(self) -> None:
        policy = MemberDateTimePolicy()

        assert policy.mode is MemberDateTimeMode.MODIFIED
        assert policy.fixed_date_time is None

    @pytest.mark.parametrize(
        "mode",
        [
            MemberDateTimeMode.PRESERVE,
            MemberDateTimeMode.MODIFIED,
        ],
    )
    def test_non_fixed_policy_is_created(
        self,
        mode: MemberDateTimeMode,
    ) -> None:
        policy = MemberDateTimePolicy(mode=mode)

        assert policy.mode is mode
        assert policy.fixed_date_time is None

    def test_fixed_policy_is_created(self) -> None:
        fixed_date_time = (2020, 1, 2, 3, 4, 6)
        policy = MemberDateTimePolicy(
            mode=MemberDateTimeMode.FIXED,
            fixed_date_time=fixed_date_time,
        )

        assert policy.mode is MemberDateTimeMode.FIXED
        assert policy.fixed_date_time is fixed_date_time

    def test_invalid_mode_type_is_rejected(self) -> None:
        expected_message = (
            "Archive-member timestamp mode must be a "
            "MemberDateTimeMode, not str."
        )

        with pytest.raises(
            TypeError,
            match=exact_message(expected_message),
        ):
            MemberDateTimePolicy(mode="modified")  # type: ignore[arg-type]

    def test_fixed_mode_requires_timestamp(self) -> None:
        expected_message = (
            "A fixed timestamp is required in fixed timestamp mode."
        )

        with pytest.raises(
            ValueError,
            match=exact_message(expected_message),
        ):
            MemberDateTimePolicy(mode=MemberDateTimeMode.FIXED)

    @pytest.mark.parametrize(
        "mode",
        [
            MemberDateTimeMode.PRESERVE,
            MemberDateTimeMode.MODIFIED,
        ],
    )
    def test_fixed_timestamp_is_rejected_outside_fixed_mode(
        self,
        mode: MemberDateTimeMode,
    ) -> None:
        expected_message = (
            "A fixed timestamp can only be used in fixed timestamp mode."
        )

        with pytest.raises(
            ValueError,
            match=exact_message(expected_message),
        ):
            MemberDateTimePolicy(
                mode=mode,
                fixed_date_time=(2020, 1, 2, 3, 4, 6),
            )

    def test_invalid_fixed_timestamp_is_rejected(self) -> None:
        expected_message = (
            "ZIP member timestamp must be a valid date and time."
        )

        with pytest.raises(
            ValueError,
            match=exact_message(expected_message),
        ):
            MemberDateTimePolicy(
                mode=MemberDateTimeMode.FIXED,
                fixed_date_time=(2020, 2, 30, 0, 0, 0),
            )


class TestValidatePositiveInteger:
    @pytest.mark.parametrize("value", [1, 100])
    def test_positive_integer_is_returned(self, value: int) -> None:
        assert validate_positive_integer(
            value,
            name="Maximum test value",
        ) == value

    @pytest.mark.parametrize("value", [True, 1.5, "1", None, object()])
    def test_non_integer_is_rejected(self, value: object) -> None:
        with pytest.raises(
            TypeError,
            match=exact_message(
                "Maximum test value must be an integer."
            ),
        ):
            validate_positive_integer(
                value,
                name="Maximum test value",
            )

    @pytest.mark.parametrize("value", [0, -1])
    def test_non_positive_integer_is_rejected(self, value: int) -> None:
        with pytest.raises(
            ValueError,
            match=exact_message(
                "Maximum test value must be a positive integer."
            ),
        ):
            validate_positive_integer(
                value,
                name="Maximum test value",
            )


class TestArchivePathLimits:
    def test_default_path_limits_are_created(self) -> None:
        limits = ArchivePathLimits()

        assert limits.max_path_length > 0
        assert limits.max_component_length > 0

    @pytest.mark.parametrize(
        ("arguments", "expected_message"),
        [
            (
                {"max_path_length": 0},
                "Maximum member path length must be a positive integer.",
            ),
            (
                {"max_path_length": -1},
                "Maximum member path length must be a positive integer.",
            ),
            (
                {"max_component_length": 0},
                "Maximum path component length must be a positive integer.",
            ),
            (
                {"max_component_length": -1},
                "Maximum path component length must be a positive integer.",
            ),
        ],
    )
    def test_invalid_path_limits_are_rejected(
        self,
        arguments: dict[str, int],
        expected_message: str,
    ) -> None:
        with pytest.raises(
            ValueError,
            match=exact_message(expected_message),
        ):
            ArchivePathLimits(**arguments)


    @pytest.mark.parametrize(
        ("arguments", "expected_message"),
        [
            (
                {"max_path_length": True},
                "Maximum member path length must be an integer.",
            ),
            (
                {"max_path_length": 1.5},
                "Maximum member path length must be an integer.",
            ),
            (
                {"max_component_length": "255"},
                "Maximum path component length must be an integer.",
            ),
            (
                {"max_component_length": None},
                "Maximum path component length must be an integer.",
            ),
        ],
    )
    def test_non_integer_path_limits_are_rejected(
        self,
        arguments: dict[str, object],
        expected_message: str,
    ) -> None:
        with pytest.raises(
            TypeError,
            match=exact_message(expected_message),
        ):
            ArchivePathLimits(**arguments)  # type: ignore[arg-type]


class TestIsDirectoryMarker:
    @pytest.mark.parametrize(
        "filename",
        ["Chapter 01/", "Chapter 01\\"],
    )
    def test_directory_marker_is_detected(self, filename: str) -> None:
        assert is_directory_marker(filename)

    @pytest.mark.parametrize(
        "filename",
        ["Chapter 01", "Chapter 01/001.jpg", "Chapter 01\\001.jpg"],
    )
    def test_regular_file_is_not_a_directory_marker(
        self,
        filename: str,
    ) -> None:
        assert not is_directory_marker(filename)


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
                limits=ArchivePathLimits(
                    max_component_length=8,
                ),
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
                limits=ArchivePathLimits(
                    max_path_length=10,
                ),
            )

    @pytest.mark.parametrize(
        "filename",
        [
            "001.jpg ",
            "001.jpg.",
            "Chapter /001.jpg",
            "Chapter./001.jpg",
        ],
    )
    def test_trailing_space_or_dot_is_rejected(
        self,
        filename: str,
    ) -> None:
        with pytest.raises(
            InvalidArchiveError,
            match=exact_message(
                "Archive member path component ends in a space or dot: "
                f"{filename!r}."
            ),
        ):
            validate_member_path(filename)

    @pytest.mark.parametrize(
        "invalid_character",
        ["<", ">", '"', "|", "?", "*", ":"],
    )
    @pytest.mark.parametrize(
        "filename_template",
        [
            "Chapter 01/page{character}01.jpg",
            "Chapter{character}01/001.jpg",
        ],
        ids=[
            "final-component",
            "parent-component",
        ],
    )
    def test_windows_invalid_filename_character_is_rejected(
        self,
        invalid_character: str,
        filename_template: str,
    ) -> None:
        filename = filename_template.format(
            character=invalid_character,
        )

        with pytest.raises(
            InvalidArchiveError,
            match=exact_message(
                "Archive member path contains an invalid Windows filename "
                f"character: {filename!r}."
            ),
        ):
            validate_member_path(filename)


    def test_windows_alternate_data_stream_is_rejected(
        self,
    ) -> None:
        filename = "001.jpg:payload"

        with pytest.raises(
            InvalidArchiveError,
            match=exact_message(
                "Archive member path contains an invalid Windows filename "
                f"character: {filename!r}."
            ),
        ):
            validate_member_path(filename)

    @pytest.mark.parametrize(
        "filename",
        [
            "Chapter 01//001.jpg",
            "Chapter 01\\/001.jpg",
            "Chapter 01/\\001.jpg",
            "Chapter 01//",
            "Chapter 01\\\\",
        ],
    )
    def test_repeated_separator_is_rejected(self, filename: str) -> None:
        with pytest.raises(
            InvalidArchiveError,
            match=exact_message(
                f"Archive member path contains an empty component: {filename!r}."
            ),
        ):
            validate_member_path(filename)

    @pytest.mark.parametrize(
        "device_name",
        [
            "CON",
            "prn",
            "AuX.txt",
            "nul.metadata.xml",
            "COM1.jpg",
            "com9",
            "LPT1.png",
            "lpt9.backup",
            "COM¹",
            "com².jpg",
            "LPT³.metadata.xml",
        ],
    )
    def test_windows_reserved_name_is_rejected(
        self,
        device_name: str,
    ) -> None:
        filename = f"Chapter 01/{device_name}"
        with pytest.raises(
            InvalidArchiveError,
            match=exact_message(
                f"Archive member uses a reserved Windows name: {filename!r}."
            ),
        ):
            validate_member_path(filename)

    @pytest.mark.parametrize(
        "filename",
        [
            "CON/001.jpg",
            "Chapter 01/COM1/001.jpg",
            "Chapter 01/COM¹/001.jpg",
        ],
    )
    def test_windows_reserved_parent_component_is_rejected(
        self,
        filename: str,
    ) -> None:
        with pytest.raises(
            InvalidArchiveError,
            match=exact_message(
                f"Archive member uses a reserved Windows name: {filename!r}."
            ),
        ):
            validate_member_path(filename)

    @pytest.mark.parametrize("limits", [False, {}, object()])
    def test_invalid_path_limits_object_is_rejected(
        self,
        limits: object,
    ) -> None:
        with pytest.raises(
            TypeError,
            match=exact_message(
                "Archive path limits must be an ArchivePathLimits instance."
            ),
        ):
            validate_member_path(
                "001.jpg",
                limits=limits,  # type: ignore[arg-type]
            )

    @pytest.mark.parametrize(
        "filename",
        [
            "console.jpg",
            "auxiliary.png",
            "com10.jpg",
            "lpt10.txt",
            "COM⁴.jpg",
            "LPT¹0.jpg",
            "COM¹page.jpg",
            "進撃の巨人/第01話.png",
            "Chapter 01/page.v2.final.jpg",
        ],
    )
    def test_portable_path_is_preserved(self, filename: str) -> None:
        assert validate_member_path(filename) == filename


class TestArchiveReadLimits:
    def test_default_limits_are_created(self) -> None:
        limits = ArchiveReadLimits()

        assert limits.max_file_size > 0
        assert limits.max_total_size > 0
        assert limits.max_expansion_ratio == DEFAULT_MAX_EXPANSION_RATIO
        assert limits.expansion_grace_size == DEFAULT_EXPANSION_GRACE_SIZE

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
            (
                {"max_expansion_ratio": 0},
                "Maximum expansion ratio must be a positive integer.",
            ),
            (
                {"max_expansion_ratio": -1},
                "Maximum expansion ratio must be a positive integer.",
            ),
            (
                {"expansion_grace_size": 0},
                "Expansion grace size must be a positive integer.",
            ),
            (
                {"expansion_grace_size": -1},
                "Expansion grace size must be a positive integer.",
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


    @pytest.mark.parametrize(
        ("arguments", "expected_message"),
        [
            (
                {"max_expansion_ratio": True},
                "Maximum expansion ratio must be an integer.",
            ),
            (
                {"max_expansion_ratio": 1.5},
                "Maximum expansion ratio must be an integer.",
            ),
            (
                {"expansion_grace_size": False},
                "Expansion grace size must be an integer.",
            ),
            (
                {"expansion_grace_size": "102400"},
                "Expansion grace size must be an integer.",
            ),
        ],
    )
    def test_non_integer_expansion_limits_are_rejected(
        self,
        arguments: dict[str, object],
        expected_message: str,
    ) -> None:
        with pytest.raises(
            TypeError,
            match=exact_message(expected_message),
        ):
            ArchiveReadLimits(**arguments)  # type: ignore[arg-type]


    @pytest.mark.parametrize(
        ("arguments", "expected_message"),
        [
            (
                {"max_file_size": True},
                "Maximum file size must be an integer.",
            ),
            (
                {"max_file_size": 1.5},
                "Maximum file size must be an integer.",
            ),
            (
                {"max_total_size": "1024"},
                "Maximum total uncompressed size must be an integer.",
            ),
            (
                {"max_total_size": None},
                "Maximum total uncompressed size must be an integer.",
            ),
            (
                {"max_expansion_ratio": object()},
                "Maximum expansion ratio must be an integer.",
            ),
            (
                {"expansion_grace_size": object()},
                "Expansion grace size must be an integer.",
            ),
        ],
    )
    def test_non_integer_read_limits_are_rejected(
        self,
        arguments: dict[str, object],
        expected_message: str,
    ) -> None:
        with pytest.raises(
            TypeError,
            match=exact_message(expected_message),
        ):
            ArchiveReadLimits(**arguments)  # type: ignore[arg-type]


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


class TestPortableMemberPathKey:
    @pytest.mark.parametrize(
        "max_files",
        [True, 1.5, "100", None, object()],
    )
    def test_non_integer_file_count_limit_is_rejected(
        self,
        max_files: object,
    ) -> None:
        archive_stream = create_archive([("001.jpg", b"image")])

        with ZipFile(archive_stream, mode="r") as archive, pytest.raises(
            TypeError,
            match=exact_message(
                "Maximum file count must be an integer."
            ),
        ):
            build_manifest(
                archive,
                max_files=max_files,  # type: ignore[arg-type]
            )

    @pytest.mark.parametrize(
        ("argument", "value", "expected_message"),
        [
            (
                "read_limits",
                {},
                "Archive read limits must be an ArchiveReadLimits instance.",
            ),
            (
                "read_limits",
                False,
                "Archive read limits must be an ArchiveReadLimits instance.",
            ),
            (
                "path_limits",
                {},
                "Archive path limits must be an ArchivePathLimits instance.",
            ),
            (
                "path_limits",
                False,
                "Archive path limits must be an ArchivePathLimits instance.",
            ),
        ],
    )
    def test_invalid_policy_object_is_rejected(
        self,
        argument: str,
        value: object,
        expected_message: str,
    ) -> None:
        archive_stream = create_archive([("001.jpg", b"image")])

        with ZipFile(archive_stream, mode="r") as archive, pytest.raises(
            TypeError,
            match=exact_message(expected_message),
        ):
            build_manifest(
                archive,
                **{argument: value},  # type: ignore[arg-type]
            )

    @pytest.mark.parametrize(
        ("first", "second"),
        [
            ("Chapter/Page.JPG", "chapter/page.jpg"),
            ("Café/001.jpg", "Cafe\u0301/001.jpg"),
        ],
    )
    def test_portable_equivalents_share_a_key(
        self,
        first: str,
        second: str,
    ) -> None:
        assert portable_member_path_key(first) == portable_member_path_key(
            second
        )

    def test_distinct_paths_have_distinct_keys(self) -> None:
        assert portable_member_path_key(
            "Chapter 01/001.jpg"
        ) != portable_member_path_key("Chapter 02/001.jpg")


class TestValidateMemberType:
    @pytest.mark.parametrize("permissions", [0, 0o600, 0o644, 0o755])
    def test_unix_regular_file_is_accepted(self, permissions: int) -> None:
        member = create_member(
            "001.jpg",
            create_system=ZIP_CREATOR_UNIX,
            external_attr=(stat.S_IFREG | permissions) << 16,
        )

        assert validate_member_type(member) is None

    @pytest.mark.parametrize(
        "filename",
        ["001.jpg", "Chapter 01/"],
    )
    def test_missing_unix_file_type_is_inferred_from_path(
        self,
        filename: str,
    ) -> None:
        member = create_member(
            filename,
            create_system=ZIP_CREATOR_UNIX,
            external_attr=0o644 << 16,
        )

        assert validate_member_type(member) is None

    def test_unix_directory_marker_is_accepted(self) -> None:
        member = create_member(
            "Chapter 01/",
            create_system=ZIP_CREATOR_UNIX,
            external_attr=(stat.S_IFDIR | 0o755) << 16,
        )

        assert validate_member_type(member) is None

    @pytest.mark.parametrize(
        ("file_type", "type_name"),
        [
            (stat.S_IFLNK, "symbolic link"),
            (stat.S_IFCHR, "character device"),
            (stat.S_IFBLK, "block device"),
            (stat.S_IFIFO, "FIFO"),
            (stat.S_IFSOCK, "socket"),
        ],
    )
    def test_unix_special_member_is_rejected(
        self,
        file_type: int,
        type_name: str,
    ) -> None:
        member = create_member(
            "special",
            create_system=ZIP_CREATOR_UNIX,
            external_attr=(file_type | 0o644) << 16,
        )
        expected_message = (
            f"Unsupported ZIP member type for 'special': {type_name}."
        )

        with pytest.raises(
            InvalidArchiveError,
            match=exact_message(expected_message),
        ):
            validate_member_type(member)

    def test_unknown_unix_special_type_is_rejected(self) -> None:
        member = create_member(
            "special",
            create_system=ZIP_CREATOR_UNIX,
            external_attr=(0xD000 | 0o644) << 16,
        )

        with pytest.raises(
            InvalidArchiveError,
            match=exact_message(
                "Unsupported ZIP member type for 'special'."
            ),
        ):
            validate_member_type(member)

    @pytest.mark.parametrize(
        ("filename", "file_type"),
        [("001.jpg/", stat.S_IFREG), ("Chapter 01", stat.S_IFDIR)],
    )
    def test_unix_type_and_path_conflict_is_rejected(
        self,
        filename: str,
        file_type: int,
    ) -> None:
        member = create_member(
            filename,
            create_system=ZIP_CREATOR_UNIX,
            external_attr=(file_type | 0o644) << 16,
        )

        with pytest.raises(
            InvalidArchiveError,
            match=exact_message(
                f"ZIP member type conflicts with its path: {filename!r}."
            ),
        ):
            validate_member_type(member)

    @pytest.mark.parametrize(
        "attributes",
        [
            0,
            DOS_ATTRIBUTE_NORMAL,
            DOS_ATTRIBUTE_READ_ONLY,
            DOS_ATTRIBUTE_HIDDEN,
            DOS_ATTRIBUTE_SYSTEM,
            DOS_ATTRIBUTE_ARCHIVE,
            DOS_ATTRIBUTE_READ_ONLY | DOS_ATTRIBUTE_HIDDEN,
        ],
    )
    def test_dos_regular_file_with_safe_attributes_is_accepted(
        self,
        attributes: int,
    ) -> None:
        member = create_member(
            "001.jpg",
            create_system=ZIP_CREATOR_DOS,
            external_attr=attributes,
        )

        assert validate_member_type(member) is None

    def test_dos_directory_marker_is_accepted(self) -> None:
        member = create_member(
            "Chapter 01/",
            create_system=ZIP_CREATOR_DOS,
            external_attr=DOS_ATTRIBUTE_DIRECTORY,
        )

        assert validate_member_type(member) is None

    @pytest.mark.parametrize(
        "attributes",
        [
            DOS_ATTRIBUTE_VOLUME_LABEL,
            DOS_ATTRIBUTE_DEVICE,
            DOS_ATTRIBUTE_REPARSE_POINT,
        ],
    )
    def test_dos_special_member_is_rejected(self, attributes: int) -> None:
        member = create_member(
            "special",
            create_system=ZIP_CREATOR_DOS,
            external_attr=attributes,
        )

        with pytest.raises(
            InvalidArchiveError,
            match=exact_message(
                "Unsupported ZIP member type for 'special'."
            ),
        ):
            validate_member_type(member)

    def test_dos_directory_attribute_requires_directory_path(self) -> None:
        member = create_member(
            "Chapter 01",
            create_system=ZIP_CREATOR_DOS,
            external_attr=DOS_ATTRIBUTE_DIRECTORY,
        )

        with pytest.raises(
            InvalidArchiveError,
            match=exact_message(
                "ZIP member type conflicts with its path: 'Chapter 01'."
            ),
        ):
            validate_member_type(member)

    @pytest.mark.parametrize(
        "attributes",
        [0, DOS_ATTRIBUTE_NORMAL],
    )
    def test_dos_directory_path_requires_directory_attribute(
        self,
        attributes: int,
    ) -> None:
        member = create_member(
            "Chapter 01/",
            create_system=ZIP_CREATOR_DOS,
            external_attr=attributes,
        )

        with pytest.raises(
            InvalidArchiveError,
            match=exact_message(
                "ZIP member type conflicts with its path: 'Chapter 01/'."
            ),
        ):
            validate_member_type(member)

    def test_dos_normal_attribute_must_be_used_alone(self) -> None:
        member = create_member(
            "001.jpg",
            create_system=ZIP_CREATOR_DOS,
            external_attr=DOS_ATTRIBUTE_NORMAL | DOS_ATTRIBUTE_ARCHIVE,
        )

        with pytest.raises(
            InvalidArchiveError,
            match=exact_message(
                "ZIP member has inconsistent DOS attributes: '001.jpg'."
            ),
        ):
            validate_member_type(member)

    @pytest.mark.parametrize(
        "filename",
        ["001.jpg", "Chapter 01/"],
    )
    def test_unknown_creator_uses_path_type(
        self,
        filename: str,
    ) -> None:
        member = create_member(
            filename,
            create_system=99,
            external_attr=0xFFFFFFFF,
        )

        assert validate_member_type(member) is None


class TestBackslashDirectoryMarkers:
    @pytest.mark.parametrize(
        ("create_system", "external_attr"),
        [
            (ZIP_CREATOR_UNIX, (stat.S_IFDIR | 0o755) << 16),
            (ZIP_CREATOR_DOS, DOS_ATTRIBUTE_DIRECTORY),
        ],
    )
    def test_backslash_directory_marker_is_accepted(
        self,
        create_system: int,
        external_attr: int,
    ) -> None:
        member = create_member(
            "Chapter 01\\",
            create_system=create_system,
            external_attr=external_attr,
        )
        normalized_path = validate_member_path(member.filename)

        assert normalized_path == "Chapter 01"
        assert is_directory_marker(member.filename)
        assert validate_member_type(member) is None

    def test_backslash_directory_marker_is_omitted_from_manifest(self) -> None:
        archive_stream = create_archive(
            [("temporary/", b""), ("001.jpg", b"image")],
        )

        with ZipFile(archive_stream, mode="r") as archive:
            directory = archive.infolist()[0]
            directory.filename = "Chapter 01\\"
            directory.create_system = ZIP_CREATOR_DOS
            directory.external_attr = DOS_ATTRIBUTE_DIRECTORY
            manifest = build_manifest(archive)

        assert [
            member.filename for member in manifest.file_members
        ] == ["001.jpg"]


class TestResolveOutputMemberAttributes:
    def test_safe_unix_permissions_are_preserved(self) -> None:
        member = create_member(
            "001.jpg",
            create_system=ZIP_CREATOR_UNIX,
            external_attr=(stat.S_IFREG | 0o640) << 16,
        )

        create_system, external_attr = resolve_output_member_attributes(member)

        assert create_system == ZIP_CREATOR_UNIX
        assert external_attr >> 16 == stat.S_IFREG | 0o640

    def test_unix_special_permission_bits_are_removed(self) -> None:
        member = create_member(
            "001.jpg",
            create_system=ZIP_CREATOR_UNIX,
            external_attr=(stat.S_IFREG | 0o7644) << 16,
        )

        _, external_attr = resolve_output_member_attributes(member)

        assert external_attr >> 16 == stat.S_IFREG | 0o644

    def test_missing_unix_permissions_use_safe_default(self) -> None:
        member = create_member(
            "001.jpg",
            create_system=ZIP_CREATOR_UNIX,
            external_attr=stat.S_IFREG << 16,
        )

        _, external_attr = resolve_output_member_attributes(member)

        assert external_attr >> 16 == (
            stat.S_IFREG | DEFAULT_UNIX_FILE_PERMISSIONS
        )

    def test_safe_dos_attributes_are_preserved(self) -> None:
        safe_attributes = (
            DOS_ATTRIBUTE_READ_ONLY
            | DOS_ATTRIBUTE_HIDDEN
            | DOS_ATTRIBUTE_SYSTEM
            | DOS_ATTRIBUTE_ARCHIVE
        )
        member = create_member(
            "001.jpg",
            create_system=ZIP_CREATOR_DOS,
            external_attr=safe_attributes,
        )

        assert resolve_output_member_attributes(member) == (
            ZIP_CREATOR_DOS,
            safe_attributes,
        )

    @pytest.mark.parametrize(
        "attribute",
        [
            DOS_ATTRIBUTE_TEMPORARY,
            DOS_ATTRIBUTE_SPARSE_FILE,
            DOS_ATTRIBUTE_COMPRESSED,
            DOS_ATTRIBUTE_OFFLINE,
            DOS_ATTRIBUTE_NOT_CONTENT_INDEXED,
            DOS_ATTRIBUTE_ENCRYPTED,
            DOS_ATTRIBUTE_VIRTUAL,
        ],
    )
    def test_irrelevant_dos_attributes_are_dropped(
        self,
        attribute: int,
    ) -> None:
        member = create_member(
            "001.jpg",
            create_system=ZIP_CREATOR_DOS,
            external_attr=DOS_ATTRIBUTE_READ_ONLY | attribute,
        )

        assert resolve_output_member_attributes(member) == (
            ZIP_CREATOR_DOS,
            DOS_ATTRIBUTE_READ_ONLY,
        )

    @pytest.mark.parametrize("attributes", [0, DOS_ATTRIBUTE_NORMAL])
    def test_dos_file_without_preserved_attributes_is_normalized(
        self,
        attributes: int,
    ) -> None:
        member = create_member(
            "001.jpg",
            create_system=ZIP_CREATOR_DOS,
            external_attr=attributes,
        )

        assert resolve_output_member_attributes(member) == (
            ZIP_CREATOR_DOS,
            DOS_ATTRIBUTE_NORMAL,
        )

    def test_unknown_creator_uses_safe_unix_metadata(self) -> None:
        member = create_member(
            "001.jpg",
            create_system=99,
            external_attr=0xFFFFFFFF,
        )

        assert resolve_output_member_attributes(member) == (
            ZIP_CREATOR_UNIX,
            (stat.S_IFREG | DEFAULT_UNIX_FILE_PERMISSIONS) << 16,
        )



class TestValidateMemberExpansion:
    @staticmethod
    def create_compressed_member(
        *,
        file_size: int,
        compress_size: int,
        compression: int = ZIP_DEFLATED,
        filename: str = "ComicInfo.xml",
    ) -> ZipInfo:
        member = ZipInfo(filename)
        member.compress_type = compression
        member.file_size = file_size
        member.compress_size = compress_size
        return member

    @pytest.mark.parametrize(
        "file_size",
        [9_999, 10_000],
    )
    def test_expansion_at_or_below_ratio_limit_is_accepted(
        self,
        file_size: int,
    ) -> None:
        member = self.create_compressed_member(
            file_size=file_size,
            compress_size=100,
        )
        validate_member_expansion(
            member,
            max_expansion_ratio=100,
            expansion_grace_size=1,
        )

    def test_expansion_above_ratio_limit_is_rejected(self) -> None:
        member = self.create_compressed_member(
            file_size=10_001,
            compress_size=100,
        )
        with pytest.raises(
            InvalidArchiveError,
            match=exact_message(
                "Archive member's declared expansion exceeds the permitted "
                "limit of 100:1 for 'ComicInfo.xml'."
            ),
        ):
            validate_member_expansion(
                member,
                max_expansion_ratio=100,
                expansion_grace_size=1,
            )

    @pytest.mark.parametrize(
        "file_size",
        [99, 100],
    )
    def test_expansion_at_or_below_grace_size_is_accepted(
        self,
        file_size: int,
    ) -> None:
        member = self.create_compressed_member(
            file_size=file_size,
            compress_size=1,
        )
        validate_member_expansion(
            member,
            max_expansion_ratio=10,
            expansion_grace_size=100,
        )

    def test_expansion_above_grace_and_ratio_allowance_is_rejected(self) -> None:
        member = self.create_compressed_member(
            file_size=101,
            compress_size=1,
        )
        with pytest.raises(
            InvalidArchiveError,
            match=exact_message(
                "Archive member's declared expansion exceeds the permitted "
                "limit of 10:1 for 'ComicInfo.xml'."
            ),
        ):
            validate_member_expansion(
                member,
                max_expansion_ratio=10,
                expansion_grace_size=100,
            )

    def test_empty_compressed_member_is_accepted(self) -> None:
        member = self.create_compressed_member(
            file_size=0,
            compress_size=0,
        )
        validate_member_expansion(
            member,
            max_expansion_ratio=100,
            expansion_grace_size=100,
        )

    def test_stored_member_is_excluded_from_expansion_validation(self) -> None:
        member = self.create_compressed_member(
            file_size=1_000,
            compress_size=0,
            compression=ZIP_STORED,
        )
        validate_member_expansion(
            member,
            max_expansion_ratio=1,
            expansion_grace_size=1,
        )

    def test_non_empty_compressed_member_with_zero_size_is_rejected(self) -> None:
        member = self.create_compressed_member(
            file_size=1,
            compress_size=0,
        )
        with pytest.raises(
            InvalidArchiveError,
            match=exact_message(
                "Archive member declares a non-empty file with zero compressed "
                "size: 'ComicInfo.xml'."
            ),
        ):
            validate_member_expansion(
                member,
                max_expansion_ratio=100,
                expansion_grace_size=100,
            )


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

    @pytest.mark.parametrize(
        ("file_type", "type_name"),
        [
            (stat.S_IFLNK, "symbolic link"),
            (stat.S_IFCHR, "character device"),
            (stat.S_IFBLK, "block device"),
            (stat.S_IFIFO, "FIFO"),
            (stat.S_IFSOCK, "socket"),
        ],
    )
    def test_manifest_rejects_unix_special_member(
        self,
        file_type: int,
        type_name: str,
    ) -> None:
        archive_stream = create_archive(
            [("001.jpg", b"image"), ("special", b"target")],
        )

        with ZipFile(archive_stream, mode="r") as archive:
            special_member = archive.getinfo("special")
            special_member.create_system = ZIP_CREATOR_UNIX
            special_member.external_attr = (file_type | 0o644) << 16
            with pytest.raises(
                InvalidArchiveError,
                match=exact_message(
                    f"Unsupported ZIP member type for 'special': "
                    f"{type_name}."
                ),
            ):
                build_manifest(archive)

    def test_directory_path_is_validated_before_being_omitted(self) -> None:
        unsafe_directory = "../Chapter 01/"
        archive_stream = create_archive(
            [("temporary/", b""), ("001.jpg", b"image")],
        )

        with ZipFile(archive_stream, mode="r") as archive:
            archive.infolist()[0].filename = unsafe_directory
            with pytest.raises(
                InvalidArchiveError,
                match=exact_message(
                    f"Archive member has an unsafe path: "
                    f"{unsafe_directory!r}."
                ),
            ):
                build_manifest(archive)

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
                read_limits=ArchiveReadLimits(max_file_size=4),
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
                read_limits=ArchiveReadLimits(max_total_size=5),
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

    def test_dos_filesystem_encryption_does_not_imply_zip_encryption(
        self,
    ) -> None:
        archive_stream = create_archive(
            [("001.jpg", b"image")],
        )

        with ZipFile(archive_stream, mode="r") as archive:
            member = archive.getinfo("001.jpg")
            member.create_system = ZIP_CREATOR_DOS
            member.external_attr = DOS_ATTRIBUTE_ENCRYPTED
            manifest = build_manifest(archive)

        assert manifest.file_members == (member,)

    def test_zip_encryption_is_rejected_independently_of_dos_attributes(
        self,
    ) -> None:
        archive_stream = create_archive(
            [("001.jpg", b"image")],
        )

        with ZipFile(archive_stream, mode="r") as archive:
            member = archive.getinfo("001.jpg")
            member.create_system = ZIP_CREATOR_DOS
            member.external_attr = DOS_ATTRIBUTE_READ_ONLY
            member.flag_bits |= 0x1

            with pytest.raises(
                InvalidArchiveError,
                match=exact_message(
                    "Encrypted archive members are not supported: '001.jpg'."
                ),
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

    def test_custom_path_limits_are_applied(self) -> None:
        filename = "Chapter 01/001.jpg"
        archive_stream = create_archive([(filename, b"image")])
        expected_message = (
            f"Archive member path exceeds 10 characters: {filename!r}."
        )

        with ZipFile(archive_stream, mode="r") as archive, pytest.raises(
            InvalidArchiveError,
            match=exact_message(expected_message),
        ):
            build_manifest(
                archive,
                path_limits=ArchivePathLimits(max_path_length=10),
            )

    @pytest.mark.parametrize("max_files", [0, -1])
    def test_invalid_file_count_limit_is_rejected(
        self,
        max_files: int,
    ) -> None:
        archive_stream = create_archive(
            [("001.jpg", b"image")]
        )

        with ZipFile(archive_stream, mode="r") as archive, pytest.raises(
            ValueError,
            match=exact_message(
                "Maximum file count must be a positive integer."
            ),
        ):
            build_manifest(archive, max_files=max_files)

    @pytest.mark.parametrize(
        ("first", "second"),
        [
            ("Page.jpg", "page.jpg"),
            ("Chapter/Page.jpg", "chapter/page.jpg"),
            ("Café/001.jpg", "Cafe\u0301/001.jpg"),
            ("Chapter/", "chapter/"),
            ("Chapter", "chapter/"),
        ],
    )
    def test_portable_path_collision_is_rejected(
        self,
        first: str,
        second: str,
    ) -> None:
        archive_stream = create_archive(
            [
                (first, b"first"),
                (second, b"second"),
                ("cover.jpg", b"image"),
            ]
        )
        expected_message = (
            "The archive contains paths that collide across filesystems: "
            f"{first!r} and {second!r}."
        )

        with ZipFile(archive_stream, mode="r") as archive, pytest.raises(
            InvalidArchiveError,
            match=exact_message(expected_message),
        ):
            build_manifest(archive)

    def test_valid_unicode_casing_and_order_are_preserved(self) -> None:
        filenames = [
            "進撃の巨人/第01話.PNG",
            "Chapter 01/page.v2.final.jpg",
            "ComicInfo.xml",
        ]
        archive_stream = create_archive(
            [(filename, b"data") for filename in filenames]
        )

        with ZipFile(archive_stream, mode="r") as archive:
            manifest = build_manifest(archive)

        assert [
            member.filename for member in manifest.file_members
        ] == filenames


    def test_directory_marker_is_not_checked_for_expansion(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        archive_stream = create_archive(
            [("Chapter 01/", b""), ("Chapter 01/001.jpg", b"image")],
        )
        validate_expansion = Mock(wraps=validate_member_expansion)
        monkeypatch.setattr(
            archive_module,
            "validate_member_expansion",
            validate_expansion,
        )

        with ZipFile(archive_stream, mode="r") as archive:
            build_manifest(archive)

        assert [
            call.args[0].filename
            for call in validate_expansion.call_args_list
        ] == ["Chapter 01/001.jpg"]

    def test_legitimate_compressed_xml_within_limits_is_accepted(self) -> None:
        xml = (
            b"<ComicInfo><Page>ordinary metadata</Page></ComicInfo>" * 2_000
        )
        archive_stream = create_archive(
            [("001.jpg", b"image"), ("ComicInfo.xml", xml)],
            compression=ZIP_DEFLATED,
        )
        with ZipFile(archive_stream, mode="r") as archive:
            metadata = archive.getinfo("ComicInfo.xml")
            manifest = build_manifest(
                archive,
                read_limits=ArchiveReadLimits(
                    max_expansion_ratio=400,
                    expansion_grace_size=100 * 1024,
                ),
            )
        assert metadata in manifest.other_members

    def test_declared_file_size_limit_precedes_expansion_validation(self) -> None:
        archive_stream = create_archive(
            [("001.jpg", b"image")],
            compression=ZIP_DEFLATED,
        )
        with ZipFile(archive_stream, mode="r") as archive:
            member = archive.getinfo("001.jpg")
            member.file_size = 101
            member.compress_size = 1
            with pytest.raises(
                InvalidArchiveError,
                match=exact_message(
                    "Archive member exceeds the permitted size: '001.jpg'."
                ),
            ):
                build_manifest(
                    archive,
                    read_limits=ArchiveReadLimits(
                        max_file_size=100,
                        max_expansion_ratio=1,
                        expansion_grace_size=1,
                    ),
                )

    def test_declared_total_size_limit_precedes_expansion_validation(self) -> None:
        archive_stream = create_archive(
            [("001.jpg", b"image")],
            compression=ZIP_DEFLATED,
        )
        with ZipFile(archive_stream, mode="r") as archive:
            member = archive.getinfo("001.jpg")
            member.file_size = 101
            member.compress_size = 1
            with pytest.raises(
                InvalidArchiveError,
                match=exact_message(
                    "The archive's uncompressed contents exceed the permitted size."
                ),
            ):
                build_manifest(
                    archive,
                    read_limits=ArchiveReadLimits(
                        max_total_size=100,
                        max_expansion_ratio=1,
                        expansion_grace_size=1,
                    ),
                )

class TestCurrentZipDateTime:
    def test_current_local_time_uses_zip_precision(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fixed_utc = datetime(2026, 8, 22, 14, 30, 59, tzinfo=UTC)
        datetime_mock = Mock(wraps=datetime)
        datetime_mock.now.return_value = fixed_utc
        monkeypatch.setattr(
            archive_module,
            "datetime",
            datetime_mock,
        )

        result = current_zip_date_time()

        expected_local = fixed_utc.astimezone()
        assert result == (
            expected_local.year,
            expected_local.month,
            expected_local.day,
            expected_local.hour,
            expected_local.minute,
            58,
        )
        datetime_mock.now.assert_called_once_with(UTC)


class TestResolveMemberDateTime:
    def test_preserve_mode_returns_source_timestamp(self) -> None:
        source_date_time = (2020, 1, 2, 3, 4, 6)
        member = ZipInfo("001.jpg", date_time=source_date_time)

        result = resolve_member_date_time(
            member,
            policy=MemberDateTimePolicy(
                mode=MemberDateTimeMode.PRESERVE,
            ),
            transformed=True,
        )

        assert result == source_date_time

    def test_modified_mode_preserves_unchanged_member_timestamp(self) -> None:
        source_date_time = (2020, 1, 2, 3, 4, 6)
        member = ZipInfo("001.jpg", date_time=source_date_time)

        result = resolve_member_date_time(
            member,
            policy=MemberDateTimePolicy(),
            transformed=False,
        )

        assert result == source_date_time

    def test_modified_mode_uses_current_time_for_transformed_member(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        member = ZipInfo("001.jpg", date_time=(2020, 1, 2, 3, 4, 6))
        current_date_time = (2026, 8, 22, 16, 30, 58)
        current_time = Mock(return_value=current_date_time)
        monkeypatch.setattr(
            archive_module,
            "current_zip_date_time",
            current_time,
        )

        result = resolve_member_date_time(
            member,
            policy=MemberDateTimePolicy(),
            transformed=True,
        )

        assert result == current_date_time
        current_time.assert_called_once_with()

    @pytest.mark.parametrize(
        "transformed",
        [False, True],
    )
    def test_fixed_mode_returns_configured_timestamp(
        self,
        transformed: bool,
    ) -> None:
        member = ZipInfo("001.jpg", date_time=(2020, 1, 2, 3, 4, 6))
        fixed_date_time = (2000, 2, 4, 6, 8, 10)

        result = resolve_member_date_time(
            member,
            policy=MemberDateTimePolicy(
                mode=MemberDateTimeMode.FIXED,
                fixed_date_time=fixed_date_time,
            ),
            transformed=transformed,
        )

        assert result == fixed_date_time

    @pytest.mark.parametrize(
        "mode",
        [
            MemberDateTimeMode.PRESERVE,
            MemberDateTimeMode.MODIFIED,
        ],
    )
    def test_invalid_source_timestamp_is_rejected_when_preserved(
        self,
        mode: MemberDateTimeMode,
    ) -> None:
        member = ZipInfo("001.jpg")
        member.date_time = (2020, 2, 30, 0, 0, 0)
        expected_message = (
            "ZIP member timestamp must be a valid date and time."
        )

        with pytest.raises(
            ValueError,
            match=exact_message(expected_message),
        ):
            resolve_member_date_time(
                member,
                policy=MemberDateTimePolicy(mode=mode),
                transformed=False,
            )


class TestCloneMemberInfo:
    def test_safe_metadata_is_cloned(self) -> None:
        member = ZipInfo("Chapter 01/001.jpg", date_time=(2020, 1, 2, 3, 4, 6))
        member.comment = b"member comment"
        member.create_system = 3
        member.internal_attr = 1
        member.external_attr = 0o100644 << 16
        member.flag_bits = 0x9
        member.CRC = 123
        member.compress_size = 456
        member.file_size = 789
        member.extra = b"stale-extra-record"

        result = archive_module._clone_member_info(
            member,
            date_time=(2026, 8, 22, 16, 30, 58),
            compression=ZIP_STORED,
            path_limits=ArchivePathLimits(),
        )

        assert member.filename == "Chapter 01/001.jpg"
        assert member.date_time == (2020, 1, 2, 3, 4, 6)
        assert member.compress_type == ZIP_STORED
        assert member.flag_bits == 0x9
        assert member.CRC == 123
        assert member.compress_size == 456
        assert member.file_size == 789
        assert member.extra == b"stale-extra-record"
        assert result is not member
        assert result.filename == member.filename
        assert result.date_time == (2026, 8, 22, 16, 30, 58)
        assert result.compress_type == ZIP_STORED
        assert result.comment == member.comment
        assert result.create_system == member.create_system
        assert result.internal_attr == member.internal_attr
        assert result.external_attr == member.external_attr
        assert result.flag_bits == 0
        assert not hasattr(result, "CRC")
        assert result.compress_size == 0
        assert result.file_size == 0
        assert result.extra == b""

    def test_dos_metadata_is_sanitized(self) -> None:
        member = create_member(
            "001.jpg",
            create_system=ZIP_CREATOR_DOS,
            external_attr=(
                DOS_ATTRIBUTE_HIDDEN
                | DOS_ATTRIBUTE_ARCHIVE
                | DOS_ATTRIBUTE_TEMPORARY
            ),
        )

        result = archive_module._clone_member_info(
            member,
            date_time=(2026, 8, 22, 16, 30, 58),
            compression=ZIP_STORED,
            path_limits=ArchivePathLimits(),
        )

        assert result.create_system == ZIP_CREATOR_DOS
        assert result.external_attr == (
            DOS_ATTRIBUTE_HIDDEN | DOS_ATTRIBUTE_ARCHIVE
        )

    def test_replacement_filename_is_normalized(self) -> None:
        member = ZipInfo("001.jpg")

        result = archive_module._clone_member_info(
            member,
            date_time=(2026, 8, 22, 16, 30, 58),
            compression=ZIP_STORED,
            path_limits=ArchivePathLimits(),
            filename=r"Chapter 01\001.png",
        )

        assert result.filename == "Chapter 01/001.png"

    def test_custom_path_limits_are_applied_to_replacement_filename(self) -> None:
        member = ZipInfo("001.jpg")
        filename = "chapter/001.png"
        expected_message = (
            f"Archive member path exceeds 10 characters: {filename!r}."
        )

        with pytest.raises(
            InvalidArchiveError,
            match=exact_message(expected_message),
        ):
            archive_module._clone_member_info(
                member,
                date_time=(2026, 8, 22, 16, 30, 58),
                compression=ZIP_STORED,
                path_limits=ArchivePathLimits(max_path_length=10),
                filename=filename,
            )

    def test_unsupported_output_compression_is_rejected(self) -> None:
        expected_message = "Unsupported ZIP compression method for output."

        with pytest.raises(
            ValueError,
            match=exact_message(expected_message),
        ):
            archive_module._clone_member_info(
                ZipInfo("001.jpg"),
                date_time=(2026, 8, 22, 16, 30, 58),
                compression=ZIP_BZIP2,
                path_limits=ArchivePathLimits(),
            )


class TestWriteMemberData:
    @pytest.mark.parametrize(
        "compression",
        [
            ZIP_STORED,
            ZIP_DEFLATED,
        ],
    )
    def test_member_is_written_and_returned(
        self,
        compression: int,
    ) -> None:
        archive_stream = BytesIO()
        source_member = ZipInfo(
            "ComicInfo.xml",
            date_time=(2020, 1, 2, 3, 4, 6),
        )
        source_member.comment = b"member comment"
        data = b"<ComicInfo />" * 20

        with ZipFile(archive_stream, mode="w") as archive:
            result = write_member_data(
                archive,
                source_member,
                data,
                compression=compression,
                date_time_policy=MemberDateTimePolicy(
                    mode=MemberDateTimeMode.PRESERVE,
                ),
                transformed=False,
                path_limits=ArchivePathLimits(),
            )

            assert result is archive.getinfo("ComicInfo.xml")

        archive_stream.seek(0)
        with ZipFile(archive_stream, mode="r") as archive:
            member = archive.getinfo("ComicInfo.xml")
            assert archive.read(member) == data
            assert member.compress_type == compression
            assert member.date_time == source_member.date_time
            assert member.comment == source_member.comment

    def test_dos_metadata_is_sanitized_when_written(self) -> None:
        archive_stream = BytesIO()
        source_member = create_member(
            "001.jpg",
            create_system=ZIP_CREATOR_DOS,
            external_attr=(
                DOS_ATTRIBUTE_READ_ONLY
                | DOS_ATTRIBUTE_HIDDEN
                | DOS_ATTRIBUTE_ENCRYPTED
            ),
        )

        with ZipFile(archive_stream, mode="w") as archive:
            write_member_data(
                archive,
                source_member,
                b"image data",
                compression=ZIP_STORED,
                date_time_policy=MemberDateTimePolicy(
                    mode=MemberDateTimeMode.PRESERVE,
                ),
                transformed=False,
                path_limits=ArchivePathLimits(),
            )

        archive_stream.seek(0)
        with ZipFile(archive_stream, mode="r") as archive:
            output_member = archive.getinfo("001.jpg")
            assert output_member.create_system == ZIP_CREATOR_DOS
            assert output_member.external_attr == (
                DOS_ATTRIBUTE_READ_ONLY | DOS_ATTRIBUTE_HIDDEN
            )
            assert archive.read(output_member) == b"image data"

    def test_replacement_filename_is_written(self) -> None:
        archive_stream = BytesIO()
        source_member = ZipInfo("001.jpg", date_time=(2020, 1, 2, 3, 4, 6))

        with ZipFile(archive_stream, mode="w") as archive:
            write_member_data(
                archive,
                source_member,
                b"converted image",
                compression=ZIP_STORED,
                date_time_policy=MemberDateTimePolicy(
                    mode=MemberDateTimeMode.PRESERVE,
                ),
                transformed=True,
                path_limits=ArchivePathLimits(),
                filename="001.png",
            )

        archive_stream.seek(0)
        with ZipFile(archive_stream, mode="r") as archive:
            assert archive.namelist() == ["001.png"]
            assert archive.read("001.png") == b"converted image"

    def test_unicode_replacement_filename_is_written(self) -> None:
        archive_stream = BytesIO()
        source_member = ZipInfo(
            "001.jpg",
            date_time=(2020, 1, 2, 3, 4, 6),
        )
        output_filename = "進撃の巨人/第01話.png"

        with ZipFile(archive_stream, mode="w") as archive:
            write_member_data(
                archive,
                source_member,
                b"converted image",
                compression=ZIP_STORED,
                date_time_policy=MemberDateTimePolicy(
                    mode=MemberDateTimeMode.PRESERVE,
                ),
                transformed=True,
                path_limits=ArchivePathLimits(),
                filename=output_filename,
            )

        archive_stream.seek(0)

        with ZipFile(archive_stream, mode="r") as archive:
            assert archive.namelist() == [output_filename]
            assert archive.read(output_filename) == b"converted image"

    def test_empty_member_data_is_written(self) -> None:
        archive_stream = BytesIO()
        source_member = ZipInfo("notes.txt", date_time=(2020, 1, 2, 3, 4, 6))

        with ZipFile(archive_stream, mode="w") as archive:
            write_member_data(
                archive,
                source_member,
                b"",
                compression=ZIP_DEFLATED,
                date_time_policy=MemberDateTimePolicy(
                    mode=MemberDateTimeMode.PRESERVE,
                ),
                transformed=False,
                path_limits=ArchivePathLimits(),
            )

        archive_stream.seek(0)
        with ZipFile(archive_stream, mode="r") as archive:
            assert archive.read("notes.txt") == b""

    def test_write_delegates_resolved_timestamp_and_metadata(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        archive = Mock(spec=ZipFile)
        source_member = ZipInfo("001.jpg")
        output_member = ZipInfo("001.png")
        resolved_date_time = (2026, 8, 22, 16, 30, 58)
        policy = MemberDateTimePolicy()
        resolve = Mock(return_value=resolved_date_time)
        clone = Mock(return_value=output_member)
        monkeypatch.setattr(
            archive_module,
            "resolve_member_date_time",
            resolve,
        )
        monkeypatch.setattr(
            archive_module,
            "_clone_member_info",
            clone,
        )
        limits = ArchivePathLimits()

        result = write_member_data(
            archive,
            source_member,
            b"image data",
            compression=ZIP_STORED,
            date_time_policy=policy,
            transformed=True,
            path_limits=limits,
            filename="001.png",
        )

        assert result is output_member
        resolve.assert_called_once_with(
            source_member,
            policy=policy,
            transformed=True,
        )
        clone.assert_called_once_with(
            source_member,
            date_time=resolved_date_time,
            compression=ZIP_STORED,
            path_limits=limits,
            filename="001.png",
        )
        archive.writestr.assert_called_once_with(
            output_member,
            b"image data",
        )


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

    def test_inspect_cbz_applies_custom_read_limits(
        self,
        tmp_path: Path,
    ) -> None:
        archive_path = tmp_path / "volume.cbz"
        with ZipFile(
            archive_path,
            mode="w",
            compression=ZIP_DEFLATED,
        ) as archive:
            archive.writestr("001.jpg", b"image")
            archive.writestr("ComicInfo.xml", b"A" * 200_000)

        with pytest.raises(
            InvalidArchiveError,
            match=exact_message(
                "Archive member's declared expansion exceeds the permitted "
                "limit of 10:1 for 'ComicInfo.xml'."
            ),
        ):
            inspect_cbz(
                archive_path,
                read_limits=ArchiveReadLimits(
                    max_expansion_ratio=10,
                    expansion_grace_size=1,
                ),
            )

    def test_inspect_cbz_applies_custom_path_limits(
        self,
        tmp_path: Path,
    ) -> None:
        archive_path = tmp_path / "volume.cbz"
        filename = "Chapter 01/001.jpg"
        with ZipFile(archive_path, mode="w") as archive:
            archive.writestr(filename, b"image")
        expected_message = (
            f"Archive member path exceeds 10 characters: {filename!r}."
        )

        with pytest.raises(
            InvalidArchiveError,
            match=exact_message(expected_message),
        ):
            inspect_cbz(
                archive_path,
                path_limits=ArchivePathLimits(max_path_length=10),
            )

    @pytest.mark.parametrize(
        ("argument", "value", "expected_message"),
        [
            (
                "read_limits",
                False,
                "Archive read limits must be an ArchiveReadLimits instance.",
            ),
            (
                "path_limits",
                {},
                "Archive path limits must be an ArchivePathLimits instance.",
            ),
        ],
    )
    def test_inspect_cbz_rejects_invalid_policy_object(
        self,
        argument: str,
        value: object,
        expected_message: str,
        tmp_path: Path,
    ) -> None:
        archive_path = tmp_path / "volume.cbz"
        with ZipFile(archive_path, mode="w") as archive:
            archive.writestr("001.jpg", b"image")

        with pytest.raises(
            TypeError,
            match=exact_message(expected_message),
        ):
            inspect_cbz(
                archive_path,
                **{argument: value},  # type: ignore[arg-type]
            )

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
