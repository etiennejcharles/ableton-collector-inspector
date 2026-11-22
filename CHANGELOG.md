# Changelog

All notable changes to Collector Inspector are documented here.

## Unreleased

- Rename the desktop and terminal app to Collector Inspector. Add branded commands and retain the original command aliases.


- Add a dedicated Plug-ins tab with AU, VST2, VST3, and unknown format totals grouped by track.
- Add plug-in metadata to CLI, terminal, JSON, and Markdown inspection reports.
- Rename the workspace navigation action to Home and add an Alt+Left shortcut.

## 1.0.0 — 2026-07-31

- Added native desktop, terminal review, and command-line workflows.
- Added recursive and mixed-source scanning with backup exclusion.
- Added JSON and Markdown audit reports.
- Added safe batch collection with `.asd` sidecar support.
- Added byte-level media identity and destination verification.
- Added portable collision-safe imported filenames.
- Added atomic copies, atomic Set replacement, verified unique backups, and process locks.
- Added automatic rollback and cleanup of files created by failed collections.
- Added safe backup restore with a safety backup of the replaced Set.
- Added OGG Vorbis and AIFF-C detection.
- Added moved-project path fallback, alternate separator support, case-tolerant resolution,
  and case-insensitive `.als` discovery.
- Added macOS, Windows, and Linux packaging scripts and CI coverage.
