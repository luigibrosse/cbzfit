# CBZFit

**Fit manga and comic archives to your screen.**

CBZFit resizes images in CBZ archives to fit within a target display, while preserving their aspect ratio. It can preserve source image formats, or convert them to JPEG, PNG or WebP. Images that require no transformation are copied byte-for-byte by default. CBZFit preserves non-image members and leaves the source archive unchanged by default.

## Features

- Fit portrait pages and landscape spreads to a target display
- Preserve source formats or convert images to JPEG, PNG, or WebP
- Copy unchanged images without re-encoding
- Configure JPEG, PNG, and WebP encoding
- Apply valid EXIF orientation before resizing
- Preserve compatible ICC profiles by default
- Preserve archive member order and non-image members such as `ComicInfo.xml`
- Verify completed archives before atomic publication
- Report image transformations, operations, elapsed time, and archive-size change

## Requirements

- Python 3.12 or later

## Installation

Install CBZFit with [pipx](https://pipx.pypa.io/):

```console
pipx install cbzfit
```

For alternative installation methods, upgrading, and development installation, see [Installation and Upgrading](docs/user-guide.md#installation-and-upgrading).

## Quick start

Screen dimensions are required, must be positive, and must be supplied in portrait orientation.

```console
cbzfit "Manga Volume 01.cbz" --screen-width 1404 --screen-height 1872
```

When `DESTINATION` is omitted, the source must end in `.cbz` or `.zip`. CBZFit writes the result beside the source with ` [CBZFit]` inserted before the final extension. The above command results in:

```text
Manga Volume 01 [CBZFit].cbz
```

Specify the destination when needed:

```console
cbzfit zipped-images.data comic-resized.cbz --screen-width 1404 --screen-height 1872
```

Select JPEG as the output format:

```console
cbzfit png-source.cbz jpg-destination.cbz --screen-width 1404 --screen-height 1872 --output-format jpeg
```

## Good to know

CBZFit processes ZIP-based archives containing static JPEG, PNG, or WebP images. It rejects invalid archives, animated images, encrypted members, unsupported compression, special ZIP member types, unsafe or colliding paths, image content not matching its extension, and data exceeding configured safety limits.

By default, CBZFit assumes that the reader or device displays landscape spreads (double pages) in landscape orientation. CBZFit therefore fits landscape images within landscape display bounds but does not rotate them.

Output is written to a temporary archive beside the destination. It is verified by default and published atomically. The source remains unchanged unless in-place replacement is explicitly requested. Existing destinations are not overwritten unless replacement is explicitly requested.

## Learn more

- [User guide](docs/user-guide.md): workflows, behavior, metadata, compatibility, summaries, troubleshooting and installation
- [CLI reference](docs/cli-reference.md): complete options, defaults, accepted values, boundaries, and interactions

Run the built-in help for a concise command summary:

```console
cbzfit --help
```

## Versioning

CBZFit follows [Semantic Versioning](https://semver.org/). Before 1.0, minor releases may introduce documented CLI or behavior changes.

User-visible changes are recorded in the [changelog](CHANGELOG.md).

## License

CBZFit is licensed under the GNU General Public License v3.0 or later.

## Inspiration

[Kindle Comic Converter](https://github.com/ciromattia/kcc) & [reCBZ](https://github.com/avalonv/reCBZ)
