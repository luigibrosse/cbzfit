# SPDX-License-Identifier: GPL-3.0-or-later

import argparse

from cbzfit import __version__


def build_parser() -> argparse.ArgumentParser:
    """Build and return the CBZFit command-line parser."""
    parser = argparse.ArgumentParser(
        prog="cbzfit",
        description="Resize manga CBZ archives to fit a target display.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main() -> int:
    """Run the CBZFit command-line interface."""
    parser = build_parser()
    parser.parse_args()
    return 0
