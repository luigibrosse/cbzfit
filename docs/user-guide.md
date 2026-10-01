# CBZFit user guide

This guide explains common CBZFit workflows and image-processing behavior. For exact option names, accepted values, defaults, and boundaries, see the [CLI reference](cli-reference.md).

## Before you begin

CBZFit processes ZIP-based CBZ archives containing static JPEG, PNG, or WebP images. Every command requires the target display width and height in portrait orientation:

```console
cbzfit SOURCE [DESTINATION] --screen-width PIXELS --screen-height PIXELS
```

Screen dimensions must be positive, and the width must not exceed the height.

## Common workflows

### Resize supported images

```console
cbzfit source.cbz --screen-width 1404 --screen-height 1872
```

### Select JPEG as the output format

```console
cbzfit source.cbz output.cbz --screen-width 1404 --screen-height 1872 --output-format jpeg
```

### Configure JPEG encoding when required

The selected quality applies only to images that require JPEG encoding. Fitting JPEG images remain unchanged unless `--reencode` is also specified.

```console
cbzfit source.cbz output.cbz --screen-width 1404 --screen-height 1872 --output-format jpeg --jpeg-quality 90
```

### Perform CRC verification of the output

```console
cbzfit source.cbz output.cbz --screen-width 1404 --screen-height 1872 --verify crc
```

## Process an archive

### Use an automatically derived destination

When the source ends in `.cbz` or `.zip`, omit the destination to write the result beside the source:

```console
cbzfit manga.cbz --screen-width 1404 --screen-height 1872
```

The result is:

```text
manga [CBZFit].cbz
```

The source extension and its casing are preserved.

### Specify the destination

Provide the destination explicitly when the source has another extension, when a different output directory is required, or when you want a specific name:

```console
cbzfit zipped-images.data output/comic.cbz --screen-width 1404 --screen-height 1872
```

The destination directory must already exist.

### Replace an existing destination

The default conflict mode is `error`. Existing destinations are not overwritten. Use replacement explicitly:

```console
cbzfit source.cbz output.cbz --screen-width 1404 --screen-height 1872 --conflict replace
```

In-place processing also requires replacement:

```console
cbzfit manga.cbz manga.cbz --screen-width 1404 --screen-height 1872 --conflict replace
```

CBZFit processes into a temporary file, verifies it as configured, and atomically replaces the destination only after processing succeeds.

## Configure the target display

### Portrait and landscape pages

Supply the physical display resolution in portrait orientation. The width must not exceed the height. Portrait images are fitted within those bounds.

By default, landscape images are fitted as if the display were rotated to landscape orientation. Disable that behavior to use portrait bounds for every image:

```console
cbzfit manga.cbz --screen-width 1404 --screen-height 1872 --no-landscape-display
```

CBZFit does not rotate landscape pages, independently of which option is chosen.

### Upscaling

Images smaller than the selected display bounds are not enlarged by default. Enable upscaling explicitly:

```console
cbzfit manga.cbz --screen-width 1404 --screen-height 1872 --upscale
```

Any size change caused by fitting or upscaling is reported as a resize operation.

## Choose the output image format

### Preserve source formats

The default output format is `original`. Each image retains its decoded source format. An image is copied byte-for-byte when it does not require resizing, EXIF reorientation, format conversion, or forced re-encoding.

```console
cbzfit source.cbz output.cbz --screen-width 1404 --screen-height 1872
```

### Convert to JPEG

```console
cbzfit source.cbz output.cbz --screen-width 1404 --screen-height 1872 --output-format jpeg
```

`jpeg` and `jpg` both select canonical JPEG output. Converted filenames use `.jpg`.

JPEG cannot represent transparency. Transparent and partially transparent source pixels are composited onto an opaque white background before JPEG encoding.

### Convert to PNG

```console
cbzfit source.cbz output.cbz --screen-width 1404 --screen-height 1872 --output-format png
```

Converted filenames use `.png`. Supported transparency is preserved.

### Convert to WebP

```console
cbzfit source.cbz output.cbz --screen-width 1404 --screen-height 1872 --output-format webp
```

Converted filenames use `.webp`. Supported transparency is preserved.

WebP support varies across older comic readers, e-readers, operating systems, and devices. Test the generated archive on the intended reader before converting a collection to WebP. JPEG or PNG may be safer for older software.

### Output filename safety

CBZFit resolves all final member filenames before image processing begins. Conversion must not create an exact or cross-platform portable collision with another output member. A collision stops processing before publication and preserves the source and any existing destination.

## Control image re-encoding

### Default pass-through behavior

Selecting the same format as the source does not force encoding. Likewise, changing encoder settings does not force encoding by itself. A fitting image is copied unchanged unless another transformation is required.

This command preserves fitting JPEG bytes:

```console
cbzfit source.cbz output.cbz --screen-width 1404 --screen-height 1872 --output-format jpeg --jpeg-quality 90
```

### Force same-format re-encoding

Use `--reencode` to apply encoder settings to images that would otherwise be copied unchanged:

```console
cbzfit source.cbz output.cbz --screen-width 1404 --screen-height 1872 --reencode --jpeg-quality 90
```

Forced re-encoding is reported only when `--reencode` causes same-format encoding and resizing, conversion, and EXIF reorientation were not otherwise required. If other transformations already require encoding, the corresponding operations are reported instead of forced re-encoding.

## Configure JPEG output

JPEG options apply only when an image is encoded as JPEG.

```console
cbzfit source.cbz output.cbz --screen-width 1404 --screen-height 1872 --output-format jpeg --jpeg-quality 90 --jpeg-optimize --jpeg-progressive
```

- Quality controls the normal JPEG quality setting.
- Optimization is enabled by default.
- Progressive encoding is disabled by default.
- Transparency is flattened on opaque white.
- Modes that JPEG cannot encode directly are converted to a compatible grayscale or RGB representation.

Supplying JPEG settings does not cause PNG or WebP output to use them.

## Configure PNG output

PNG options apply only when an image is encoded as PNG.

```console
cbzfit source.cbz output.cbz --screen-width 1404 --screen-height 1872 --output-format png --png-compress-level 9 --png-optimize
```

PNG optimization is enabled by default. When optimization is enabled, Pillow uses maximum compression effort and the selected compression level has no effect. Disable optimization when the explicit level must be used:

```console
cbzfit source.cbz output.cbz --screen-width 1404 --screen-height 1872 --output-format png --no-png-optimize --png-compress-level 6
```

## Configure WebP output

WebP options apply only when an image is encoded as WebP.

```console
cbzfit source.cbz output.cbz --screen-width 1404 --screen-height 1872 --output-format webp --webp-quality 90 --webp-method 6
```

Enable lossless WebP explicitly:

```console
cbzfit source.cbz output.cbz --screen-width 1404 --screen-height 1872 --output-format webp --webp-lossless
```

Lossless mode is independent of the selected quality and method values accepted by the encoder.

## Control image metadata

### ICC profiles

Compatible embedded ICC profiles are preserved by default when the image mode remains compatible with the selected output format.

Disable profile preservation with:

```console
cbzfit source.cbz output.cbz --screen-width 1404 --screen-height 1872 --no-preserve-icc-profile
```

ICC preservation is best effort:

- Empty or invalid profile values are not preserved.
- If an image mode changes, the source profile is not copied unless CBZFit performs a successful color-managed CMYK-to-RGB conversion.
- A CMYK image with a usable profile is converted to sRGB; the generated sRGB profile is retained only when profile preservation is enabled.
- If color-managed conversion fails, CBZFit falls back to ordinary RGB conversion without retaining the incompatible source profile.

### EXIF orientation and metadata

CBZFit reads the EXIF orientation before resizing:

- Missing orientation and orientation `1` leave pixels unchanged and do not trigger encoding.
- Valid orientations `2` through `8` are physically applied to the pixels and count as EXIF reorientation.
- Invalid orientation values are rejected.
- Orientation is removed after it is applied so readers or devices do not rotate the output a second time.

When an image is encoded, existing non-empty EXIF bytes are forwarded to the selected encoder. Metadata support still depends on the output format and Pillow encoder. CBZFit does not promise preservation of arbitrary format-specific metadata outside the handled ICC and EXIF data.

Images copied without encoding retain their original bytes and therefore retain all metadata already present in those bytes.

## Verify the output archive

Select verification with `--verify MODE`:

- `structure`, the default, reopens the generated archive and validates its ZIP structure
- `crc` reads every output member and validates compressed data and CRC
- `none` trusts successful ZIP finalization and skips reopening the output

For the strongest verification:

```console
cbzfit source.cbz output.cbz --screen-width 1404 --screen-height 1872 --verify crc
```

Verification occurs before publication. A verification failure removes the temporary output and preserves the source and any existing destination.

## Progress and completion output

Progress is shown automatically when standard error is an interactive terminal. Transformation and CRC verification display member-based progress. Redirected standard error suppresses progress automatically. Disable it explicitly with `--no-progress`.

Successful processing prints four summary lines:

```text
Output: optimized.cbz completed in 10.5 s
└─Images: 10 total, 7 transformed, 3 unchanged. Other members copied: 2
└─Operations: 5 resized, 4 converted, 2 re-encoded, 1 EXIF-reoriented
└─Size: 180.0 MiB -> 96.0 MiB, 46.7 % decrease
```

### Image results

- **Transformed** means the encoded image bytes changed.
- **Unchanged** means the original image bytes were copied.
- Transformed plus unchanged always equals the number of image members.
- Other members are copied separately and are not included in the image total.

### Operations

- **Resized** means the encoded pixel dimensions changed through fitting or upscaling.
- **Converted** means the final encoded format differs from the decoded source format.
- **Re-encoded** means `--reencode` caused otherwise unnecessary same-format encoding.
- **EXIF-reoriented** means a valid non-normal EXIF orientation was physically applied.

Resize, conversion, and EXIF reorientation can apply to the same image, so operation counters do not have to sum to the transformed-image count. Forced same-format re-encoding is exclusive of those other reasons. The operation line is shown even when every counter is zero.

## Troubleshooting

### An image was copied unchanged

This is expected when no resize, conversion, EXIF reorientation, or forced re-encoding is required. Add `--reencode` if encoder settings must be applied to otherwise unchanged images. Re-encoding JPEG or lossy WebP images can introduce additional quality loss.

### Encoder settings did not take effect

Encoder settings apply only when that format is encoded. Select the corresponding output format, require another transformation, or add `--reencode` for fitting same-format images.

### Converted filenames changed

Conversion replaces the final image suffix with the canonical extension: `.jpg`, `.png`, or `.webp`. Parent directories and the filename stem are preserved.

### Conversion reports a collision

Two source members would produce identical or cross-platform equivalent output paths. Rename or remove one conflicting source member before retrying. CBZFit does not overwrite one archive member with another.

### WebP images do not open on a device

The reader may not support WebP. Convert to JPEG or PNG instead, or update the reading software if possible.

### The destination already exists

Choose a different destination or explicitly use `--conflict replace`. Review the path carefully before enabling replacement.

## Installation and upgrading

### With pipx

pipx is the recommended installation method for CBZFit:

```console
pipx install cbzfit
```

Upgrade with:

```console
pipx upgrade cbzfit
```

### With pip

Install CBZFit into the active Python environment:

```console
python -m pip install cbzfit
```

Upgrade with:

```console
python -m pip install --upgrade cbzfit
```

### Development

Clone the repository and create a virtual environment:

```console
git clone https://github.com/luigibrosse/cbzfit.git
cd cbzfit
python -m venv .venv
```

Activate it:

```console
# Windows PowerShell
.venv\Scripts\Activate.ps1

# Linux or macOS
source .venv/bin/activate
```

Install CBZFit with the development dependencies:

```console
python -m pip install -e ".[dev]"
```

Run Ruff and the complete test suite with branch coverage:

```console
python -m ruff check .
python -m pytest --cov=cbzfit --cov-branch --cov-report=term-missing --cov-fail-under=100
```

Build and validate the distributions:

```console
python -m pip install --upgrade build twine
python -m build
python -m twine check --strict dist/*
```
