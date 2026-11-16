# SPDX-License-Identifier: GPL-3.0-or-later

import re
from typing import cast

from PIL import ImageCms


def exact_message(message: str) -> str:
    """Return a regular expression that matches a complete error message."""
    return rf"^{re.escape(message)}$"

def create_srgb_profile_bytes() -> bytes:
    """Create valid sRGB ICC profile bytes for tests."""
    # Pillow's conditional native-module import obscures the return type.
    profile = cast(object, ImageCms.createProfile("sRGB"))  # pyright: ignore[reportUnknownMemberType]
    return ImageCms.ImageCmsProfile(profile).tobytes()
