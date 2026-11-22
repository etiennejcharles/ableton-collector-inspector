from pathlib import Path
import runpy
import sys


PROJECT_ROOT = Path(SPECPATH).parent
SOURCE_ROOT = PROJECT_ROOT / "src"
ASSET_ROOT = SOURCE_ROOT / "ableton_audio_collector" / "assets"
VERSION = runpy.run_path(SOURCE_ROOT / "ableton_audio_collector" / "__init__.py")["__version__"]
IS_MACOS = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"
ICON_PATH = (
    ASSET_ROOT / "app_icon.icns"
    if IS_MACOS
    else ASSET_ROOT / "app_icon.ico"
    if IS_WINDOWS
    else None
)

analysis = Analysis(
    [str(SOURCE_ROOT / "ableton_audio_collector" / "gui.py")],
    pathex=[str(SOURCE_ROOT)],
    binaries=[],
    datas=[(str(ASSET_ROOT), "assets")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["textual"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Collector Inspector",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    target_arch=None,
    icon=str(ICON_PATH) if ICON_PATH else None,
)
collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="Collector Inspector",
)
if IS_MACOS:
    app = BUNDLE(
        collection,
        name="Collector Inspector.app",
        icon=str(ICON_PATH),
        bundle_identifier="com.etienne.ableton-inspector",
        info_plist={
            "CFBundleDisplayName": "Collector Inspector",
            "CFBundleName": "Collector Inspector",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "CFBundleDocumentTypes": [
                {
                    "CFBundleTypeExtensions": ["als"],
                    "CFBundleTypeName": "Ableton Live Set",
                    "CFBundleTypeRole": "Viewer",
                }
            ],
            "LSMinimumSystemVersion": "12.0",
            "NSHighResolutionCapable": True,
            "NSPrincipalClass": "NSApplication",
        },
    )
