# CBZFit

**Fit manga and comic archives to your screen.**

CBZFit resizes images in CBZ archives for a target display resolution while preserving their aspect ratio and original image format. Images that already fit are copied without re-encoding, and the source archive remains unchanged unless replacement is explicitly enabled.

## Features

- Resize portrait pages and landscape spreads for a target display
- Preserve JPEG, PNG, and WebP image formats
- Keep images unchanged when resizing is unnecessary
- Preserve non-image archive members such as `ComicInfo.xml`
- Verify the output archive before writing it to its destination
- Write the output atomically to avoid incomplete archives

## Requirements

- Python 3.12 or later

## Installation

Clone the repository and install CBZFit in a virtual environment:

```console
git clone https://github.com/luigibrosse/cbzfit.git
cd cbzfit
python -m venv .venv
```

Activate the environment:

```console
# Windows PowerShell
.venv\Scripts\Activate.ps1

# Linux or macOS
source .venv/bin/activate
```

Install the project:

```console
python -m pip install .
```

## Usage

```console
cbzfit SOURCE DESTINATION --screen-width PIXELS --screen-height PIXELS
```

Screen dimensions must be positive and supplied in portrait orientation, with the width no greater than the height.

For example, resize an archive to a 1404 x 1872 display:

```console
cbzfit comic.cbz comic-resized.cbz   --screen-width 1404   --screen-height 1872
```

By default, landscape images are fitted to the display in landscape orientation. To use portrait display bounds for every image:

```console
cbzfit manga.cbz manga-resized.cbz   --screen-width 1404   --screen-height 1872   --no-landscape-display
```

Small images are not enlarged by default. Enable upscaling when required:

```console
cbzfit manga.cbz manga-resized.cbz   --screen-width 1404   --screen-height 1872   --upscale
```

Use `--conflict replace` to replace an existing destination or process an archive in place:

```console
cbzfit comic.cbz comic.cbz   --screen-width 1404   --screen-height 1872   --conflict replace
```

Progress is shown automatically on interactive terminals and is written to
standard error, leaving the final summary on standard output. Progress is
suppressed when output is redirected. Disable it explicitly with
`--no-progress`.

Run the built-in help for the complete command reference:

```console
cbzfit --help
```

## Supported files

CBZFit processes ZIP-based CBZ archives containing static JPEG, PNG, or WebP images. Other archive members are copied to the output unchanged.

Animated images, encrypted archive members, unsafe member paths, and unsupported ZIP compression methods are rejected.

## Development

Install the development dependencies and run the checks:

```console
python -m pip install -e ".[dev]"
python -m pytest --cov=cbzfit --cov-branch --cov-report=term-missing --cov-fail-under=100
python -m ruff check .
```

## Versioning

CBZFit follows [Semantic Versioning](https://semver.org/). Version 0.1.0 is an
initial pre-1.0 release. Before 1.0, minor releases may introduce documented
CLI or behavior changes. Patch releases are intended for backward-compatible
fixes and documentation or packaging corrections.

User-visible changes are recorded in [CHANGELOG.md](CHANGELOG.md).
`src/cbzfit/__init__.py` is the authoritative version source through
`cbzfit.__version__`; Hatchling derives the package metadata version from it.

## License

CBZFit is licensed under the GNU General Public License v3.0 or later.
