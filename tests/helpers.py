# SPDX-License-Identifier: GPL-3.0-or-later

import re


def exact_message(message: str) -> str:
    """Return a regular expression that matches a complete error message."""
    return rf"^{re.escape(message)}$"
