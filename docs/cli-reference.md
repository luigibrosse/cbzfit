# CBZFit CLI reference

This reference defines the released command-line interface. For task-oriented instructions and behavioral explanations, see the [user guide](user-guide.md).

## Command syntax

```console
cbzfit SOURCE [DESTINATION] --screen-width PIXELS --screen-height PIXELS [OPTIONS]
```

The same interface is available through:

```console
python -m cbzfit SOURCE [DESTINATION] --screen-width PIXELS --screen-height PIXELS [OPTIONS]
```

## Positional arguments

### `SOURCE`

Source ZIP-based CBZ archive.

- **Required:** yes
- **Accepted automatic-destination extensions:** `.cbz`, `.zip`, case-insensitive
- **Behavior:** remains unchanged unless source and destination identify the same file and replacement is explicitly enabled

### `DESTINATION`

Destination archive.

- **Required:** no
- **Default:** derived from `SOURCE`
- **Derived name:** inserts ` [CBZFit]` before the final `.cbz` or `.zip` extension
- **Requirement:** the destination parent directory must already exist

If `DESTINATION` is omitted and the source does not end in `.cbz` or `.zip`, argument processing fails.

## Required display options

### `--screen-width PIXELS`

Target display width in portrait orientation.

- **Type:** positive integer
- **Required:** yes

### `--screen-height PIXELS`

Target display height in portrait orientation.

- **Type:** positive integer
- **Required:** yes

The width must not exceed the height.

## Display behavior

### `--landscape-display` / `--no-landscape-display`

Controls whether landscape images are fitted using the display in landscape orientation.

- **Default:** enabled
- **Effect:** swaps display bounds for landscape images when enabled
- **Does not:** rotate an image solely because it is landscape

### `--upscale`

Allows images smaller than the selected display bounds to be enlarged.

- **Default:** disabled
- **Effect:** an increased encoded dimension counts as resizing

## Output format and re-encoding

### `--output-format FORMAT`

Selects the encoded image format.

- **Accepted values:** `original`, `jpeg`, `jpg`, `png`, `webp`
- **Default:** `original`
- **Aliases:** `jpeg` and `jpg` both select JPEG

Canonical formats and converted filename extensions:

- JPEG uses `.jpg`
- PNG uses `.png`
- WebP uses `.webp`
- `original` preserves the normalized source member path and source image format

Selecting the same format as the source does not force encoding.

### `--reencode`

Forces encoding when resizing, conversion, and EXIF reorientation are otherwise unnecessary.

- **Default:** disabled
- **Effect:** applies the selected format's encoder settings to fitting same-format images
- **Reporting:** counts as forced re-encoding only when no other transformation reason requires encoding

Encoder settings alone do not force encoding.

## JPEG options

JPEG options apply only when JPEG is encoded.

### `--jpeg-quality 0-95`

- **Type:** integer
- **Valid range:** 0 through 95, inclusive
- **Default:** 85

### `--jpeg-optimize` / `--no-jpeg-optimize`

- **Default:** enabled
- **Effect:** enables or disables Pillow JPEG optimization

### `--jpeg-progressive` / `--no-jpeg-progressive`

- **Default:** disabled
- **Effect:** selects progressive or non-progressive JPEG encoding

JPEG output is grayscale or RGB compatible. Transparency is composited onto opaque white before encoding.

## PNG options

PNG options apply only when PNG is encoded.

### `--png-compress-level 0-9`

- **Type:** integer
- **Valid range:** 0 through 9, inclusive
- **Default:** 9
- **Interaction:** ignored by Pillow when PNG optimization is enabled

### `--png-optimize` / `--no-png-optimize`

- **Default:** enabled
- **Effect:** enables or disables Pillow PNG optimization

Supported PNG transparency is preserved.

## WebP options

WebP options apply only when WebP is encoded.

### `--webp-quality 0-100`

- **Type:** integer
- **Valid range:** 0 through 100, inclusive
- **Default:** 85

### `--webp-method 0-6`

- **Type:** integer
- **Valid range:** 0 through 6, inclusive
- **Default:** 4

### `--webp-lossless` / `--no-webp-lossless`

- **Default:** disabled
- **Effect:** selects lossless or lossy WebP encoding

Supported WebP transparency is preserved. Older readers and devices may not support WebP.

## Metadata option

### `--preserve-icc-profile` / `--no-preserve-icc-profile`

Controls compatible embedded ICC-profile preservation during encoding.

- **Default:** enabled
- **Scope:** applies only when an image is encoded
- **Limitation:** source ICC data is copied only when compatible with the prepared image mode
- **CMYK behavior:** a successful color-managed CMYK-to-RGB conversion may produce and preserve an sRGB profile

Images copied byte-for-byte retain metadata already present in the original encoded data.

EXIF orientation is not controlled by a separate CLI option. Valid non-normal orientation is always applied before resizing. Existing non-empty EXIF bytes are forwarded when encoding, subject to format and Pillow support.

## Verification

### `--verify MODE`

Selects output verification before publication.

- **Accepted values:** `none`, `structure`, `crc`
- **Default:** `structure`

Modes:

- `none`: trust successful ZIP finalization
- `structure`: reopen the archive and parse its ZIP structure
- `crc`: read every output member and validate its compressed data and CRC

## Progress

### `--no-progress`

Disables interactive progress.

- **Default:** progress is enabled when standard error is an interactive terminal
- **Output stream:** progress uses standard error
- **Summary stream:** the completion summary uses standard output

Progress is suppressed automatically when standard error is not interactive.

## Destination conflict handling

### `--conflict MODE`

Controls behavior when the destination exists.

- **Accepted values:** `error`, `replace`
- **Default:** `error`

Modes:

- `error`: stop without modifying the existing destination
- `replace`: atomically replace the destination after successful processing and verification

In-place processing requires `replace`.

## Informational options

### `--version`

Prints the installed CBZFit version and exits.

### `-h` / `--help`

Prints command help and exits.

## Default configuration

The effective CLI defaults are:

- landscape display fitting: enabled
- upscaling: disabled
- output format: `original`
- forced re-encoding: disabled
- JPEG quality: 85
- JPEG optimization: enabled
- progressive JPEG: disabled
- PNG compression level: 9
- PNG optimization: enabled
- WebP quality: 85
- WebP method: 4
- lossless WebP: disabled
- ICC-profile preservation: enabled
- output verification: `structure`
- interactive progress: enabled when standard error is interactive
- destination conflict handling: `error`

## Option interactions

### Unchanged pass-through

An image is copied byte-for-byte when all of these are true:

- its EXIF orientation is missing or `1`
- its dimensions do not change
- its selected output format equals its decoded source format
- `--reencode` is not enabled

### Encoding conditions

Encoding occurs when at least one of these is true:

- EXIF orientation `2` through `8` is applied
- fitting or upscaling changes dimensions
- the selected output format differs from the source format
- `--reencode` forces otherwise unnecessary same-format encoding

### Format-specific settings

JPEG, PNG, and WebP settings may be supplied together. Only the settings for the format being encoded are used. Supplying a setting does not select its format and does not force encoding.

### Operation overlap

Resize, conversion, and EXIF reorientation can apply to the same image. Forced same-format re-encoding applies only when none of those other reasons applies.

### Conversion filenames and collisions

When converting, CBZFit replaces the final image suffix with the canonical extension before reading member data. It rejects exact and cross-platform portable output collisions before publication.

## Completion summary

Successful processing prints:

```text
Output: DESTINATION completed in ELAPSED s
└─Image(s): TOTAL total, TRANSFORMED transformed, UNCHANGED unchanged. Other member(s) copied: COPIED
└─Operation(s): RESIZED resized, CONVERTED converted, REENCODED re-encoded, REORIENTED EXIF-reoriented
└─Size: SOURCE_SIZE -> DESTINATION_SIZE, CHANGE
```

Operation counters can overlap and are not required to sum to the transformed-image count.

## Exit behavior

- Successful processing returns status `0`.
- Argument parsing and validation failures produce an argparse error.
- Expected archive, image, path, verification, publication, and filesystem failures return status `1` with a concise error message.
- Unexpected exceptions are not hidden.
- Failed processing removes temporary output when possible and does not publish an incomplete destination.
