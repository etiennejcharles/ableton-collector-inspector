# Ableton Collector Inspector

> Made with love ❤️ for the Ableton community

Collector Inspector inspects VSTs, other plug-ins, and audio dependencies in
Ableton Live projects. It flags missing audio and can safely collect selected
external audio into each project. It includes a desktop app, an interactive
terminal review screen, and automation-friendly commands for macOS, Windows, and Linux.

It works locally. Audio and Set data are never uploaded.

## Quick start

With Python 3.11 or newer, follow the [source installation steps](#install-from-source),
then launch `collector-inspector` and drop in a Set or project folder. To inspect a
single Set from the terminal, run:

```bash
collector-inspector-cli inspect "/path/to/Set.als"
```

Inspection lists plug-ins and audio references without modifying the Set. For external
audio, preview a collection plan with `collector-inspector-cli collect "/path/to/Set.als" --dry-run`.

## What it does

- Scans individual `.als` files, mixed selections, or whole folder trees.
- Lists AU, VST2, VST3, and unknown third-party plug-ins by track, including devices inside racks.
- Keeps local scan-history snapshots with each Set's plug-ins and referenced audio files.
- Warns when a Set changed, moved, or cannot be verified since its latest saved scan.
- Classifies audio as project-local, external and collectable, or missing.
- Supports WAV, AIFF, AIFF-C, FLAC, OGG Vorbis, MP3, and M4A references.
- Groups duplicate references using byte-level content checks.
- Collects audio and matching `.asd` files into `Samples/Imported/`.
- Resolves filename collisions without overwriting existing project audio.
- Produces portable filenames that remain valid on Windows, macOS, and Linux.
- Creates and verifies a unique Set backup before every write.
- Uses atomic copies and atomic `.als` replacement to avoid partial files.
- Prevents two Inspector processes from changing the same Set concurrently.
- Validates every rewritten reference after collection.
- Restores the original Set and removes files created by a failed collection.
- Exports JSON and per-project Markdown audit reports.
- Safely restores a chosen backup while first preserving the current Set.

## Desktop app

Launch `collector-inspector`, or open the packaged desktop application. Then:

1. Drop one or more `.als` files or folders, or use either Browse button.
2. Filter Sets by Ready, Safe, Blocked, or Error.
3. Use the Overview and Plug-ins tabs to inspect media paths, blockers, and plug-in formats by track.
4. Search plug-ins or tracks by name and narrow the list by AU, VST2, VST3, or unknown format.
5. Open **History** to revisit saved inventories, search their plug-ins with the same
   name/track and format filters, or rescan a Set that may be stale.
6. Select Ready Sets and choose **Collect Selected**.
7. Close those Sets in Ableton Live and confirm.

Collection is sequential and stops on the first failure. The result screen shows the
backup, copied files, reference count, rollback, and copied-file cleanup state.

Scan history is stored only on the local computer and uses the original storage folder
name so previous scans stay available. On macOS it is saved at
`~/Library/Application Support/Ableton Inspector/scan-history.json`; Windows uses the
user's roaming application-data folder, and Linux uses `XDG_DATA_HOME` or
`~/.local/share`. A snapshot is marked stale when the current `.als` save timestamp or
file size differs from the values captured during that scan. The overall warning uses
the latest snapshot for each Set; older snapshots remain available for comparison.

## Install from source

Python 3.11 or newer is required.

macOS or Linux:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
collector-inspector
```

Windows PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
collector-inspector
```

## Build a native desktop application

Install the development dependencies first:

```bash
python -m pip install -e ".[dev]"
```

Build on the target operating system. PyInstaller does not cross-compile.

- macOS: `./scripts/build_macos_app.sh`
- Linux: `./scripts/build_linux_app.sh`
- Windows PowerShell: `.\scripts\build_windows_app.ps1`
- Any platform: `python scripts/build_app.py`

The result is written to `dist/Collector Inspector.app` on macOS or
`dist/Collector Inspector` on Windows and Linux. macOS builds use the architecture of
the build Python, so build once on Apple Silicon and once on Intel when both are needed.

Local builds are unsigned. Sign and notarize macOS releases and sign Windows releases
before public distribution. Linux binaries should be built on the oldest distribution
version that the release intends to support.

## Command line

The original `ableton-inspector` and `ableton-inspector-cli` commands remain available.


```bash
# Inspect and report
collector-inspector-cli inspect "/path/to/Set.als"
collector-inspector-cli scan "/path/to/Projects" --output report.json
collector-inspector-cli inspect "/path/to/Set.als" --project-report

# Review many Sets interactively
collector-inspector-cli review "/path/to/Projects"

# Plan and collect
collector-inspector-cli collect "/path/to/Set.als" --dry-run
collector-inspector-cli collect "/path/to/Set.als" --apply --include 1,3
collector-inspector-cli collect "/path/to/Set.als" --apply --all

# Preview and apply a backup restore
collector-inspector-cli restore "/path/to/Set.als" "/path/to/Backup.als"
collector-inspector-cli restore "/path/to/Set.als" "/path/to/Backup.als" --apply
```

`collect` defaults to a dry run. Applying requires `--all` or an explicit
`--include` list. `restore` also defaults to a dry run and requires `--apply`.

## Safety model

- Inspection does not modify a Set. Reports are written only when requested.
- Existing destination audio is reused only when its bytes match the source.
- Different files with the same name receive deterministic numbered names.
- The active Set is never rewritten in place; a validated temporary file replaces it.
- A verified backup is created before audio copying or Set rewriting starts.
- Post-write validation checks every selected reference and every copied audio file.
- If any step fails, the Set is restored atomically and newly copied files are removed.
- Backup Sets are excluded from recursive scans.

Always close affected Sets in Ableton Live before collecting or restoring. Ableton Live
could otherwise save its in-memory copy over the Inspector's changes.

## Development and release checks

```bash
python -m pip install -e ".[dev]"
ruff check .
ruff format --check .
python -m pytest
python -m build
python scripts/build_app.py
```

The CI workflow runs lint, tests, wheel builds, and wheel smoke tests on Python 3.11
and 3.13 across macOS, Windows, and Linux.

## Related project

[Ableton Inspector by Owen Bush](https://github.com/owenbush/ableton-inspector)
extracts musical metadata such as tempo, key, and arrangement markers from Live Sets.

Collector Inspector is an independent utility for Ableton Live. Ableton and Live are
trademarks of Ableton AG.

## License

Collector Inspector is distributed under the [MIT License](LICENSE). The code and
icon artwork were developed with AI assistance. Dependencies are installed separately
and retain their own licenses, including [Qt for Python](https://doc.qt.io/qtforpython-6/overviews/qtdoc-lgpl.html),
[Textual](https://github.com/Textualize/textual), and [Typer](https://github.com/fastapi/typer/blob/master/LICENSE).
