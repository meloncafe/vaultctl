# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for vaultctl."""

import sys
import os
from pathlib import Path

block_cipher = None

# 소스 경로
src_path = Path("src")
templates_path = src_path / "vaultctl" / "templates"

# Hardcoded list - collect_submodules doesn't work reliably in CI
# CI에서 collect_submodules가 제대로 작동하지 않으므로 하드코딩
VAULTCTL_HIDDENIMPORTS = [
    # Core modules
    "vaultctl",
    "vaultctl.__main__",
    "vaultctl.cli",
    "vaultctl.config",
    "vaultctl.utils",
    "vaultctl.vault_client",
    # Command modules - CRITICAL
    "vaultctl.commands",
    "vaultctl.commands.admin",
    "vaultctl.commands.compose", 
    "vaultctl.commands.extended",
    "vaultctl.commands.repo",
    "vaultctl.commands.setup",
]

# Collect ALL .py files as data to ensure they're included
# 모든 .py 파일을 데이터로 포함하여 확실히 번들링
vaultctl_datas = []
vaultctl_src = src_path / "vaultctl"
for py_file in vaultctl_src.rglob("*.py"):
    if "__pycache__" not in str(py_file):
        rel_dir = py_file.parent.relative_to(src_path)
        vaultctl_datas.append((str(py_file), str(rel_dir)))

print(f"Including vaultctl source files: {[d[0] for d in vaultctl_datas]}")

a = Analysis(
    [str(src_path / "vaultctl" / "__main__.py")],
    pathex=[str(src_path)],
    binaries=[],
    datas=[
        # Include Jinja2 templates
        (str(templates_path), "vaultctl/templates"),
    ] + vaultctl_datas,
    hiddenimports=VAULTCTL_HIDDENIMPORTS + [
        # External dependencies
        "typer",
        "rich",
        "httpx",
        "pydantic",
        "pydantic_settings",
        "dotenv",
        "keyring",
        "jinja2",
        "ruamel.yaml",
        # typer related
        "typer.core",
        "typer.main",
        "click",
        "click.core",
        # rich related
        "rich.console",
        "rich.table",
        "rich.panel",
        "rich.progress",
        "rich.prompt",
        # pydantic related
        "pydantic.fields",
        "pydantic_settings.sources",
        # jinja2 related
        "jinja2.ext",
        "jinja2.loaders",
        "markupsafe",
        # ruamel.yaml related
        "ruamel.yaml.main",
        "ruamel.yaml.comments",
        "ruamel.yaml.clib",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "numpy",
        "pandas",
        "PIL",
        "cv2",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="vaultctl",
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
