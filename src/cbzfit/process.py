# SPDX-License-Identifier: GPL-3.0-or-later

from dataclasses import dataclass, field
from io import BytesIO

from PIL import Image, UnidentifiedImageError

from cbzfit.decode import (
    UnsupportedImageContentError,
    validate_source_image,
)
from cbzfit.encode import (
    EncoderOptions,
    encode_image,
)
from cbzfit.image import resize_for_display


@dataclass(frozen=True)
class ImageProcessingOptions:
    """Store settings used to process one image."""

    portrait_screen_size: tuple[int, int]
    use_landscape_display: bool = True
    allow_upscale: bool = False
    encoder_options: EncoderOptions = field(
        default_factory=EncoderOptions
    )


@dataclass(frozen=True)
class ProcessedImage:
    """Contain processed image data and transformation details."""

    data: bytes
    format: str
    transformed: bool

    @property
    def size(self) -> int:
        """Return the processed image size in bytes."""
        return len(self.data)


def process_image_data(
    data: bytes,
    filename: str,
    *,
    options: ImageProcessingOptions,
) -> ProcessedImage:
    """Validate, resize, and encode one image member when required.

    Original encoded bytes are returned unchanged when the image already fits
    the target display and upscaling is disabled.
    """
    try:
        image = Image.open(
            BytesIO(data),
        )
    except Image.DecompressionBombError as error:
        raise UnsupportedImageContentError(
            f"Image dimensions exceed the permitted limit: {filename!r}."
        ) from error
    except (UnidentifiedImageError, OSError) as error:
        raise UnsupportedImageContentError(
            f"Image data could not be decoded: {filename!r}."
        ) from error

    with image:
        source_format = validate_source_image(
            image=image,
            filename=filename,
        )

        try:
            image.load()
        except Image.DecompressionBombError as error:
            raise UnsupportedImageContentError(
                f"Image dimensions exceed the permitted limit: {filename!r}."
            ) from error
        except OSError as error:
            raise UnsupportedImageContentError(
                f"Image data could not be decoded: {filename!r}."
            ) from error

        resized_image = resize_for_display(
            image=image,
            portrait_screen_size=options.portrait_screen_size,
            use_landscape_display=options.use_landscape_display,
            allow_upscale=options.allow_upscale,
        )

        if resized_image is image:
            return ProcessedImage(
                data=data,
                format=source_format,
                transformed=False,
            )

        try:
            encoded_image = encode_image(
                resized_image,
                output_format=source_format,
                options=options.encoder_options,
            )
        finally:
            resized_image.close()

    return ProcessedImage(
        data=encoded_image.data,
        format=encoded_image.format,
        transformed=True,
    )
