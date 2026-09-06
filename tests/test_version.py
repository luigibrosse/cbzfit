# SPDX-License-Identifier: GPL-3.0-or-later

from importlib.metadata import version

from cbzfit import __version__


def test_distribution_metadata_matches_runtime_version() -> None:
    assert version("cbzfit") == __version__
