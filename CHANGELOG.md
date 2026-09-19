# Changelog

Notable user-visible changes to CBZFit are documented here.

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
