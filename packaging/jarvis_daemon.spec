from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs


project_root = Path(SPEC).resolve().parents[1]
source_root = project_root / "src"
sqlite_datas, sqlite_binaries, sqlite_hidden = collect_all("sqlite_vec")
mlx_binaries = collect_dynamic_libs("mlx")
libomp = (
    Path(sys.prefix)
    / "lib"
    / f"python{sys.version_info.major}.{sys.version_info.minor}"
    / "site-packages/torch/lib/libomp.dylib"
)
openmp_binaries = [(str(libomp), ".")] if libomp.is_file() else []

analysis = Analysis(
    [str(source_root / "aegis_core/runtime/frozen_entrypoint.py")],
    pathex=[str(source_root)],
    binaries=[*sqlite_binaries, *mlx_binaries, *openmp_binaries],
    datas=sqlite_datas,
    hiddenimports=sqlite_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "IPython",
        "PIL",
        "matplotlib",
        "pytest",
        "setuptools",
        "torch",
        "torchgen",
    ],
    noarchive=False,
    optimize=1,
)

python_archive = PYZ(analysis.pure)

executable = EXE(
    python_archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="jarvis-daemon",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    target_arch="arm64",
    codesign_identity=None,
    entitlements_file=None,
)

bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="jarvis-daemon",
)
