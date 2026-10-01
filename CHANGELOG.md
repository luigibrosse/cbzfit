# Changelog

Notable user-visible changes to CBZFit are documented here.

## 0.2.0

Image processing and conversion release.

### Added

- Select original-format, JPEG, PNG, or WebP image output from the command line.
- Use `jpeg` or `jpg` to select canonical JPEG output with `.jpg` filenames.
- Force same-format re-encoding of otherwise unchanged images with `--reencode`.
- Configure JPEG quality, optimization, and progressive encoding.
- Configure PNG compression and optimization.
- Configure WebP quality, method, and lossless encoding.
- Control compatible ICC-profile preservation during encoding.
- Report resized, converted, forced re-encoded, and EXIF-reoriented image counts.

### Changed

- Apply valid non-normal EXIF orientation before display fitting and remove the applied orientation from encoded output.
- Preserve fitting image bytes when no resize, conversion, EXIF reorientation, or forced re-encoding is required.
- Use canonical `.jpg`, `.png`, and `.webp` member extensions after format conversion.
- Composite transparent source pixels onto opaque white when encoding JPEG.
- Preserve supported PNG and WebP transparency during encoding.
- Preserve compatible ICC and EXIF metadata during encoding within the selected format's capabilities.
- Print image operations on a separate completion-summary line.

### Security

- Resolve and validate converted member paths before image processing begins.
- Reject exact and cross-platform portable output collisions introduced by conversion.
- Preserve the source and existing destination and clean temporary output after conversion-collision failures.

### Compatibility

- Preserve the previous default format-preserving and same-format pass-through behavior when new image options are omitted.
- Keep encoder settings format-specific and prevent them from implicitly forcing re-encoding.
- Document that WebP support varies across older readers and devices.

## 0.1.1

Security hardening release.

### Security

- Enforce explicit image pixel limits and handle Pillow decompression-bomb warnings and errors safely.
- Reject unsafe ZIP member types, non-portable paths, path collisions, and extreme declared expansion ratios.
- Validate configuration limits and nested policy-object types strictly.
- Pin GitHub Actions to reviewed commit SHAs and add Dependabot, dependency auditing, and CodeQL analysis.
- Document private vulnerability reporting and coordinated disclosure.

### Compatibility

- Preserve supported v0.1.0 archive-processing and command-line behavior for valid inputs.

## 0.1.0

Initial release.

### Added

- Resize static JPEG, PNG, and WebP images for a target display while preserving aspect ratio and source image format.
- Copy images without re-encoding when resizing is unnecessary.
- Preserve archive member order and non-image members such as `ComicInfo.xml`.
- Derive a destination containing ` [CBZFit]` when `DESTINATION` is omitted for a `.cbz` or `.zip` source.
- Verify generated archives using structural or CRC checks before publication.
- Publish completed output atomically and protect existing destinations through explicit conflict handling.
- Display interactive transformation and verification progress while keeping the final processing summary on standard output.
