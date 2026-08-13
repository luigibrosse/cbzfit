# SPDX-License-Identifier: GPL-3.0-or-later

from cbzfit.cli import build_parser


def test_program_name() -> None:
    parser = build_parser()

    assert parser.prog == "cbzfit"
