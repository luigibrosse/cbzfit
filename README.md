# CBZFit

**Fit manga and comic archives to your screen.**

CBZFit resizes images in CBZ archives for a target display resolution while preserving their aspect ratio and original image format. Images that already fit are copied without re-encoding. CBZFit preserves non-image members, and leaves the source archive unchanged by default.

## Features

- Resize portrait pages and landscape spreads for a target display
- Preserve JPEG, PNG, and WebP image formats
- Copy images without re-encoding when resizing is unnecessary
- Preserve archive member order and non-image members such as `ComicInfo.xml`
- Derive a safe destination name when `DESTINATION` is omitted
- Verify generated archives before publication
- Publish output atomically to avoid incomplete destination archives
- Display interactive transformation and verification progress
- Report image counts, elapsed time, and archive-size change

## Requirements

- Python 3.12 or later

## Installation

### Recommended: pipx

Install CBZFit from PyPI with [pipx](https://pipx.pypa.io/):

```console
pipx install cbzfit
```

Upgrade an existing installation with:

```console
pipx upgrade cbzfit
```

## Usage

```console
cbzfit SOURCE [DESTINATION] --screen-width PIXELS --screen-height PIXELS
```

Screen dimensions must be positive and supplied in portrait orientation, with the width no greater than the height.

For example, resize an archive to a 1404 x 1872 display:

```console
cbzfit "Manga Volume 01.cbz" --screen-width 1404 --screen-height 1872
```

When `DESTINATION` is omitted, the final extension of `SOURCE` must be `.cbz` or `.zip`. CBZFit inserts ` [CBZFit]` before that extension and writes the output beside the source. The above example creates:

```text
Manga Volume 01 [CBZFit].cbz
```

### Specify the destination

For a source with another extension or no extension, provide `DESTINATION` explicitly.

```console
cbzfit zipped-images.data comic-resized.cbz --screen-width 1404 --screen-height 1872
```

### Landscape pages

By default, landscape images are fitted to the display in landscape orientation. To use portrait display bounds for every image:

```console
cbzfit manga.cbz --screen-width 1404 --screen-height 1872 --no-landscape-display
```

### Upscaling

Images smaller than the target display are not enlarged by default. Enable upscaling with:

```console
cbzfit comic.cbz --screen-width 1404 --screen-height 1872 --upscale
```

### Existing destinations

The default conflict mode is `error`, which stops without modifying the source or destination. Use `--conflict replace` to replace an existing destination or process an archive in place:

```console
cbzfit manga.cbz manga.cbz --screen-width 1404 --screen-height 1872 --conflict replace
```

### Output verification

Select verification with `--verify MODE`:

- `structure`, the default, reopens the generated archive and validates its ZIP structure
- `crc` reads every output member and validates its compressed data and CRC
- `none` trusts successful ZIP finalization and skips reopening the output

For the strongest verification:

```console
cbzfit comic.cbz --screen-width 1404 --screen-height 1872 --verify crc
```

### Progress

Progress is shown automatically when standard error is an interactive terminal. Transformation and CRC verification display member-based progress bars. Progress is written to standard error so the final summary remains on standard output.

Progress is suppressed when standard error is redirected. Disable it explicitly with:

```console
cbzfit manga.cbz --screen-width 1404 --screen-height 1872 --no-progress
```

### Completion summary

Successful processing prints a three-line summary:

```text
Output: optimized.cbz completed in 10.5 s
└─Images: 200 total, 100 transformed, 100 unchanged. Other members copied: 100
└─Size: 180.0 MiB -> 95.8 MiB, 46.8 % decrease
```

Run the built-in help for the complete command reference:

```console
cbzfit --help
```

## Supported files and behavior

CBZFit processes ZIP-based archives containing static JPEG, PNG, or WebP images.

- Resized images preserve their source image format.
- Images that already fit are copied without re-encoding.
- Non-image members, including `ComicInfo.xml`, are preserved in archive order but are not interpreted or edited.
- The source archive remains unchanged unless in-place replacement is explicitly requested.
- Generated output is finalized, optionally verified, and published atomically.

CBZFit rejects:

- archives without supported images
- invalid ZIP archives
- animated images
- encrypted archive members
- unsafe or duplicate member paths
- unsupported ZIP compression methods
- image content that does not match its filename extension
- archive members or archives that exceed the configured safety limits

## Development

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

Install CBZFit in editable mode with the development dependencies:

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

## Versioning

CBZFit follows [Semantic Versioning](https://semver.org/). Version 0.1.0 is an initial pre-1.0 release. Before 1.0, minor releases may introduce documented CLI or behavior changes. Patch releases are intended for backward-compatible fixes and documentation or packaging corrections.

User-visible changes are recorded in [CHANGELOG.md](CHANGELOG.md). `src/cbzfit/__init__.py` is the authoritative version source through `cbzfit.__version__`; Hatchling derives the package metadata version from it.

## License

CBZFit is licensed under the GNU General Public License v3.0 or later.
